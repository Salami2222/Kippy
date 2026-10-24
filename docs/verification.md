# Kippy conversion verification — October 24, 2026

## Implemented

- Responsive, neutral chat UI with a collapsible conversation/library sidebar, persistent history, New Chat, conversation switching/deletion, multiline composer, copy, loading/error states, sanitized Markdown, code blocks and locally rendered LaTeX.
- Real PDF/PPTX/DOCX/TXT upload/extraction, persistent random-ID files, SQLite metadata/chunks, per-conversation selections, processing/errors, removal and drag/drop.
- Qwen3.5:9b through the Python Ollama client, bounded recent history, lexical retrieval, explicit page/slide/section/line requests, and section-by-section whole-document review with explicit coverage limits.
- Existing loopback/Tailscale access model and deployment/Ollama supervision retained. New release checks and a guarded first-migration procedure are prepared, not deployed.

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
| Frontend suite, Mac Node 26 | **14 passed, 0 failed** |
| Same Python suite in disposable Windows Python 3.13 environment | **39 passed, 0 failed** |
| Same frontend suite in disposable Windows Node 24 environment | **14 passed, 0 failed** |
| Existing isolated Windows deployment tests | **24 passed, 0 failed** |
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

**The production Windows application has not been converted or restarted. The completed local main commit is intentionally not pushed until Windows migration preparation is performed.** The installed launcher still names the removed package, and its old release checker expects removed mascot assets. Follow `docs/windows-migration.md` before pushing. The prepared migration keeps one web application, one Ollama instance and one deployment/monitoring workflow; existing operational task/path names remain to avoid credential and supervisor breakage. Cleanup instructions identify the inspected obsolete backup task/data destinations, and require reviewing those exact targets after Kippy is healthy.
