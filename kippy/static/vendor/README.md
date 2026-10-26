# Locally served browser dependencies

Downloaded from the official npm registry tarballs; SHA-512 package integrity was checked against registry metadata:

- `marked` 18.1.1 — `lib/marked.umd.js` → `marked.js` ([project](https://github.com/markedjs/marked))
- `dompurify` 3.4.17 — `dist/purify.min.js` → `purify.js` ([project](https://github.com/cure53/DOMPurify))
- `katex` 0.19.1 — minified runtime, auto-render helper, CSS and WOFF2 fonts ([project](https://github.com/KaTeX/KaTeX))

Licenses are included alongside these files. `package-lock.json` also records dependency integrity. There is no CDN request at runtime. To update, review upstream releases, refresh these files and licenses, and run the tests. jsdom is only a development test dependency, not shipped browser code.

## Interface lettering

Silkscreen Regular (32,220 bytes) is served locally from `silkscreen/Silkscreen-Regular.ttf`, unchanged from the [Google Fonts repository](https://github.com/google/fonts/tree/main/ofl/silkscreen). Copyright 2001 The Silkscreen Project Authors; SIL Open Font License 1.1 is included as `silkscreen/OFL.txt`. It is used only for branding and short UI labels. Long responses, filenames and mathematical explanations use the existing readable body/math fonts. No external font requests occur at runtime.
