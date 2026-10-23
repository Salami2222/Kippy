"""Import supplied PNGs unchanged; Pillow is needed only for validation/preview."""
import argparse
import json
from pathlib import Path
from zipfile import ZipFile
from PIL import Image
import io

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('zip', type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
target = root / 'macromellow/static/assets/mellow'
with ZipFile(args.zip) as bundle:
    metadata = json.loads(bundle.read('animation.json'))
    assert metadata['frame_count'] == 12
    assert metadata['frame_size'] == [362, 362]
    assert metadata['grid'] == {'columns': 4, 'rows': 3}
    assert sum(metadata['durations_ms']) == 1685
    atlas_data = bundle.read('happy_bounce_atlas.png')
    atlas = Image.open(io.BytesIO(atlas_data)).convert('RGBA')
    assert atlas.size == (1448, 1086)
    frames = []
    for index in range(12):
        name = f'happy_bounce_{index:02d}.png'
        content = bundle.read(name)
        frame = Image.open(io.BytesIO(content)).convert('RGBA')
        assert frame.size == (362, 362)
        x, y = index % 4 * 362, index // 4 * 362
        assert frame.tobytes() == atlas.crop((x, y, x + 362, y + 362)).tobytes()
        (target / name).write_bytes(content)
        # Cream background only for the review GIF; production PNG alpha is untouched.
        preview = Image.new('RGBA', frame.size, '#FBF7F0')
        preview.alpha_composite(frame)
        frames.append(preview.convert('RGB'))
    (target / 'happy_bounce_atlas.png').write_bytes(atlas_data)
    (target / 'happy-bounce-animation.json').write_bytes(bundle.read('animation.json'))
    frames[0].save(target / 'happy-bounce-preview.gif', save_all=True, append_images=frames[1:],
                   duration=metadata['durations_ms'], loop=0, disposal=2)
print('Imported 12 unchanged frames and matching atlas; generated review-only GIF.')
