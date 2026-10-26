# Course workspace migration

## Deployment

This release stays in the existing checkout, venv, Windows tasks, port 8000 and `C:\Users\tommy\KippyData`. The existing main-branch updater stages and tests the release before restarting the same Kippy process. There are **no manual database or Windows configuration steps**. Reload an already-open browser tab after deployment because the API is now course-based.

Startup adds four tables with `CREATE TABLE IF NOT EXISTS`: `courses`, `course_conversations`, `course_materials`, and `legacy_imports`. The original six tables retain their columns, values and IDs. Foreign keys and a unique course/conversation relationship enforce one conversation per course and one owning course per uploaded material. Existing databases and uploaded bytes are not reset, relocated or overwritten. Fresh storage has no courses, conversations or example documents.

## Bringing older studies into courses

If pre-course global chats or files exist, the sidebar offers **Bring existing studies into a course**. It opens Add Course with **Import existing studies** expanded:

1. Enter a course name (code and description are optional).
2. Optionally select one old chat. Its messages, selected files and still-present historical cited files are copied together. Source file IDs in copied messages are remapped to that course’s new document IDs.
3. Optionally select other old files, then create the course. At most 300 total files can be imported in one operation.

The originals remain unassigned and unchanged, not part of active retrieval. There is no silent automatic classification or course creation. Shared legacy files get independent copies in each imported course, preventing edits/deletion in one course from affecting another. Copying uses additional disk space. A missing ready original or failed copy aborts the import, rolls back its database writes and removes incomplete copies; the originals remain intact. The same old conversation cannot accidentally be imported twice while its imported course exists.

Deleting an imported course deletes its **course-owned copies and conversation**, not the preserved originals. The confirmation states this explicitly; those originals can be imported again later. This preservation is intentional. New course uploads have no hidden original copy.

## Runtime boundaries

The UI uses `GET/POST /api/courses`, `GET/POST/DELETE /api/courses/<id>`, `POST /api/courses/<id>/materials?filename=...`, and `DELETE /api/courses/<id>/materials/<material_id>`. Chat posts `course_id`, `message` and `request_id` to `/api/chat`. Deleting a course also requires JSON `confirm_course_id` matching its URL. `/api/legacy` exposes migration choices only. Former global `/api/conversations` and `/api/materials` routes return 404.

Python resolves the continuous conversation through `course_conversations`; ready-document retrieval joins `course_materials`. Supplying another course’s conversation or material IDs in a chat request cannot expand that scope. Removing a foreign course’s material is refused. All ready documents of a course are eligible; relevant questions use lexical retrieval and document-wide reviews use the existing bounded synthesis. File errors remain visible in Materials; unreadable files do not contribute text. This remains a private single-user application, not a multi-user authorization system.

## Deletion and rollback

A course deletion stages its owned original files in an internal quarantine before deleting its database rows in one transaction. Files are restored if staging or SQL fails. On restart, any interrupted deletion is reconciled against the material records: files for retained records are restored, while files whose deletion committed are cleaned up. Unrelated directories are not touched.

Because this migration does not change original column layouts or erase data, the previous Kippy release can still read/write its original tables if the existing updater needs to roll back. It will display global chats/files rather than course organization. Returning to this release restores course links and offers any new unassigned records for explicit import. Do not manually drop course tables or restore an older database over current storage.

## Verification

Automated tests exercise zero-course initialization, persistence, rename, deletion/confirmation, independent histories, all extraction formats, course-only retrieval, hostile cross-course IDs, old six-table data migration, shared-file imports, failed-copy rollback and restart recovery of interrupted course deletion. Frontend tests cover course creation/switching/reload, mobile sidebar, drawer uploads/removal, loading guards, Markdown/math/code, copy, errors and import choices.
