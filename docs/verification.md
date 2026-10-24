# Kippy conversion verification — October 24, 2026

## Implemented

- Responsive Deepwater chat UI with a collapsible conversation/library sidebar, persistent history, New Chat, conversation switching/deletion, multiline composer, copy, loading/error states, sanitized Markdown, code blocks and locally rendered LaTeX.
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
