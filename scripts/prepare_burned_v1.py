"""Extract the supplied two-row burned sheets without resampling/repainting.

Development only: requires Pillow. Arguments: sleeping PNG, talking PNG.
"""
import hashlib
import json
import math
import sys
from pathlib import Path
from PIL import Image


def runs(length, occupied):
    result, start = [], None
    for index in range(length + 1):
        hit = index < length and occupied(index)
        if hit and start is None:
            start = index
        if not hit and start is not None:
            result.append((start, index))
            start = None
    return result


def prepare(source, pose, count, frame_ms):
    output = Path(__file__).resolve().parents[1] / 'macromellow/static/assets/mellow'
    image = Image.open(source).convert('RGBA')
    alpha = image.getchannel('A')
    mask = alpha.point(lambda v: 255 if v > 32 else 0)
    rows = runs(image.height, lambda y: mask.crop((0, y, image.width, y + 1)).getbbox())
    boxes, anchors = [], []
    for top, bottom in rows:
        columns = runs(image.width, lambda x: mask.crop((x, top, x + 1, bottom)).getbbox())
        for left, right in columns:
            bounds = mask.crop((left, top, right, bottom)).getbbox()
            if right - left < 50 or bounds[3] - bounds[1] < 50:
                raise ValueError('Unexpected small component in source sheet')
            box = (left - 3, top + bounds[1] - 3, right + 3, top + bounds[3] + 3)
            opaque = alpha.crop(box).point(lambda v: 255 if v > 128 else 0).getbbox()
            boxes.append(box)
            anchors.append((box[0] + opaque[0], box[1] + opaque[3]))
    if len(boxes) != count:
        raise ValueError(f'Expected {count} poses; found {len(boxes)}')
    baseline = max(ground - box[1] for box, (_, ground) in zip(boxes, anchors)) + 4
    width = math.ceil((max(box[2] - box[0] for box in boxes) + 12) / 4) * 4
    height = math.ceil((baseline + max(box[3] - ground for box, (_, ground) in zip(boxes, anchors)) + 4) / 4) * 4
    frames, records = [], []
    for index, (box, (rear, ground)) in enumerate(zip(boxes, anchors)):
        position = (8 - (rear - box[0]), baseline - (ground - box[1]))
        frame = Image.new('RGBA', (width, height))
        frame.paste(image.crop(box), position)
        # Assert the complete crop fits, including non-opaque outline pixels.
        assert min(position) >= 0
        assert position[0] + box[2] - box[0] <= width and position[1] + box[3] - box[1] <= height
        frame.save(output / f'burned-{pose}-{index:02d}.png')
        frames.append(frame)
        records.append({'source_box': box, 'paste_at': position, 'source_baseline': ground})
    atlas = Image.new('RGBA', (width * count, height))
    for index, frame in enumerate(frames):
        atlas.paste(frame, (index * width, 0))
    atlas.save(output / f'burned-{pose}-atlas.png')
    frames[0].save(output / f'burned-{pose}-preview.gif', save_all=True, append_images=frames[1:],
                   duration=frame_ms, loop=0, disposal=2, optimize=False)
    metadata = {'source_sha256': hashlib.sha256(Path(source).read_bytes()).hexdigest(),
                'source_size': image.size, 'frame_size': [width, height], 'frame_count': count,
                'frame_ms': frame_ms, 'cycle_ms': count * frame_ms, 'rear_edge': 8,
                'baseline': baseline, 'frames': records}
    (output / f'burned-{pose}.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(pose, json.dumps({key: value for key, value in metadata.items() if key != 'frames'}))
    for target, ground in [(70, 65.5), (64, 59.3)]:
        # Scale based on complete body width, not padded canvas width.
        body = max(frame.getchannel('A').point(lambda v: 255 if v > 128 else 0).getbbox()[2] - 8 for frame in frames)
        scale = target / body
        print('CSS', target, 'width', width * scale, 'height', height * scale,
              'top', ground - baseline * scale, 'left', 2.8 - 8 * scale)


if __name__ == '__main__':
    prepare(Path(sys.argv[1]), 'sleep', 10, 250)
    prepare(Path(sys.argv[2]), 'talk', 7, 170)
