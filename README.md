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

A fresh installation starts with **zero courses**. Click **Add Course**, enter any course name, and optionally add a code and description. Kippy creates an empty **New Chat** for that course and opens it immediately. Each course can hold multiple independently named, persistent conversations sharing one document library. Switch courses in the sidebar; the last active course is remembered by the browser. There is no global New Chat or Library.

- **Materials** in the course header opens its file drawer. Upload PDF, PPTX, DOCX or TXT using the drawer, the composer’s **＋**, or drag/drop. Files automatically belong to the active course, including failed uploads whose errors remain visible. All ready course files are available for retrieval; no global document-selection step is needed.
- Type a question, press Enter to send, or Shift+Enter for a newline. Responses support sanitized Markdown, code blocks and KaTeX math (`$…$`, `$$…$$`, `\\(…\\)`, `\\[…\\]`). **Copy response** copies the original Markdown. Source details show actual context locations.
- The **⋯ Edit course** control lets you rename a course or change its code/description without renaming or replacing its saved chats. Each sidebar course also has a small **⋯** actions menu with **Delete Course**. A confirmation dialog defaults to **Cancel** and names the target course before deleting all its saved chats, messages, extracted sections and uploaded files. Deleting another course leaves the current conversation and draft intact; deleting the selected course opens another available course or the empty selection screen. The course editor’s delete action uses the same dialog. Removing an individual material also requires confirmation; saved answers remain.
- Every request identifies both its course and saved chat. Python validates that relationship, reads only that chat’s history, and retrieves only course-owned documents. Foreign conversation IDs are rejected; supplied material IDs cannot broaden retrieval. General academic knowledge is still available and is distinguished from document evidence.
- If older global chats/files exist, **Bring existing studies into a course** opens the course editor’s import options. Choose one saved chat and/or loose files. Kippy copies that history and the referenced/selected files into the new course, preserving the originals unchanged. See [course migration](docs/course-migration.md).
- The sidebar collapses on desktop and becomes a dismissible mobile menu. Materials use a drawer; chat scrolls internally with the composer near the bottom.

### Saved conversations and Double Chat

- **＋ New Chat** under the active course creates a named, empty chat. Use the pencil beside its selector to rename it; the **×** asks for confirmation before deleting only that chat and its messages. Shared course documents stay available to other chats. Chat names are saved independently from course names.
- **Single Chat** displays one saved conversation. The selector or sidebar reopens its complete history. Switching courses restores that course’s saved selection and layout.
- The first **Double Chat** click preserves the existing conversation and creates two new, empty chats, **Chat 1** and **Chat 2**. Neither receives old messages. Both can retrieve the same course materials. Each panel has its own selector, composer, scrolling, loading/error status and request ID. Selecting a chat already owned by the other panel focuses that panel instead of duplicating it.
- Returning to **Single Chat** displays the currently targeted chat. Returning to **Double Chat** restores the saved pair. These toggles do not create or delete chats. **＋ New Double Chat** explicitly creates another fresh pair and keeps earlier chats saved.
- Narrow windows use **Chat A / Chat B** tabs instead of squeezing two columns. Drafts stay associated with chat IDs across tabs, layout toggles and course switches; session storage also survives ordinary refreshes. Drafts are never submitted or shared automatically. Chat selection, names, layout and messages persist in SQLite across server restarts; the browser remembers the selected course.
- Chats share **only course metadata and academic source documents**, not messages, conversational summaries, memory or drafts. Kippy is instructed to acknowledge that it cannot see other saved chats. Prompts retain the existing history and source-context budgets; there is no cross-chat message retrieval or model training.
- Questions from both panels may be sent close together. Python queues them through the same Ollama client/model. The other composer remains editable while one response is pending. Lifecycle changes/uploads are disabled during inference to prevent deleting a chat or document underneath a pending response. Responses remain non-streaming. There is no draggable divider in this lightweight equal-width layout.

The **Enchanted Study** interface uses locally stored pixel-art library scenery, a moonlit castle window, navy-and-gold course navigation, parchment conversation panels, lavender user messages, and one decorative red-and-gold turtle. The active course fills the remaining viewport with an internally scrolling conversation and anchored composer. Materials stay in a compact button that opens the existing drawer. On mobile, courses move into a drawer and scenery yields to readable chat space. Academic responses use readable serif text, code uses monospace, and math retains KaTeX; Silkscreen is limited to small UI labels. Reduced-motion preferences disable animation and transitions. No mascot animation or game mechanics are included. See [artwork provenance and prompts](kippy/static/assets/enchanted/README.md).

## Persistent storage and upload limits

Default storage is **`~/.kippy/`**, outside the repository and deployment staging directories:

```text
kippy.sqlite3   courses, named chats, layouts, messages, documents/chunks, idempotency/import records
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

Python extracts text and stores overlapping chunks with file IDs, names, and page/slide/section references. A database join limits retrieval to documents owned by the active course, and the prompt includes that course’s name, code and description. Lexical retrieval ranks relevant text; explicit page/slide requests prioritize that location. The prompt includes only the target chat’s recent conversation context (at most 12 messages and 10,000 characters), allowing follow-ups without resending an entire history. Full history remains stored for viewing.

Summaries, exam reviews, memorization lists, and study guides review **all extracted sections** of the active course’s materials. Long materials are processed in bounded batches, then combined through hierarchical summaries. More than 24 input batches produces an explicit request for a narrower topic or specific pages/slides, rather than a falsely complete answer. Individual prompt excerpts are budgeted conservatively by characters, not an exact Qwen tokenizer. Summarization is lossy by nature: check important formulas and details against originals.

Uploaded text is supplied as **untrusted data** in user-context containers, never as a system instruction. Kippy is instructed to cite filenames/pages/slides, distinguish document evidence from general knowledge, and acknowledge missing support. The expandable **Context provided** list shows actual retrieved source locations; it does not independently verify every model-generated claim. Qwen can still make mistakes, including citation or mathematical errors. There is no vector database or external search.

Responses are currently **non-streaming**: the former transport used completed Ollama responses. This keeps multi-stage review and final persistence straightforward. Loading remains visible while Qwen works. A single inference/mutation lock queues chat requests to one model and prevents conflicting lifecycle edits during inference/upload; document or course mutations get a retryable busy response while it is held. Metadata/history reads continue while generation runs. Interrupted generations are marked as errors after a server restart; request IDs prevent replaying the same completed request.

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

Startup automatically upgrades the former one-to-one course/chat link to one-to-many in a transaction. Existing chat IDs, messages, courses and document links are preserved; each original course chat is named **Original Chat** once. Layout and request-idempotency tables are added without copying course documents. Reload already-open browser tabs after deployment: chat requests now require an explicit conversation ID. No manual SQL, new dependencies, Windows task changes, model changes or storage relocation are required. See [migration and rollback notes](docs/course-migration.md).

### Deleting saved studies

Each saved conversation has a compact ⋯ menu with **Delete Conversation**; the panel's existing delete control opens the same named confirmation. Confirming removes only that chat's messages, turns, selections and cached drafts, never the shared course materials. Cancel makes no deletion request.

**Delete Course** first lists everything being removed. Choose **Continue to Delete**, then type the exact, case-sensitive course name in a separate final dialog. The red **Delete Course** button stays disabled until it matches; Cancel or Escape at either step exits the entire flow. The backend also requires `confirm_course_id` and `confirm_course_name` matching the current course. Older browser tabs must reload before deleting courses. Success appears only after the server confirms deletion.

Course deletion cascades through every owned chat, message, turn, layout and retrieval record. Existing transactional file quarantine restores files if deletion fails and recovers interrupted cleanup at startup. Other courses and independently copied legacy materials remain intact. Kippy stores no separate AI summaries or external vector index; prompt context comes from the retained messages and course chunks. Deletion clears active in-browser caches and saved selections in the initiating tab. Already-open tabs cannot erase text already displayed elsewhere; refresh them after deletion.

Kippy remains a single-user local application behind the existing private Tailscale access. It has no account-level ownership/authentication system and cannot claim multi-user authorization. Scoped routes reject conversations/files belonging to another course; write requests retain the existing origin and custom-header protections.
