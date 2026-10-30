"""Bounded local extraction. Artwork and scanned text are never inferred."""
from __future__ import annotations

import io
import re
import stat
import struct
import zlib
import zipfile
from pathlib import Path

from .storage import identifier, now

SUPPORTED = {'.pdf', '.pptx', '.docx', '.txt'}
UPLOAD_TYPES = SUPPORTED | {'.zip'}
MAX_ARCHIVE_FILES = 50
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1000
MAX_ARCHIVE_RATIO = 1000
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


def upload(store, filename, content, max_bytes, course_id=None):
    name = safe_name(filename)
    extension = Path(name).suffix.lower()
    if extension not in UPLOAD_TYPES:
        raise ValueError('Supported formats: PDF, PPTX, DOCX, TXT, and ZIP.')
    if not content or len(content) > max_bytes:
        raise ValueError('The file is empty or exceeds the upload size limit.')
    if course_id is not None:
        store.course(course_id)
    if extension == '.zip':
        return import_archive(store, name, content, max_bytes, course_id)
    key = identifier()
    with store.connect() as db:
        db.execute('INSERT INTO materials (id,name,type,size,status,created_at) VALUES (?,?,?,?,?,?)',
                   (key, name, extension[1:], len(content), 'processing', now()))
        if course_id is not None:
            db.execute('INSERT INTO course_materials VALUES (?,?)', (course_id, key))
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


def _archive_directory(content):
    """Bound the central directory before ZipFile constructs entry objects; no ZIP64/multidisk."""
    offset = content.rfind(b'PK\x05\x06', max(0, len(content)-65557))
    if offset < 0 or offset+22 > len(content):
        raise ValueError('This is not a valid ZIP archive.')
    _, disk, central_disk, disk_count, count, size, start, comment = struct.unpack_from('<4s4H2LH', content, offset)
    if offset+22+comment != len(content) or disk or central_disk or disk_count != count:
        raise ValueError('Malformed or multi-disk ZIP archives are not supported.')
    if count == 65535 or size == 0xffffffff or start == 0xffffffff or (offset>=20 and content[offset-20:offset-16]==b'PK\x06\x07'):
        raise ValueError('ZIP64 archives are not supported; export a standard ZIP.')
    if count > MAX_ARCHIVE_ENTRIES or size > 1024*1024 or start+size != offset:
        raise ValueError('ZIP directory exceeds the safe limit or is malformed (maximum 1,000 entries).')
    return count


def _archive_path(info):
    name = info.orig_filename
    normalized = name.replace('\\','/')
    parts = normalized.split('/')
    kind = stat.S_IFMT(info.external_attr >> 16)
    if (not name or len(name)>1024 or normalized.startswith('/') or ':' in name
            or '..' in parts or any(ord(c)<32 or ord(c)==127 for c in name)
            or kind not in (0,stat.S_IFREG,stat.S_IFDIR) or info.external_attr & 0x400):
        raise ValueError('ZIP contains an unsafe path, symlink, or special file; nothing was imported.')
    if info.flag_bits & (1 | 64):
        raise ValueError('Encrypted/password-protected ZIP archives are not supported; nothing was imported.')
    return normalized


def import_archive(store, name, content, max_bytes, course_id):
    """Preflight every entry, then read bounded bytes one document at a time. Never extract paths."""
    expected = _archive_directory(content)
    result = {'kind':'archive','name':name,'materials':[],'imported':[],'failed':[],'skipped':[]}
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) != expected:
                raise ValueError('Malformed ZIP directory; nothing was imported.')
            paths = [(info,_archive_path(info)) for info in infos]
            if sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
                raise ValueError('ZIP exceeds 100 MiB total uncompressed content; nothing was imported.')
            candidates = []
            # Check bomb/entry constraints even on ignored entries before writing anything.
            for info,path in paths:
                if info.file_size > max(1024*1024,info.compress_size*MAX_ARCHIVE_RATIO):
                    raise ValueError('ZIP compression ratio exceeds the safe extraction limit; nothing was imported.')
                if info.is_dir() or path.endswith('/'):
                    continue
                base = path.rsplit('/',1)[-1]
                if '__MACOSX' in path.split('/') or base == '.DS_Store' or base.startswith('._'):
                    continue
                if Path(base).suffix.lower() not in SUPPORTED:
                    result['skipped'].append({'name':path,'reason':'Unsupported file type (nested ZIPs are not extracted).'})
                    continue
                candidates.append((info,path))
            if len(candidates)>MAX_ARCHIVE_FILES:
                raise ValueError('ZIP contains more than 50 supported documents; split it into smaller archives. Nothing was imported.')
            expanded = 0
            for info,path in candidates:
                try:
                    filename = safe_name(path)
                    if info.file_size < 1 or info.file_size > max_bytes:
                        raise ValueError('Document is empty or exceeds the configured per-file upload limit.')
                    if info.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):
                        raise ValueError('Use ZIP stored or deflate compression for this entry.')
                    # Bound reads independently of the directory declarations, and verify CRC by reaching EOF.
                    with archive.open(info) as stream, io.BytesIO() as buffer:
                        while True:
                            chunk = stream.read(min(65536,max_bytes-buffer.tell()+1))
                            if not chunk:break
                            expanded += len(chunk)
                            if buffer.tell()+len(chunk)>max_bytes or expanded>MAX_ARCHIVE_BYTES:
                                raise ValueError('Expanded document exceeds the safe extraction limit.')
                            buffer.write(chunk)
                        if buffer.tell()!=info.file_size:
                            raise ValueError('ZIP entry size does not match its directory record.')
                        doc = upload(store,filename,buffer.getvalue(),max_bytes,course_id)
                    result['materials'].append(doc)
                    if doc['status']=='ready':result['imported'].append({'name':path,'material_id':doc['id']})
                    else:result['failed'].append({'name':path,'error':doc['error'],'material_id':doc['id']})
                except (ValueError,zipfile.BadZipFile,RuntimeError,NotImplementedError,EOFError,zlib.error) as error:
                    message = str(error) if isinstance(error,ValueError) else 'Malformed or unreadable ZIP entry (data or CRC validation failed).'
                    result['failed'].append({'name':path,'error':message})
    except (zipfile.BadZipFile,EOFError,NotImplementedError,RuntimeError,zlib.error) as error:
        raise ValueError('Malformed or unreadable ZIP archive.') from error
    result['summary']={key:len(result[key]) for key in ('imported','failed','skipped')}
    return result
