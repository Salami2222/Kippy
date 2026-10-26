# Enchanted study artwork

The PNGs are local decorative assets, created with the built-in imagegen tool from the two user-provided design references on October 26, 2026. They contain no functional controls. No external artwork hosts or runtime image-generation calls are used.

- `study-room.png`: 1536 × 1024 environmental scenery; bookshelves, candlelight, ivy, flowers, lanterns and a moonlit castle. The empty navy center is covered by independent HTML panels. The green turtle from the initial output was removed in a targeted edit; no animal remains in this scenery.
- `turtle.png`: transparent 1624 × 968 red-and-gold turtle sprite, derived from the small character reference. It appears once, in the sidebar, without animation. Its alpha is retained.
- `gold-frame.svg`: original reusable nine-slice beveled gold frame.
- `parchment.svg`: original low-opacity grain over warm parchment, tiled without stretching artwork.
- `compass.svg`, `flask.svg`, `book.svg`, `wizard-hat.svg`: original small code-native decorative icons. Course icons cycle visually and do not claim to classify academic subjects.

Both PNGs are stored directly as generated, retaining transparency and crisp pixel detail. CSS preserves their aspect ratios; the scenery is cropped by `cover` as viewport proportions change. Mobile scenery is confined mainly to the course drawer. The result adapts the reference to a functional full-height interface; it is not a pixel-identical screenshot reproduction.

## Generation prompts

Mode: built-in imagegen, reference-guided generation for scenery; background extraction/recreation for the sprite. Final files are this directory's `study-room.png` and `turtle.png`.

### Study room

“Use case: stylized-concept. Asset type: reusable pixel-art environmental backdrop for a real HTML study application, landscape 1536x1024. Reference image defines exact enchanted library art style and atmosphere. Recreate ONLY environmental scenery: highly detailed crisp 2D pixel art wooden bookshelves, books, candle clusters, hanging brass lanterns, ivy and little white flowers, navy moonlit castle through tall arched window at far right with glowing crescent moon. Dense narrow wooden top border, left quarter bookshelves with lower-left resting cushion/platform, right edge window/castle, narrow bottom books/candles. Center and upper-left are simple dark midnight navy empty negative space, for HTML panels to cover. Center 70% from x25% to95%, y10% to95% should remain empty dark navy unobtrusive background. Preserve reference's warm candlelit gold, wood brown, midnight blue, greenery. No turtle, no people, NO letters, NO text, NO interface controls, NO chat bubbles, NO fake buttons, NO parchment central rectangle, no UI screenshot. This is scenery only, intended behind functional HTML components. Keep artwork richly detailed at perimeter and left lower quarter, like reference.”

Targeted cleanup: “Use case: precise-object-edit. Change ONLY ONE thing: completely REMOVE the green sleeping turtle in the lower-left corner on the red cushion. Leave the empty red cushion and books there, reconstructing cushion/wood where turtle was. No turtle or other animal anywhere. Preserve every other pixel-art study-room detail, blank midnight center, layout, colors, moonlit window, foliage, candles, shelves, dimensions and lighting unchanged. This is scenery background that must contain zero animals.”

### Turtle

“Use case: background-extraction. Asset type: single transparent pixel-art turtle sprite for enchanted library sidebar. Extract/recreate ONLY the exact friendly golden-yellow turtle in this reference, full body with short rounded golden limbs, bright crimson red shell with golden segmented outlines, dark pixel outlines, friendly black rounded eye, facing left. Preserve its original appearance and small chunky pixel-art proportions, no costume, no logos, no university marks. Transparent background, no books, no table, no library, no rectangular background or shadow rectangle, no extra characters, no text. One complete turtle with all limbs/shell visible. Clean crisp pixel-art silhouette. No animation.”
