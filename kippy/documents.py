"""Bounded local extraction. Artwork and scanned text are never inferred."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from .storage import identifier, now

SUPPORTED = {'.pdf', '.pptx', '.docx', '.txt'}
MAX_TEXT = 2_000_000
MAX_ZIP_BYTES = 200_000_000
WARNING = 'Text extraction only: images, diagrams, handwriting, and scanned text are not read. Check the original material.'


def safe_name(name):
    # Both separators matter even on POSIX; the disk path is always a generated ID.
    name = re.split(r'[/\\]', name)[-1]
    name = re.sub(r'[\x00-\x1f\x7f]', '', name).strip(' .')
    if not name or len(name) > 180:
        raise ValueError('Use a file name between 1 and 180 characters.')
    return name


def extract(path, extension):
    segments = []
    if extension == '.pdf':
        from pypdf import PdfReader
        if not path.read_bytes()[:5] == b'%PDF-':
            raise ValueError('This file is not a PDF.')
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError('Password-protected PDFs are not supported.')
        if len(reader.pages) > 1000:
            raise ValueError('PDFs are limited to 1,000 pages.')
        for i, page in enumerate(reader.pages):
            segments.append((f'page {i+1}', page.extract_text() or ''))
    elif extension in {'.pptx', '.docx'}:
        with zipfile.ZipFile(path) as archive:
            files = archive.infolist()
            if len(files) > 10000 or sum(f.file_size for f in files) > MAX_ZIP_BYTES:
                raise ValueError('The expanded document exceeds the safe extraction limit.')
            required = 'ppt/presentation.xml' if extension == '.pptx' else 'word/document.xml'
            if required not in archive.namelist():
                raise ValueError('The file contents do not match its extension.')
        if extension == '.pptx':
            from pptx import Presentation
            slides = Presentation(path).slides
            if len(slides) > 1000:
                raise ValueError('Presentations are limited to 1,000 slides.')
            def texts(shapes):
                for shape in shapes:
                    if hasattr(shape, 'shapes'):
                        yield from texts(shape.shapes)
                    if shape.has_text_frame:
                        yield shape.text_frame.text
                    if shape.has_table:
                        for row in shape.table.rows:
                            yield ' | '.join(cell.text for cell in row.cells)
            for i, slide in enumerate(slides):
                segments.append((f'slide {i+1}', '\n'.join(texts(slide.shapes))))
        else:
            from docx import Document
            from docx.table import Table
            doc = Document(path)
            # Preserve body order including tables. Word pagination is not exposed.
            for i, block in enumerate(doc.iter_inner_content()):
                text = '\n'.join(' | '.join(c.text for c in r.cells) for r in block.rows) if isinstance(block, Table) else block.text
                segments.append((f'section {i+1}', text))
    else:
        raw = path.read_bytes()
        if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
            text = raw.decode('utf-16')
        else:
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError as exc:
                raise ValueError('TXT must use UTF-8 or BOM-marked UTF-16. Please export as UTF-8.') from exc
        if '\x00' in text:
            raise ValueError('This TXT file appears to contain binary data.')
        lines = text.splitlines()
        segments = [(f'lines {i+1}–{min(i+30,len(lines))}', '\n'.join(lines[i:i+30])) for i in range(0,len(lines),30)]
    if sum(len(text) for _, text in segments) > MAX_TEXT:
        raise ValueError('Extracted text exceeds 2 million characters; split the material into smaller files.')
    if not any(text.strip() for _, text in segments):
        raise ValueError('No readable text found. Scanned/image-only files need OCR before upload.')
    chunks = []
    # Aggregate short segments while retaining source location boundaries.
    for location, text in segments:
        text = text.strip()
        for start in range(0, len(text), 1600):
            chunks.append((location, text[start:start+1800]))
    if len(chunks) > 5000:
        raise ValueError('Too many text sections; split this material into smaller files.')
    return chunks, WARNING + (' Some pages/slides contain no extractable text.' if any(not t.strip() for _, t in segments) else '')


def upload(store, filename, content, max_bytes):
    name = safe_name(filename)
    extension = Path(name).suffix.lower()
    if extension not in SUPPORTED:
        raise ValueError('Supported formats: PDF, PPTX, DOCX, and TXT.')
    if not content or len(content) > max_bytes:
        raise ValueError('The file is empty or exceeds the upload size limit.')
    key = identifier()
    with store.connect() as db:
        db.execute('INSERT INTO materials (id,name,type,size,status,created_at) VALUES (?,?,?,?,?,?)',
                   (key, name, extension[1:], len(content), 'processing', now()))
    path = store.uploads / key
    try:
        path.write_bytes(content)
        chunks, warning = extract(path, extension)
        with store.connect() as db:
            db.executemany('INSERT INTO chunks (material_id,ordinal,location,text) VALUES (?,?,?,?)',
                           [(key, i, loc, text) for i, (loc, text) in enumerate(chunks)])
            db.execute("UPDATE materials SET status='ready',warning=? WHERE id=?", (warning, key))
    except Exception as error:
        # Parsing diagnostics can contain document content/paths; never expose those.
        # Known validation errors are surfaced by the helper below.
        message = str(error) if isinstance(error, ValueError) else 'Could not extract this file. It may be malformed or unsupported.'
        with store.connect() as db:
            db.execute("UPDATE materials SET status='failed',error=? WHERE id=?", (message[:400], key))
    return store.material(key)
