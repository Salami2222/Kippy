"""Crop the supplied V2 sheet without resampling or repainting its RGBA pixels.

Development only: requires Pillow. Run with the path to the original sprite sheet.
"""
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image


def prepare(source, output):
    image = Image.open(source).convert('RGBA')
    alpha = image.getchannel('A')
    # Ignore near-invisible export noise when detecting silhouettes, not when copying pixels.
    silhouette = alpha.point(lambda value: 255 if value > 10 else 0)
    runs, start = [], None
    for x in range(image.width + 1):
        occupied = x < image.width and silhouette.crop((x, 0, x + 1, image.height)).getbbox()
        if occupied and start is None:
            start = x
        if not occupied and start is not None:
            runs.append((start, x))
            start = None
    if len(runs) != 4:
        raise ValueError(f'Expected four separated silhouettes; found {len(runs)}')
    boxes = []
    for left, right in runs:
        _, top, _, bottom = silhouette.crop((left, 0, right, image.height)).getbbox()
        boxes.append((left - 2, top - 2, right + 2, bottom + 2))
    # The supplied artwork's opaque feet share y=581. Keep that existing baseline.
    opaque_boxes = [alpha.crop((left, 0, right, image.height)).point(
        lambda value: 255 if value > 128 else 0).getbbox() for left, right in runs]
    baselines = [box[3] for box in opaque_boxes]
    rear_edges = [left + box[0] for (left, _), box in zip(runs, opaque_boxes)]
    width = max(right - left for left, _, right, _ in boxes) + 8
    baseline = max(base - top for base, (_, top, _, _) in zip(baselines, boxes))
    height = baseline + max(bottom - base for base, (_, _, _, bottom) in zip(baselines, boxes)) + 2
    frames, placements = [], []
    output.mkdir(parents=True, exist_ok=True)
    for index, (box, ground, rear) in enumerate(zip(boxes, baselines, rear_edges), 1):
        frame = Image.new('RGBA', (width, height))
        position = (6 - (rear - box[0]), baseline - (ground - box[1]))
        frame.paste(image.crop(box), position)  # No mask: copy alpha exactly, including edge pixels.
        frame.save(output / f'idle-v2-frame-{index}.png')
        frames.append(frame)
        placements.append({'source_box': box, 'paste_at': position, 'source_baseline': ground})
    atlas = Image.new('RGBA', (width * 4, height))
    for index, frame in enumerate(frames):
        atlas.paste(frame, (index * width, 0))
    atlas.save(output / 'idle-v2-atlas.png')
    # APNG preview uses the original pixels and timing; the runtime uses the static atlas.
    frames[0].save(output / 'idle-v2-preview.png', save_all=True, append_images=frames[1:],
                   duration=700, loop=0, disposal=0, blend=0)
    metadata = {'source_sha256': hashlib.sha256(Path(source).read_bytes()).hexdigest(),
                'source_size': image.size, 'frame_size': [width, height], 'baseline': baseline,
                'rear_edge': 6, 'cycle_ms': 2800, 'frames': placements}
    (output / 'idle-v2.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(json.dumps(metadata, indent=2))


if __name__ == '__main__':
    prepare(Path(sys.argv[1]), Path(__file__).resolve().parents[1] / 'macromellow/static/assets/mellow')
