"""Extract the supplied seven-pose talking sheet without repainting/resampling.

Development only: requires Pillow. Pass the original PNG path.
"""
import hashlib
import json
import sys
from pathlib import Path
from PIL import Image


def prepare(source):
    output = Path(__file__).resolve().parents[1] / 'macromellow/static/assets/mellow'
    image = Image.open(source).convert('RGBA')
    alpha = image.getchannel('A')
    mask = alpha.point(lambda v: 255 if v > 32 else 0)
    runs, start = [], None
    for x in range(image.width + 1):
        occupied = x < image.width and mask.crop((x, 0, x + 1, image.height)).getbbox()
        if occupied and start is None:
            start = x
        if not occupied and start is not None:
            runs.append((start, x))
            start = None
    if len(runs) != 7:
        raise ValueError(f'Expected seven poses; found {len(runs)}')
    cuts = [0] + [(runs[n - 1][1] + runs[n][0]) // 2 for n in range(1, 7)] + [image.width]
    frames, records = [], []
    for index, (left, right) in enumerate(runs):
        opaque = alpha.crop((left, 0, right, image.height)).point(lambda v: 255 if v > 128 else 0).getbbox()
        _, top, _, bottom = mask.crop((left, 0, right, image.height)).getbbox()
        box = (max(cuts[index], left - 2), top - 2, min(cuts[index + 1], right + 2), bottom + 2)
        rear, ground = left + opaque[0], opaque[3]
        position = (6 - (rear - box[0]), 224 - (ground - box[1]))
        if min(position) < 0 or position[0] + box[2] - box[0] > 324 or position[1] + box[3] - box[1] > 228:
            raise ValueError('Pose exceeds the fixed normalization canvas')
        frame = Image.new('RGBA', (324, 228))
        frame.paste(image.crop(box), position)
        frame.save(output / f'talk_{index:02d}.png')
        frames.append(frame)
        records.append({'source_box': box, 'paste_at': position, 'source_baseline': ground})
    atlas = Image.new('RGBA', (324 * 7, 228))
    for index, frame in enumerate(frames):
        atlas.paste(frame, (index * 324, 0))
    atlas.save(output / 'talking-v1-atlas.png')
    frames[0].save(output / 'talking-v1-preview.gif', save_all=True, append_images=frames[1:],
                   duration=150, loop=0, disposal=2, optimize=False)
    (output / 'talking-v1.json').write_text(json.dumps({
        'source_sha256': hashlib.sha256(Path(source).read_bytes()).hexdigest(),
        'source_size': image.size, 'frame_size': [324, 228], 'frame_count': 7,
        'frame_ms': 150, 'cycle_ms': 1050, 'rear_edge': 6, 'baseline': 224, 'frames': records,
    }, indent=2) + '\n')
    print('Prepared seven pixel-exact crops, aligned at x=6 and baseline y=224.')


if __name__ == '__main__':
    prepare(Path(sys.argv[1]))
