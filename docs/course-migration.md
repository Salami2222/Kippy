# Course workspace migration

## Deployment

This release stays in the existing checkout, venv, Windows tasks, port 8000 and `C:\Users\tommy\KippyData`. The existing main-branch updater stages and tests the release before restarting the same Kippy process. There are **no manual database or Windows configuration steps**. Reload an already-open browser tab after deployment because the API is now course-based.

## Independent saved-chat migration

Startup detects the former `course_conversations.course_id` primary key. In one SQLite transaction it renames that link table, creates the one-to-many replacement (`conversation_id` is now its primary key), copies every link, renames each preexisting course conversation **Original Chat**, and drops the old link table. IDs, message contents/order/timestamps, courses, material rows, extracted chunks, selections and uploaded bytes are retained. This detection becomes false after success, so repeated startup does not duplicate records or overwrite later chat names. A failed migration rolls back the original link table and titles.

`course_layouts` saves Single/Double mode, single-chat selection, left/right IDs, active panel and whether a first split has occurred. `chat_splits` and `chat_creations` retain request IDs for safe retries. These records reference the existing course/conversation tables. New chats reuse course material links; no files, excerpts or conversation messages are copied into them. A fresh installation still starts with zero courses.

For this Windows release, an online SQLite backup was taken outside the running application in `C:\Users\tommy\KippyMigration\IndependentChats\backup-a390bea78e`. The candidate migration was run twice on a separate rehearsal copy and checked against every original row (allowing the intended original-chat title change) and upload hash. The live application was not migrated during rehearsal. The existing automatic updater stages tests, then the same web process applies the migration at restart.

## Bringing older studies into courses

If pre-course global chats or files exist, the sidebar offers **Bring existing studies into a course**. It opens Add Course with **Import existing studies** expanded:

1. Enter a course name (code and description are optional).
2. Optionally select one old chat. Its messages, selected files and still-present historical cited files are copied together. Source file IDs in copied messages are remapped to that course’s new document IDs.
3. Optionally select other old files, then create the course. At most 300 total files can be imported in one operation.

The originals remain unassigned and unchanged, not part of active retrieval. There is no silent automatic classification or course creation. Shared legacy files get independent copies in each imported course, preventing edits/deletion in one course from affecting another. Copying uses additional disk space. A missing ready original or failed copy aborts the import, rolls back its database writes and removes incomplete copies; the originals remain intact. The same old conversation cannot accidentally be imported twice while its imported course exists.

Deleting an imported course deletes its **course-owned copies and all saved conversations**, not the preserved originals. Preserved pre-course originals can be imported again later. This preservation is intentional. New course uploads have no hidden original copy.

## Runtime boundaries

The UI retains the existing course/material routes. Additional scoped endpoints are:

- `POST /api/courses/<course_id>/conversations`: create a named chat (`name`, optional idempotent `request_id`).
- `GET/POST/DELETE /api/courses/<course_id>/conversations/<conversation_id>`: read, rename (`name`), or delete with matching `confirm_conversation_id`. Chat deletion preserves course files and repairs selections transactionally.
- `POST /api/courses/<course_id>/split`: create the first independent pair, or an explicit new pair (`request_id`, optional `fresh: true`). Repeated request IDs do not create duplicates.
- `POST /api/courses/<course_id>/layout`: save mode and validated selections. Opening the same chat in both panels or a foreign course’s chat is refused.
- `GET /api/courses/<id>` includes saved-chat metadata and workspace selections. A compatibility `conversation` view is also included; new inference does not use it to choose a target.

`/api/chat` requires `course_id`, **`conversation_id`**, `message` and `request_id`. Python validates course membership before reading that conversation, and never loads another chat’s messages to build its prompt. Ready-document retrieval joins `course_materials`; client material IDs cannot expand scope. Broad reviews retain the existing bounded document-wide approach. Chat requests queue through one Ollama client/model, while lifecycle/document writes are guarded. No second model/server or cross-chat summary memory exists.

Course deletion requires matching `confirm_course_id` and removes every linked conversation and all course-owned files. `/api/legacy` remains available only for explicit import choices; former global `/api/conversations` and `/api/materials` routes remain 404. This is still a private single-user application, not a multi-user authorization system.

## Deletion and rollback

A course deletion stages its owned original files in an internal quarantine before deleting its database rows in one transaction. Files are restored if staging or SQL fails. On restart, any interrupted deletion is reconciled against the material records: files for retained records are restored, while files whose deletion committed are cleaned up. Unrelated directories are not touched.

The previous one-chat release can still read an original link during automatic activation rollback, before users create additional saved chats. It is **not a safe everyday editor for the new multi-chat database**: its course deletion code knows only one conversation. Prefer forward fixes after successful activation. Do not drop tables or automatically restore the pre-upgrade database over newer conversations. A manual data restore would discard post-backup changes and requires a deliberate recovery decision. The updater’s code rollback is not a database restore.

## Verification

Automated tests exercise zero-course initialization, persistence, rename, deletion/confirmation, independent histories, all extraction formats, course-only retrieval, hostile cross-course IDs, old six-table data migration, shared-file imports, failed-copy rollback and restart recovery of interrupted course deletion. Frontend tests cover course creation/switching/reload, mobile sidebar, drawer uploads/removal, loading guards, Markdown/math/code, copy, errors and import choices.

Saved-chat tests also verify transactional migration failure/retry, repeat startup, independent same-course prompts, cross-course rejection, shared file reuse, chat deletion/selection repair, fresh/idempotent splits, concurrent HTTP requests and inference serialization, duplicate request suppression, independent panel errors/drafts/scrolling, mobile tabs and layout restoration.
