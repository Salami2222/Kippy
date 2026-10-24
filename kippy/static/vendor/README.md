# Locally served browser dependencies

Downloaded from the official npm registry tarballs; SHA-512 package integrity was checked against registry metadata:

- `marked` 18.1.1 — `lib/marked.umd.js` → `marked.js` ([project](https://github.com/markedjs/marked))
- `dompurify` 3.4.17 — `dist/purify.min.js` → `purify.js` ([project](https://github.com/cure53/DOMPurify))
- `katex` 0.19.1 — minified runtime, auto-render helper, CSS and WOFF2 fonts ([project](https://github.com/KaTeX/KaTeX))

Licenses are included alongside these files. `package-lock.json` also records dependency integrity. There is no CDN request at runtime. To update, review upstream releases, refresh these files and licenses, and run the tests. jsdom is only a development test dependency, not shipped browser code.
