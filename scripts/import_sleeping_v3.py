"""Import supplied V3 PNGs byte-for-byte and generate a 250ms/frame GIF preview.

Development only: Pillow is required. Pass the supplied ZIP path.
"""
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

from PIL import Image


def prepare(archive):
    output = Path(__file__).resolve().parents[1] / 'macromellow/static/assets/mellow'
    with zipfile.ZipFile(archive) as bundle:
        # Read only named assets; never extract paths or execute archive contents.
        files = {Path(name).name: name for name in bundle.namelist()}
        frames = []
        hashes = {}
        for index in range(10):
            name = f'sleep_{index:02d}.png'
            data = bundle.read(files[name])
            frame = Image.open(io.BytesIO(data)).convert('RGBA')
            if frame.size != (576, 450):
                raise ValueError(f'Unexpected frame dimensions: {name}')
            frames.append(frame)
            hashes[name] = hashlib.sha256(data).hexdigest()
            (output / name).write_bytes(data)
        name = 'mellow_sleeping_10frame_atlas.png'
        data = bundle.read(files[name])
        atlas = Image.open(io.BytesIO(data)).convert('RGBA')
        if atlas.size != (5760, 450):
            raise ValueError('Unexpected atlas dimensions')
        for index, frame in enumerate(frames):
            if atlas.crop((index * 576, 0, (index + 1) * 576, 450)).tobytes() != frame.tobytes():
                raise ValueError(f'Atlas cell {index} differs from its source frame')
        (output / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
        # GIF is only a review artifact; production uses the unchanged transparent PNG atlas.
        frames[0].save(output / 'sleeping-v3-preview.gif', save_all=True, append_images=frames[1:],
                       duration=250, loop=0, disposal=2, optimize=False)
        (output / 'sleeping-v3.json').write_text(json.dumps({
            'source_zip_sha256': hashlib.sha256(Path(archive).read_bytes()).hexdigest(),
            'frame_size': [576, 450], 'frame_count': 10, 'frame_ms': 250,
            'cycle_ms': 2500, 'baseline': 400, 'source_sha256': hashes,
        }, indent=2) + '\n')
        print('Imported 10 original frames and matching atlas; generated 250ms/frame preview.')


if __name__ == '__main__':
    prepare(Path(sys.argv[1]))
