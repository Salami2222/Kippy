# Kippy

**An AI-powered academic study assistant that helps students understand lecture materials and prepare for exams.**

Kippy runs a small Python web backend and a responsive HTML/CSS/vanilla JavaScript chat UI. It uses your existing Ollama installation and `qwen3.5:9b`, without a cloud model or paid API. Chat, uploads, extracted text, and selections persist locally in SQLite and on disk.

## Run from VS Code on your Mac

Open this repository folder in VS Code, then use its integrated terminal:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export OLLAMA_MODEL=qwen3.5:9b
python -m kippy web
```

Requires **Python 3.11+**. The browser opens at [http://127.0.0.1:8000](http://127.0.0.1:8000). Use `--no-browser`, `--port 8001`, or `--data-dir /absolute/persistent/path` as needed. Press Ctrl+C to stop. Select `.venv/bin/python` with VS Code's **Python: Select Interpreter** command if using its Python extension.

Kippy reads exported environment variables; `.env.example` is documentation, not an automatically loaded file. Do not overwrite working Ollama settings. If Ollama is offline, conversations and the library still load; failed answers are saved with a clear connection message.

### Use the Windows model from your Mac

If Ollama is only listening on Windows loopback, use a temporary SSH tunnel in a separate terminal:

```bash
ssh -N -i ~/.ssh/macromellow_windows -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:11445:127.0.0.1:11434 tommy@100.81.130.10
```

Then launch Kippy with `export OLLAMA_BASE_URL=http://127.0.0.1:11445`. This does not expose Ollama publicly, change its bind address, install a model, or alter Tailscale configuration. Stop the tunnel with Ctrl+C.

## Course workspaces

A fresh installation starts with **zero courses**. Click **Add Course**, enter any course name, and optionally add a code and description. Kippy creates exactly one continuous, persistent conversation for that course and opens it immediately. Switch courses in the sidebar; the last active course is remembered by the browser. There is no global New Chat or Library.

- **Materials** in the course header opens its file drawer. Upload PDF, PPTX, DOCX or TXT using the drawer, the composer’s **＋**, or drag/drop. Files automatically belong to the active course, including failed uploads whose errors remain visible. All ready course files are available for retrieval; no global document-selection step is needed.
- Type a question, press Enter to send, or Shift+Enter for a newline. Responses support sanitized Markdown, code blocks and KaTeX math (`$…$`, `$$…$$`, `\\(…\\)`, `\\[…\\]`). **Copy response** copies the original Markdown. Source details show actual context locations.
- The **⋯ Edit course** control lets you rename a course or change its code/description without replacing its conversation. **Delete course** requires an explicit confirmation and removes its conversation, messages, extracted sections and uploaded files. Removing an individual material also requires confirmation; saved answers remain.
- A course uses only its own conversation history and documents. Python derives the conversation and retrieval scope from the course ID; client-provided conversation/material IDs cannot broaden it. General academic knowledge is still available and is distinguished from document evidence.
- If older global chats/files exist, **Bring existing studies into a course** opens the course editor’s import options. Choose one saved chat and/or loose files. Kippy copies that history and the referenced/selected files into the new course, preserving the originals unchanged. See [course migration](docs/course-migration.md).
- The sidebar collapses on desktop and becomes a dismissible mobile menu. Materials use a drawer; chat scrolls internally with the composer near the bottom.

The **Enchanted Study** interface uses locally stored pixel-art library scenery, a moonlit castle window, navy-and-gold course navigation, parchment conversation panels, lavender user messages, and one decorative red-and-gold turtle. The active course fills the remaining viewport with an internally scrolling conversation and anchored composer. Materials stay in a compact button that opens the existing drawer. On mobile, courses move into a drawer and scenery yields to readable chat space. Academic responses use readable serif text, code uses monospace, and math retains KaTeX; Silkscreen is limited to small UI labels. Reduced-motion preferences disable animation and transitions. No mascot animation or game mechanics are included. See [artwork provenance and prompts](kippy/static/assets/enchanted/README.md).

## Persistent storage and upload limits

Default storage is **`~/.kippy/`**, outside the repository and deployment staging directories:

```text
kippy.sqlite3   courses, linked conversations, messages, documents/chunks, import records
uploads/       original files, stored under random internal IDs
```

Set `KIPPY_DATA_DIR` or pass `--data-dir` to choose a persistent location. Windows migration uses **`C:\Users\tommy\KippyData`**. Do not put uploads in temporary storage, the source checkout, or a staging worktree. Private uploads and SQLite files are excluded from Git; never force-add them.

Default maximum upload size: **25 MiB per file**, configurable with `KIPPY_MAX_UPLOAD_MB` (1–100). Ready documents are scoped to their course; there is no 20-file selection limit. Complete reviews remain bounded by the existing 24-batch context limit. There is no total disk quota; imports create independent copies and therefore use additional disk space.

| Format | Extracted content | References |
| --- | --- | --- |
| PDF | Embedded text, page by page | Page numbers |
| PPTX | Slide text boxes, grouped text, table cells | Slide numbers |
| DOCX | Body paragraphs and tables in order | Section numbers; no Word pagination |
| TXT | UTF-8 (optional BOM), or BOM-marked UTF-16 | Line ranges |

Scanned pages, handwriting, embedded diagrams, images, speaker notes, PDF reading order/layout reconstruction, equations stored as images, and Word equation objects are **not** understood by extraction. Empty/image-only materials fail visibly; partial empty-page extraction carries a warning. Run OCR externally before uploading scans. Corrupt files fail visibly and can be removed. ZIP-based files are checked for type and expansion limits; PDF/PPTX limits are 1,000 pages/slides, extracted text is capped at 2 million characters and 5,000 chunks. Parser limits reduce risk but do not provide a sandbox for malicious documents; upload files you trust to this private prototype.

## Grounded conversations

Python extracts text and stores overlapping chunks with file IDs, names, and page/slide/section references. A database join limits retrieval to documents owned by the active course, and the prompt includes that course’s name, code and description. Lexical retrieval ranks relevant text; explicit page/slide requests prioritize that location. The prompt includes recent conversation context (at most 12 messages and 10,000 characters), allowing follow-ups without resending an entire history. Full history remains stored for viewing.

Summaries, exam reviews, memorization lists, and study guides review **all extracted sections** of the active course’s materials. Long materials are processed in bounded batches, then combined through hierarchical summaries. More than 24 input batches produces an explicit request for a narrower topic or specific pages/slides, rather than a falsely complete answer. Individual prompt excerpts are budgeted conservatively by characters, not an exact Qwen tokenizer. Summarization is lossy by nature: check important formulas and details against originals.

Uploaded text is supplied as **untrusted data** in user-context containers, never as a system instruction. Kippy is instructed to cite filenames/pages/slides, distinguish document evidence from general knowledge, and acknowledge missing support. The expandable **Context provided** list shows actual retrieved source locations; it does not independently verify every model-generated claim. Qwen can still make mistakes, including citation or mathematical errors. There is no vector database or external search.

Responses are currently **non-streaming**: the former transport used completed Ollama responses. This keeps multi-stage review and final persistence straightforward. Loading remains visible while Qwen works. A single-user mutation lock prevents conflicting edits during inference/upload; other tabs get a retryable busy response. Metadata/history reads continue while generation runs. Interrupted generations are marked as errors after a server restart; request IDs prevent replaying the same completed request.

## Privacy and networking

The backend binds **127.0.0.1 only** and keeps the existing private Tailscale reverse-proxy model. The browser calls Python, never Ollama directly. There are no public file-download routes; original uploads are not static assets. Writes require a same-origin/Tailscale request plus a custom header; Host validation limits DNS rebinding. Markdown is sanitized with DOMPurify, math uses KaTeX with untrusted commands disabled, and all browser libraries/fonts are served locally. See [vendor provenance](kippy/static/vendor/README.md).

This is a single-user prototype **without authentication**, as requested. Keep it behind your existing trusted local/Tailscale access; do not publish the port or proxy publicly. Document text is sent to the configured Ollama server. The application does not automatically download models or change GPU/model storage/network settings.

## Tests and checks

Python dependencies are pinned in `requirements.txt`. Frontend test dependencies are pinned in `package-lock.json`; Node **24.14+** is required for the current jsdom test version (the inspected Windows installation has Node 24.18.0).

```bash
source .venv/bin/activate
python -m unittest discover -s tests -q
npm ci --ignore-scripts
npm test
npm run check
python -m compileall -q kippy
python -m pip check
```

Python tests create real PDF, PPTX, DOCX, and TXT fixtures in temporary directories. They cover persistence, extraction, references, uploads, safe names, retrieval, whole-document coverage, prompts, offline Ollama, HTTP routes/security, and deletion. Frontend tests exercise the actual client in jsdom, including sanitization and math rendering. No real model is needed for ordinary tests. There is no frontend compilation/build step. Existing isolated PowerShell deployment/monitor tests remain in `tests/Test-Deployment.ps1`, `tests/Test-OllamaMonitor.ps1`, and `tests/Test-KippyMigration.ps1`; run them on Windows under the existing elevated task-owner identity. Their task operations are mocked.

## Windows deployment

Kippy is deployed on the existing Windows PC at **https://tp-industry.tail4cab83.ts.net/**. Tailscale Serve forwards privately to `http://127.0.0.1:8000`; no Funnel or second hostname is configured. Persistent conversations and uploads live in `C:\Users\tommy\KippyData`.

The initial migration is complete. The existing web task now launches `python -m kippy web`, and automatic deployment from GitHub `main` and independent Ollama monitoring are enabled. The discontinued nutrition backup task, launchers, package cache, database and backup archives were removed after verification. The normal workflow is **Codex → GitHub main → Windows automatic deployment**.

[The migration guide](docs/windows-migration.md) records the initial transition and retained installation bindings. Do not rerun first-migration activation on an already finalized installation. Use `scripts/kippy-update --check` for a manual health/test check; normal pushes are picked up by the existing scheduled deployment workflow.

Operational script names, task identifiers, SSH key, and existing installation/service directory names are retained where required by the inspected host. They do not run a second application or retain discontinued features. The old application package, dashboards, tools, scrapers, database schema, sprites, and associated tests have been removed from the tracked codebase.

See [conversion verification](docs/verification.md) for actual test counts, live Qwen/browser checks, major changes and remaining limitations.

### Course database upgrade

Course support uses an automatic **additive SQLite migration** on startup. No manual SQL, new dependencies, Windows task changes, model changes or storage relocation are required. Existing tables and global records remain intact; old data is associated with courses only through the explicit import UI. Read [the migration and rollback notes](docs/course-migration.md) before deploying this first course-based release.
