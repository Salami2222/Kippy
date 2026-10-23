# Mellow pixel mascot assets

Eight transparent, single-pose PNGs retained for the lightweight frontend mascot controller.
The application uses the V3 atlas for normal idle, the seven-frame Talking V1 atlas for normal activity, and the Burned V1 sleeping/talking atlases.
Thinking uses the talking artwork; laptop and profile exports are retained for future use.
Each of the original eight exports is a 1254 × 1254 RGBA square. The PNGs retain their transparent padding;
render them with `image-rendering: pixelated` and a fixed square image box.

| File | Pose |
| --- | --- |
| `idle.png` | Resting, closed eyes |
| `talking.png` | Raised head, cheerful open mouth |
| `thinking.png` | Working at a gray laptop |
| `profile.png` | Smiling assistant portrait |
| `idle-burned.png` | Resting, toasted shell, tiny flame/smoke and tear |
| `talking-burned.png` | Responsive crying expression, toasted shell |
| `thinking-burned.png` | Crying while working at the laptop |
| `profile-burned.png` | Sad assistant portrait, toasted shell |

## Preparation and limitations

The user-approved **Marshmallow Turtle Pixel Sprite Sheet.png** supplied the visual
reference. Its neighboring poses overlap and it contains labels and colorful
background residue, so direct rectangular crops would include defective edges or
parts of adjacent characters. These assets are reference-guided cleanups made with
the built-in image generation/editing tool, one call for each logical asset. They
are not exact pixel-for-pixel extractions. The original supplied sheet is not
bundled in the repository.

Normal poses were isolated first. Each burned pose was then edited from its
matching normal pose with the original sheet as the burned-design reference.
The same brown terrapin, oversized off-white marshmallow shell, warm palette,
and chunky pixel shapes were retained. Generated files were copied into this
directory without resampling or alpha replacement.

These are single frames, not a multi-frame animation sheet. CSS supplies subtle
whole-sprite motion; there is no alternate mouth, blink, paw, flame, or tear frame.
Every export was visually inspected for complete feet/shell/laptop, clear padding,
labels, adjacent sprites, and clipping. Normal/burned pairs keep nearly the same
body placement, while the burned effects use the upper padding. Main pose
baselines differ by up to 45 source pixels (about 4.6 displayed pixels at 128px),
reflecting their different poses. `app.css` uses measured per-pose offsets to align
the feet at the resting pose's baseline, including the burned variants. These
poses change with activity; they are not cycled as animation frames.

Alpha inspection found faint generated edge colors at at most 11/255 opacity.
They are not opaque background fragments and are imperceptible at the intended
small display sizes on the application's cream surfaces. The transparent margins
remain clear; all visible silhouettes sit well inside the canvas. Keep the fixed
image box when switching state so the layout does not shift.

## Prompt set

All calls used a transparent background and the approved sheet as a reference.
The normal-asset prompt consisted of this shared text and one pose suffix below:

> Use case: background-extraction. Asset type: production transparent PNG mascot
> for a small web UI. Input image: approved Mellow sprite sheet, identity and style
> reference. Clean up and isolate ONLY the requested single character pose.
> Preserve the exact approved cute small brown terrapin identity, its enormous
> off-white squarish marshmallow shell with rounded corners, warm caramel body,
> dark brown pixel outlines, tiny feet and reference pixel-art aesthetic and
> proportions. Single complete character only, no neighboring characters or
> fragments, labels, text, backdrop, circles, arrows, sparkles or floating symbols.
> Keep crisp pixel clusters, not smooth vector or painterly rendering. Genuinely
> transparent background. Square canvas with generous transparent padding on all
> sides, character centered horizontally, sole baseline 84% of canvas, maximum
> character width 76%, no clipping.

Pose suffixes:

- **idle:** Requested pose: normal unburned sleeping/resting Mellow from group 1.
  Head down and eyes gently closed, tiny peaceful smile. One large white
  marshmallow shell, no char, fire, smoke or tears. Keep complete tail and feet
  visible. Do not include the red Z symbols.
- **talking:** Requested pose: normal unburned happy talking Mellow from group 2,
  three-quarter side view facing right, head raised, big friendly dark eyes and
  small open smiling mouth. One large white marshmallow shell. No char, fire,
  smoke, tears or alert marks. Keep complete feet and tail visible.
- **thinking:** Requested pose: normal unburned working/thinking Mellow from group 3,
  three-quarter side view facing right, focused friendly face with eyes open,
  tiny gray laptop open in front with paws touching keyboard. Preserve shell and
  body proportions, complete laptop and feet. One large white marshmallow shell,
  no char, fire, smoke, tears, symbols or laptop logos.
- **profile:** Requested pose: normal smiling profile portrait Mellow from group 4.
  Three-quarter portrait facing right, big friendly dark eyes and small open
  smiling mouth, caramel head and paws emerging from enormous white squarish
  marshmallow shell. Character only, remove circular icon frame. Center complete
  shell and visible body with padding, no clipping. No fire, smoke, tears or
  alert marks.

Burned-asset prompt (image 1: matching normal export; image 2: original sheet):

> Use case: precise-object-edit. Asset type: transparent pixel-art mascot PNG.
> Image 1 is the exact clean normal Mellow asset to edit. Image 2 is the approved
> original sheet showing the burned design reference. Change ONLY the normal
> asset into its burned marshmallow variant: preserve exact character geometry,
> pose, size, position, outline, warm caramel terrapin body and giant squarish
> rounded marshmallow shell, pixel cluster style and transparent square canvas.
> Add dark toasted/charred brown patches only across the upper shell while
> retaining off-white lower shell. Add ONE tiny orange flame on shell and short
> subtle gray smoke wisp, fully inside existing transparent canvas padding. Keep
> these effects understated, not dramatic.

This was followed by one pose instruction:

- **idle-burned:** Keep exact sleeping pose, closed eyes and relaxed expression,
  add one small blue tear below a closed eye.
- **talking-burned:** Keep exact upright talking pose. Change face to responsive
  sad small open mouth, bright blue tears below eyes, retaining cute friendly
  identity.
- **thinking-burned:** Keep exact laptop pose and gray laptop. Add small blue tears
  under eyes with focused sad expression. Do not remove laptop or alter complete
  body.
- **profile-burned:** Keep same profile portrait composition. Change to sad, crying
  facial expression with small open mouth and blue tears, same head and eyes.

Every burned prompt ended with:

> No labels, no adjacent sprites, no text, no backdrop, no circle, no fragments,
> no alert symbols, no extra props. Do not zoom or rescale the character. Genuinely
> transparent background. Crisp pixel art, no smooth vector rendering. Keep
> silhouette away from canvas edges.

## Sleeping Mellow V2 (archived supplied artwork, no generation)

`idle-v2-frame-1.png` through `idle-v2-frame-4.png` are exact RGBA crops from the user-supplied `sleeping_turtle_breathing_sprite_sheet.png` (2172×724). Uneven horizontal runs were detected using alpha > 10 solely to find the silhouettes; copying retains the original alpha/color pixels. Each crop includes a 2px safety margin. The original feet share an opaque baseline of y=581; the four 535×377 normalized canvases preserve that baseline at y=372, with a fixed rear-foot edge. No resampling, recoloring, alpha replacement, or generated artwork was used.

`idle-v2-atlas.png` arranges the four frames left to right (2140×377). `idle-v2-preview.png` is an APNG preview with 700ms per frame. `idle-v2.json` records the source SHA-256, crop boxes, placements, and timing. `scripts/prepare_sleeping_mellow.py` reproduces these assets using Pillow (development only).

The V2 implementation animated atlas background positions with CSS `steps(4)` over 2.8 seconds, removing the atlas outside normal idle so returning to idle starts at frame one. No transform/scale is applied to this loop. Until the atlas loads, the first-frame PNG remains visible. Reduced motion fixes the atlas at frame one. Original burned artwork and other exported poses remain unchanged.

## Sleeping Mellow V3 (current)

Imported directly from the supplied `mellow_sleeping_10frame_v3.zip`. `sleep_00.png` through `sleep_09.png` and `mellow_sleeping_10frame_atlas.png` are byte-for-byte copies. All ten source cells are 576×450; the atlas is 5760×450. Solid character bounds have x=28..547 and baseline y=400 throughout. No crops, recoloring, resampling, or generated replacement artwork were applied. SHA-256 hashes are recorded in `sleeping-v3.json`.

The app preloads the atlas and uses `steps(10,end)` for a 2.5s cycle (250ms per frame), without transform animation. The first PNG is a loading fallback and reduced motion shows the first atlas cell. `sleeping-v3-preview.gif` is generated from the ten supplied frames at the same timing; GIF palette reduction affects only this review artifact, not the production PNGs. `scripts/import_sleeping_v3.py` checks that each atlas cell matches its corresponding PNG and regenerates this preview. Original V2 and other pose assets remain available.

The normal V3 bundle itself contained no burned frames. The separately supplied Burned V1 sheets below now supply those animations.

## Talking Mellow V1 (seven supplied poses)

The user confirmed the supplied `Mellow Turtle Talking Sprite Sheet.png` contains seven poses. `talk_00.png`–`talk_06.png` are original RGBA crops on 324×228 transparent canvases, with opaque rear edges at x=6 and feet at y=224. Crop rectangles respect the unequal pose spacing and adjacent characters. The last pose is shifted up by two source pixels to align its feet. Pixel colors/alpha within the crops are copied exactly, without resampling, interpolation, repainting, or replacement artwork.

`talking-v1-atlas.png` places those cells left-to-right (2268×228); `talking-v1-preview.gif` loops at 150ms/frame, matching the 1.05-second CSS `steps(7,end)` cycle. `talking-v1.json` records source SHA-256, crop rectangles, placements, and timing. `scripts/prepare_talking_v1.py` reproduces the assets using Pillow (development only).

Thinking and responding use the same animation properties so the loop continues across the response transition. Each atlas has an independent preload/fallback. Normal talking has no extra transform animation; reduced motion shows cell zero. Sleeping V3 remains unchanged, and original single-pose exports remain archived. Crops and actual Qwen-backed website playback were visually checked at desktop/mobile sizes; no noticeable mouth/outline interpolation artifacts were observed.

## Burned Mellow V1 (current)

Source mapping:

- Sleeping: `ChatGPT Image Oct 22, 2026, 09_28_11 PM-1.png` (1983×793), 10 poses in two rows of five.
- Talking: `ChatGPT Image Oct 22, 2026, 09_28_12 PM-2.png` (1774×887), seven poses in rows of four and three.

Frames are ordered left-to-right, top-to-bottom. `scripts/prepare_burned_v1.py` finds row/column alpha silhouettes (>32 for detection only), includes a 3px safety margin, copies the original RGBA pixels unchanged, and aligns the opaque rear edge/ground baseline (>128 for anchors only). No source alpha/colors are replaced, resampling applied, or new art generated. `burned-sleep-00.png`–`09.png` use 388×360 canvases with baseline y=351; `burned-talk-00.png`–`06.png` use 424×380 with baseline y=369. Both rear edges align at x=8. Full flame/smoke/feet/tears remain inside the canvases.

`burned-sleep-atlas.png` (3880×360) uses 250ms/frame (2.5s). `burned-talk-atlas.png` (2968×380) uses 170ms/frame (1.19s). Matching GIF previews use the same timings; GIF palette reduction affects only the previews. JSON metadata records source SHA-256, crop boxes, placements, dimensions, baseline, and timing. All extracted crop pixels and anchors were checked against the original sheets.

The existing presentation controller and goal logic select the appropriate atlas. Four independent preload/fallback flags prevent normal artwork being used for a burned state. CSS `steps(10)` / `steps(7)` supplies the frame motion only. The old static burned bounce, shimmer, and CSS smoke effects are removed. Reduced motion holds the first cell. The fixed header slot and per-atlas geometry keep the baseline stable and preserve complete smoke/flame shapes on desktop and narrow mobile screens. Normal atlas pixels and timings are unchanged.
