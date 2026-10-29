# Kippy conversion verification — October 24, 2026

## Implemented

- Responsive course-based swamp-fantasy chat UI with a collapsible conversation/library sidebar, persistent history, New Chat, conversation switching/deletion, multiline composer, copy, loading/error states, sanitized Markdown, code blocks and locally rendered LaTeX.
- Real PDF/PPTX/DOCX/TXT upload/extraction, persistent random-ID files, SQLite metadata/chunks, per-conversation selections, processing/errors, removal and drag/drop.
- Qwen3.5:9b through the Python Ollama client, bounded recent history, lexical retrieval, explicit page/slide/section/line requests, and section-by-section whole-document review with explicit coverage limits.
- Existing loopback/Tailscale access model and deployment/Ollama supervision retained. New release checks and the guarded first-migration procedure are deployed; see the live verification below.

## Removed

The entire tracked `macromellow/` application was deleted: nutrition agent/planner, UMD retrieval/scraping, food search, tracker/goals, meal/nutrition memory schemas, frontend dashboard, every mascot image/sprite/controller, and their tests/fixtures/import scripts. No hidden legacy endpoints or feature flags remain. The three ignored local legacy database/menu files and remaining old package cache were deleted after explicit user authorization, without backups. Windows data was not deleted.

Remaining old names appear in operational host bindings/migration instructions and regression tests that prove removed routes return 404. KaTeX's mathematical “macros” are unrelated to nutrition. The GitHub repository About description/topics and local origin URL now identify Kippy. No new repository, archive, or pull request was created.

## Major files

Added:

- `kippy/web.py`: loopback HTTP API, write/origin/Host checks, upload routes, release/status endpoints.
- `kippy/storage.py`: independent SQLite schema, history, selections, file metadata, idempotent turns and interrupted-request recovery.
- `kippy/documents.py`: bounded extraction and safe file lifecycle.
- `kippy/study.py`: retrieval, source metadata, academic prompt and full-coverage batch summarization.
- `kippy/ollama.py`, `kippy/__main__.py`: local model transport and CLI.
- `kippy/static/index.html`, `app.css`, `app.js`, `vendor/`: chat/library client, local Markdown/sanitization/math libraries, fonts and licenses.
- `requirements.txt`, `pyproject.toml`, `package.json`, `package-lock.json`, `.env.example`: package metadata and reproducible dependencies/configuration.
- `tests/test_kippy.py`, `tests/test_frontend.cjs`, `tests/Test-KippyMigration.ps1`: application and migration regression coverage.
- `scripts/Prepare-KippyMigration.ps1`, `scripts/kippy-update`, `docs/windows-migration.md`.

Modified: README, ignore rules, release/dependency gates in the existing Windows updater, deployment regression tests, and remote-deployment documentation. Deleted: the old application tree, its nutrition tests/fixtures and sprite-processing scripts, and the discontinued Mac helper name.

## Actual test results

| Check | Result |
| --- | --- |
| Python application suite, Mac Python 3.14 | **39 passed, 0 failed** |
| Frontend suite, Mac Node 26 | **16 passed, 0 failed** |
| Same Python suite in disposable Windows Python 3.13 environment | **39 passed, 0 failed** |
| Frontend suite, Windows Node 24 (staged deployment) | **16 passed, 0 failed** |
| Existing isolated Windows deployment tests | **25 passed, 0 failed** |
| Isolated Windows Ollama monitor tests | **8 passed, 0 failed** |
| New isolated Windows migration tests | **5 passed, 0 failed** |
| Python compile check, JS syntax check, pip dependency check | Passed |
| Python wheel build and included UI/math-font inspection | Passed |
| Frontend dependency audit during installation | 0 reported vulnerabilities at installation time |

The Windows tests used temporary source/test environments, mocked task/network operations where appropriate, and separate dependency installation. They did not modify the serving checkout, its venv, user data, credentials or scheduled tasks. An initial oversized SSH test transfer timed out before creating a test directory; a compressed test transfer then completed successfully. Temporary Windows test files were removed.

## Actual model and browser checks

A local Kippy server connected through an ephemeral SSH tunnel to the **existing Windows Ollama/Qwen3.5:9b**, without changing its configuration. Five real requests succeeded:

1. Summarized an uploaded first-order ODE lecture and cited its TXT line range.
2. Correctly handled the follow-up changing the initial condition from `y(0)=1` to `y(0)=5`, producing `y=3+2e^(-2x)`.
3. Explained actual extracted **PPTX slide 15**, citing `Linear-ODEs.pptx, slide 15`.
4. Returned the actual text from **PDF page 2**, citing `Review-notes.pdf, page 2`.
5. Answered a general eigenvalue question with **no selected materials**.

The actual website showed rendered math, real stored conversations, successful file upload and selected-material chips. Refresh and a local server restart preserved messages/uploads/selections. Visual review covered 1280×720 desktop, 390×844 and 320×700 mobile, plus a 390×420 keyboard-height viewport. An observed sidebar horizontal overflow was fixed and rechecked. Mobile drawer controls, internal scrolling and composer visibility were checked. No browser console errors appeared in the final review. Browser automation required a session reset during testing; it recovered, and final checks completed. Physical iOS/Android keyboard behavior is not claimed as tested.

## Limits and deployment status

No OCR, diagram/image understanding, Word equation-object extraction, exact PDF layout reconstruction, or streaming is implemented. Reviews are bounded to 24 source batches; longer requests explicitly require smaller selections. Retrieval is lexical and recent history is bounded. Model claims/citations can still be wrong: the context list records actual supplied excerpts, not proof of every generated statement. This remains a private single-user application without accounts.

**Windows migration completed on October 24, 2026.** The existing URL now serves Kippy. The prior push hold described during development is resolved. Four existing operational Web/Deploy/Ollama/Monitor task names remain as installation bindings; the obsolete nutrition backup task and legacy application data/components were removed. No second application, model service, hostname or repository was created.


## Deepwater theme verification — October 24, 2026

Changed only `kippy/static/app.css`, `index.html`, `app.js`, frontend tests and documentation. Navy/cyan/orange design tokens cover the sidebar, messages, composer, library, drag overlay, errors and connection status. Small file-type badges and selected/processing/failed styles use actual existing document state. Three welcome suggestions populate the composer without sending or creating a conversation. Backend, model configuration and deployment scripts are unchanged.

- Mac: **39 Python tests and 16 frontend tests pass**, including new suggestion/real-status rendering coverage; JavaScript syntax check passes.
- Production packaging: Python wheel builds successfully with the static frontend. This vanilla HTML/CSS/JS app has no separate frontend compilation step.
- Browser: reviewed 1280×720 desktop, 834×1112 tablet, 390×844 and 320×568 mobile, and a 390×420 reduced-height viewport. No page horizontal overflow; conversation scrolls internally and composer remains visible. Mobile sidebar opens/closes; desktop collapse and conversation refresh work. A physical phone keyboard was not tested.
- Uploaded a real TXT lecture through the mobile attachment control, verified selection, submitted a real request to existing Windows Qwen over a temporary loopback SSH tunnel, and observed the completed response with rendered math. No model or server settings were changed. Existing PDF/PPTX history and library remain visible.
- Text contrast checks: primary text/main background 15.04:1; muted text/secondary surface 6.02:1; cyan/selected surface 5.19:1; Send text/orange 7.69:1; error text/error surface 7.33:1; success text/secondary surface 7.57:1. Reduced-motion rules disable all app animation/transition effects.
- Historical pre-deployment check showed the production launcher using `python -m macromellow` and live commit `9340dbf`. That hold was subsequently resolved by the authorized migration below; the visual redesign itself did not change Windows configuration.


## Live Windows migration — October 24, 2026

- Prepared under the existing elevated SSH owner. Paused the two-minute deployment task, provisioned pinned Python dependencies in its existing venv, preserved Ollama environment/GPU/model settings, and changed the checkout origin to the already renamed `Salami2222/Kippy` repository. Initial application/theme commits and the updater fix were pushed normally to main.
- The first activation safely refused during staging due to Windows PowerShell stripping embedded Python double quotes. Fixed the metadata query to use single quotes inside Python; added and ran a real native-subprocess regression. No serving code was promoted on that failed attempt. The corrected activation passed **39 Python, 16 frontend, 25 deployment, 8 Ollama-monitor, and 5 migration tests** before fast-forwarding/restarting the existing checkout/task.
- Existing **https://tp-industry.tail4cab83.ts.net/** returns HTTP 200 and Kippy HTML; `/api/version` identifies Kippy at the deployed commit. The actual browser shows the Deepwater academic interface, not the old nutrition dashboard. Tailscale Serve still forwards to `127.0.0.1:8000`; its node, hostname, TLS and absence of Funnel were unchanged.
- Through the real HTTPS endpoint, uploaded synthetic PDF/PPTX/DOCX/TXT lectures; all four returned ready. A real Qwen response used the integrating-factor example and supplied actual page/slide/section/line references. Browser math rendering, selected materials and source details were visually verified.
- Finalized `start-kippy.cmd` and restarted the existing web task. Compared the full conversation before/after: messages, source references, all four uploaded files and selections persisted unchanged under a new PID. The running command uses `-m kippy web --port 8000 --data-dir C:\Users\tommy\KippyData`.
- Web and Ollama tasks are running under existing S4U principals with boot triggers; auto-deploy and monitor tasks retain boot and recurring triggers. The scheduled model monitor and a real small inference check passed. Both listeners remain loopback-only, with one web backend and one Ollama server. No machine reboot or forced Ollama outage was performed.
- Removed only inspected discontinued targets: nutrition backup task, `backup-macromellow.py`, `start-macromellow.cmd`, transition launcher, cache-only `macromellow` directory, `MacroMellowData`, local `MacroMellowBackups` and six verified nutrition backup archives in the matching OneDrive directory. Kippy data, current supervision/logs, tasks needed by Kippy, credentials and Ollama/Tailscale were retained.
- Final documentation is published through the reenabled automatic GitHub deployment workflow; verify `/api/version` against main and `deploy-*.jsonl` success events after each release. There are no required first-migration manual steps left.


## Retro Handheld redesign — October 26, 2026

- Replaced the modern Deepwater styling with shared slate/cyan/sand/orange tokens, reusable pixel panel/action classes, layered hard borders, square menu selections, dialogue windows and a lightly tiled welcome-frame accent. Updated welcome copy while preserving all real suggestion, upload and chat actions. No mascot or game artwork was introduced.
- Bundled the unchanged 32 KB Silkscreen Regular font and SIL OFL license from Google Fonts. Only branding and brief interface labels use the pixel face; long answers, filenames, code and KaTeX remain readable. Font is preloaded and served locally, with system monospace fallback. No new runtime library or backend dependency.
- **39 Python tests, 16 frontend tests and JS syntax check passed.** Python wheel packaging passed, including the new font and license. There is no separate frontend build step for the static HTML/CSS/JS client.
- Browser checks covered desktop 1280×720, tablet 834×1112, mobile 390×844 and 320×568, plus a reduced-height 390×420 viewport. Verified internal conversation scrolling, sidebar open/collapse, long filenames, real mobile attachment upload, select/deselect persistence, and a real Qwen answer with headings, lists, display math and horizontally scrollable Python code. Copy response returned the original Markdown. A physical mobile keyboard was not tested.
- Checked representative text contrast: body/dialogue 10.78:1; muted text/selected panel 4.94:1; cyan/secondary panel 5.29:1; send label/orange 6.07:1; sand/sidebar 8.36:1. Reduced-motion rules turn off animation/transitions. Backend, data formats, storage keys, model settings and Windows/Tailscale deployment configuration remain unchanged.


## Course workspaces + Swamp Fantasy — October 26, 2026

### Product and persistence

Courses replace the global New Chat/history/Library UI. Each course owns one continuous conversation and its document collection. The header exposes Materials and Edit course; the drawer provides real PDF/PPTX/DOCX/TXT uploads and errors. Python derives both conversation and document scope from the supplied course ID, and SQL joins enforce course-only retrieval. Course metadata accompanies the existing bounded academic prompt. Assistant responses use an open reading area; user/composer panels retain original stepped frames, moss/teal/gold colors and restrained lantern/book/reed details. No mascot, model, framework or cloud service was added.

Four new link/metadata tables implement an additive schema upgrade; original six-table records and uploaded bytes stay unchanged. The explicit import UI copies a chosen old chat and its selected/cited files into a named course, remapping source IDs and retaining originals. Shared legacy documents get independent course copies. Confirmed course deletion removes only that course’s files/history. Interrupted staged file deletion is reconciled on restart. See `docs/course-migration.md` for deployment/rollback details; no manual SQL, dependencies, model changes or Windows task changes are needed.

### Checks completed before publication

- **55 Python tests and 19 frontend tests pass**, including creation, zero-course initialization, rename/delete, history switching/reload, scoping, hostile cross-course request IDs, original-schema migration, shared imports, copy failures, deletion recovery, modal/drawer behaviors, offline status, sanitization/math/code, copy, validation and post-mutation refresh failures. JavaScript syntax and Python dependency checks pass.
- Python wheel packaging succeeds and includes the new original SVG frame, static client and existing licensed pixel font. This application has no separate frontend compilation step.
- The actual local website started with zero courses. Created Differential Equations and Chemistry through the UI; uploaded PDF/PPTX/DOCX/TXT into Math and a separate TXT into Chemistry. Both real Qwen responses identified the correct active course and cited only that course’s material locations. Switching and renaming preserved histories and file counts.
- A real local backend restart preserved both full course workspaces byte-for-byte in API snapshots. A real browser legacy import copied a historical chat and remapped its source IDs; deleting its course via the confirmed API removed only the new copy while original messages and bytes remained intact.
- Visual checks cover 1280×720 desktop, 834×1112 tablet, 390×844 and 320×568 mobile, and a 390×420 reduced-height viewport. Verified course navigation, editor, drawer, internal scrolling, composer visibility and no page-width overflow. Physical phone keyboard behavior is not claimed as tested. Pixel fonts remain limited to short labels; body/math stay readable.
- Representative contrast ratios: body/main 15.14:1, muted/selected surface 4.57:1, teal/secondary surface 5.43:1, send label/gold 10.46:1, parchment/sidebar 11.48:1. Reduced-motion rules remain active.
- Windows preflight confirmed the existing main-branch deployment task and storage path. Its data fingerprint inventory contained zero conversations/materials/uploads; no production example courses were created during development. Existing-data preservation was exercised using populated migration fixtures and real local import checks. The normal guarded main-branch workflow will perform Windows staging tests before restarting into this additive upgrade.


## Enchanted pixel-art interface — October 26, 2026

- Recreated the supplied design using real course navigation, Materials drawer, Markdown/KaTeX messages and composer over local environmental artwork. The room contains scenery only; course names, buttons, uploads and responses are never baked into an image. Navy/gold framing, parchment panels, lavender user replies and readable serif body text replace the moss palette.
- Generated local library scenery and one transparent red-and-gold turtle using the built-in image tool and supplied references. Visually removed the unwanted green turtle from the room. Original SVG frame/compass/book/flask/hat and parchment grain are reusable UI assets. Prompts and provenance are in `kippy/static/assets/enchanted/README.md`. This is a responsive adaptation, not a pixel-identical copy of the reference.
- All **55 Python tests and 19 frontend tests pass**, along with JS syntax, compileall and dependency checks. Python wheel packaging succeeds and includes the local artwork. There is no separate frontend compilation step.
- Browser review covered the reference-sized **1448×1086**, **1280×720** laptop, **834×1112** tablet, **390×844** and **320×568** phones, and a **390×420** reduced-height viewport. Confirmed no page-width overflow, internal feed scrolling, anchored composer, course switching, editor, Materials drawer and mobile navigation. The 320px drawer has identical client/scroll widths (265px). Physical phone keyboard behavior is not claimed as tested; existing VisualViewport handling and safe-area padding remain.
- Uploaded a disposable TXT lecture into local QA storage through the actual file chooser. It reached `ready`, and actual Windows Qwen answered a browser-submitted question with the uploaded source and rendered inline mathematics. Existing PDF/PPTX/DOCX extraction, sanitization, persistence and course isolation remain covered by the unchanged backend tests.
- No backend, SQLite schema, deployment scripts, Ollama settings or runtime dependencies changed. The existing private Windows updater remains the release path.


## Sidebar deletion and cleanup — October 26, 2026

- Each sidebar course has a compact, keyboard-accessible three-dot menu. Delete Course opens a native modal naming the target, with the requested permanent-deletion warning and Cancel focused by default. Cancel/Escape leave all data unchanged. The existing editor delete control uses the same confirmation.
- Reused the unchanged backend deletion route and SQLite/file quarantine implementation. Frontend mutations remain locked while deletion is pending; failures stay visible in the modal. Deleting an inactive course preserves the selected conversation and unsent draft; deleting the active course selects an available survivor or clears the selection when none remain.
- Removed all three requested decorative phrases and the bordered sidebar information box, together with their unused styles. The existing turtle and study-room artwork remain unchanged.
- **55 Python tests and 25 frontend tests pass.** Added coverage for sidebar deletion, cancellation, keyboard dismissal, inactive-course preservation, active-course fallback and reload, failure/retry UI, duplicate suppression, and removed copy. Strengthened backend assertions for deleted messages, chunks, material mappings, files, and persistence, while retaining another course's records. Existing import tests protect independent copies and legacy originals.
- Browser-verified the desktop menu, exact warning text, Cancel behavior, 390px mobile drawer/menu, and 320px confirmation layout with no horizontal overflow. Destructive data-path checks run in isolated automated test storage; existing production courses are not deleted for QA.
- JS syntax and the production Python wheel build pass. The vanilla frontend has no separate compilation step; its HTML/CSS/JS are included directly in the wheel.


## Independent saved conversations and Double Chat — October 26, 2026

- Replaced the one-to-one link with transactional one-to-many course conversations. Existing course chat IDs/messages/documents remain intact; original conversations are named Original Chat once. Added backend-persisted layout and creation/split request-idempotency records. Repeated migration preserves subsequent names; a deliberately failed migration rolls back links and titles.
- Saved-chat creation, rename, reopening and confirmed deletion are course-scoped. Deleting one chat keeps shared files; deleting a course removes all its chats. Single/Double selections persist independently of stored messages. First split creates two empty chats; layout toggles reuse the pair. An explicit New Double Chat creates another fresh pair.
- Every AI request requires course and conversation IDs. Foreign targets are rejected. Prompt-building tests fail if another chat's history is even loaded. Both chats retrieve the same course lecture; same-course private history markers never cross prompts, and Chemistry cannot retrieve Math messages or documents. Broad source reviews retain the existing batching and context budget.
- Chat requests queue through the same Ollama client behind one lock; near-simultaneous HTTP requests both complete. Request IDs prevent repeated inference/message insertion. Each panel has its own composer, request identity, loading/error state, scroll and draft. Another composer stays editable during inference. Lifecycle/document mutations remain guarded.
- **68 Python tests and 33 frontend tests pass**, plus JavaScript syntax, compileall, dependency validation and the production wheel build. The static client has no separate frontend compilation step. No runtime dependencies, model configuration, Windows task/SSH/Tailscale settings, or artwork changes.
- Actual browser testing sent two near-simultaneous questions to Windows Qwen from separate local panels; both cited the same uploaded variation-of-parameters lecture and saved into the correct chats. A real backend restart preserved complete API snapshots of courses, chat names/messages, source files and layouts exactly. Desktop 1280×720 uses two independently scrolling, equal-width panes. At 390×844 only the chosen mobile tab is visible and the composer stays above the viewport bottom; both unsent drafts remain distinct.
- An online Windows SQLite backup and separate two-pass candidate migration rehearsal preserved every original course/message/document/link record (except the intended Original Chat title change) and upload hash. Source data was read-only during rehearsal. See `docs/course-migration.md` for storage, scoped endpoints, backup and rollback notes. Destructive chat tests use isolated automated storage, not existing user conversations.
- Remaining scope: completed (non-streaming) responses, one queued model instance, fixed equal-width desktop panels, and session-scoped local drafts. Physical mobile keyboard hardware is not claimed as tested; existing VisualViewport/safe-area handling remains.

### Completion checks — October 29, 2026

- Re-ran all 68 Python tests, 33 frontend tests, JavaScript syntax, dependency validation and production wheel packaging successfully.
- Refreshed the Windows online backup and repeated the candidate migration twice on its isolated copy; the original course and two messages remained intact. Live activation uses the existing automatic deployment workflow after the completed commit is pushed.

- Windows staging found two migration-test connections that relied on garbage collection for closure. They now close explicitly, including rollback paths, so temporary SQLite files can be removed on Windows. The updater correctly left the serving release untouched when those tests failed.

## Explicit deletion confirmations — October 29, 2026

- Every saved conversation now has a compact native ⋯ menu; its Delete Conversation action and the existing panel delete button open the same named, destructive confirmation. Cancel/Escape makes no request. Successful deletion purges the initiating tab's message/draft/retry/error/scroll caches and repairs the selected chat without deleting course materials.
- Course deletion has two separate modal dialogs: a scope warning, then exact-name verification. Cancel/Escape exits both stages. The final red action is disabled until the case-sensitive name matches, and its handler also checks the final stage and open dialog. The backend validates both course ID and exact current name, and returns deleted chat IDs for cache cleanup.
- Existing database cascades and quarantined-file transaction/restart recovery are preserved. Added assertions cover all chat state tables, source files, foreign-key consistency, restart persistence and other-course preservation. Legacy source copies remain independently owned. Kippy has no separate conversation-summary store or external vector index.
- 69 Python tests and 36 frontend tests pass; JavaScript syntax, production wheel packaging and diff checks pass. Browser verification on desktop 1280×720 and mobile 390×844 covered both dialogs, incorrect-case rejection, cancellation, actual isolated chat/course deletion and refresh persistence. Original production user data was not used for destructive tests.
- Authorization remains the existing single-user/private Tailscale model with write-origin/custom-header guards and course-scoped membership validation. Multi-user account authorization is not implemented. Other already-open browser tabs need refreshing to remove previously rendered text; the server no longer returns deleted resources.
