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

## The interface

The **Deepwater** theme uses deep navy surfaces, cyan selections and focus rings, and an orange Send button. Its palette is defined at the top of `kippy/static/app.css`. Welcome suggestions fill the composer for editing; they do not send automatically. Document type badges and status colors reflect actual library data. Reduced-motion preferences disable transitions and animations.

- **New chat** creates a fresh stored conversation. The sidebar lists actual conversations; reopen or delete them there. The last selected conversation is remembered by this browser.
- Type a question, press Enter to send, or Shift+Enter for a newline. Responses support sanitized Markdown, code blocks, and KaTeX math (`$…$`, `$$…$$`, `\(…\)`, `\[…\]`). **Copy response** copies the original Markdown.
- Upload using **＋**, **Upload**, or drag files into the conversation. Upload and extraction states are real, not simulated. Successful uploads are selected for the current conversation automatically, up to 20 files.
- Select/deselect existing materials using the library checkboxes. Selections are stored per conversation and shown above the composer. **×** removes a material from disk, metadata, extracted chunks, and all conversation selections after confirmation. Previously generated answers remain as history.
- Collapse the sidebar for more chat space. On mobile it opens as a dismissible overlay. Conversation scrolling stays inside the application; the composer follows the visual viewport.

No account system, analytics dashboard, or mascot is included.

## Persistent storage and upload limits

Default storage is **`~/.kippy/`**, outside the repository and deployment staging directories:

```text
kippy.sqlite3   conversations, messages, file metadata, extracted text/chunks, selections
uploads/       original files, stored under random internal IDs
```

Set `KIPPY_DATA_DIR` or pass `--data-dir` to choose a persistent location. Windows migration uses **`C:\Users\tommy\KippyData`**. Do not put uploads in temporary storage, the source checkout, or a staging worktree. Private uploads and SQLite files are excluded from Git; never force-add them.

Default maximum upload size: **25 MiB per file**, configurable with `KIPPY_MAX_UPLOAD_MB` (1–100). Up to 20 ready materials can be selected per conversation. There is no automatic deletion or total disk quota; remove unwanted files in the library and monitor local disk usage.

| Format | Extracted content | References |
| --- | --- | --- |
| PDF | Embedded text, page by page | Page numbers |
| PPTX | Slide text boxes, grouped text, table cells | Slide numbers |
| DOCX | Body paragraphs and tables in order | Section numbers; no Word pagination |
| TXT | UTF-8 (optional BOM), or BOM-marked UTF-16 | Line ranges |

Scanned pages, handwriting, embedded diagrams, images, speaker notes, PDF reading order/layout reconstruction, equations stored as images, and Word equation objects are **not** understood by extraction. Empty/image-only materials fail visibly; partial empty-page extraction carries a warning. Run OCR externally before uploading scans. Corrupt files fail visibly and can be removed. ZIP-based files are checked for type and expansion limits; PDF/PPTX limits are 1,000 pages/slides, extracted text is capped at 2 million characters and 5,000 chunks. Parser limits reduce risk but do not provide a sandbox for malicious documents; upload files you trust to this private prototype.

## Grounded conversations

Python extracts text and stores overlapping chunks with file IDs, names, and page/slide/section references. Lexical retrieval ranks relevant text; explicit page/slide requests prioritize that location. The prompt includes recent conversation context (at most 12 messages and 10,000 characters), allowing follow-ups without resending an entire history. Full history remains stored for viewing.

Summaries, exam reviews, memorization lists, and study guides review **all extracted sections** of selected materials. Long materials are processed in bounded batches, then combined through hierarchical summaries. More than 24 input batches produces an explicit request to select fewer materials, rather than a falsely complete answer. Individual prompt excerpts are budgeted conservatively by characters, not an exact Qwen tokenizer. Summarization is lossy by nature: check important formulas and details against originals.

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

## Windows deployment — migration required before pushing

**Do not send this first conversion to automatic deployment without preparation.** The currently installed launcher invokes the removed Python package, and the old updater requires removed mascot assets. An ordinary first auto-update would fail its health checks and attempt rollback.

Follow [the verified installation inventory and migration procedure](docs/windows-migration.md). The prepared transition runs one application on the existing port, retains Ollama and its environment, and creates no application backup or archive repository. After migration, the normal workflow remains **Codex → GitHub main → Windows automatic deployment**.

Operational script names, task identifiers, SSH key, and existing installation/service directory names are retained where required by the inspected host. They do not run a second application or retain discontinued features. The old application package, dashboards, tools, scrapers, database schema, sprites, and associated tests have been removed from the tracked codebase.

See [conversion verification](docs/verification.md) for actual test counts, live Qwen/browser checks, major changes and remaining limitations.
