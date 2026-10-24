"""Single-user SQLite persistence. No dependency on the former application's schema."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier():
    return uuid4().hex


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.uploads = self.directory / 'uploads'
        self.uploads.mkdir(exist_ok=True)
        self.path = self.directory / 'kippy.sqlite3'
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS materials (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, size INTEGER NOT NULL,
                    status TEXT NOT NULL, warning TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY, material_id TEXT NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL, location TEXT NOT NULL, text TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS selections (
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    material_id TEXT NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
                    PRIMARY KEY (conversation_id, material_id));
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]',
                    error INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS turns (
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL, status TEXT NOT NULL, response TEXT,
                    PRIMARY KEY (conversation_id, request_id));
            ''')
            # A crash never leaves an apparently active upload or inference forever.
            db.execute("UPDATE materials SET status='failed', error='Processing interrupted. Remove and upload again.' WHERE status='processing'")
            for row in db.execute("SELECT conversation_id, request_id FROM turns WHERE status='pending'").fetchall():
                message = 'Response interrupted by server restart. Please ask again.'
                result = {'content': message, 'sources': [], 'error': message}
                db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?)', (identifier(), row['conversation_id'], 'assistant', message, '[]', 1, now()))
                db.execute("UPDATE turns SET status='failed', response=? WHERE conversation_id=? AND request_id=?",
                           (json.dumps(result), row['conversation_id'], row['request_id']))


    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def conversation(self, key):
        with self.connect() as db:
            row = db.execute('SELECT * FROM conversations WHERE id=?', (key,)).fetchone()
            if not row:
                raise LookupError('Conversation not found.')
            result = dict(row)
            result['messages'] = [{**dict(m), 'sources': json.loads(m['sources'])} for m in db.execute(
                'SELECT * FROM messages WHERE conversation_id=? ORDER BY rowid', (key,))]
            result['material_ids'] = [m[0] for m in db.execute('SELECT material_id FROM selections WHERE conversation_id=? ORDER BY rowid', (key,))]
            return result

    def conversations(self):
        with self.connect() as db:
            return [dict(x) for x in db.execute('SELECT * FROM conversations ORDER BY updated_at DESC, rowid DESC')]

    def create_conversation(self):
        key, stamp = identifier(), now()
        with self.connect() as db:
            db.execute('INSERT INTO conversations VALUES (?,?,?,?)', (key, 'New chat', stamp, stamp))
        return self.conversation(key)

    def delete_conversation(self, key):
        self.conversation(key)
        with self.connect() as db:
            db.execute('DELETE FROM conversations WHERE id=?', (key,))

    def select(self, key, ids):
        self.conversation(key)
        if not isinstance(ids, list) or len(ids) > 20 or any(not isinstance(i, str) for i in ids):
            raise ValueError('Select at most 20 materials.')
        with self.connect() as db:
            for mid in ids:
                if not db.execute("SELECT id FROM materials WHERE id=? AND status='ready'", (mid,)).fetchone():
                    raise ValueError('Only ready materials can be selected.')
            db.execute('DELETE FROM selections WHERE conversation_id=?', (key,))
            db.executemany('INSERT INTO selections VALUES (?,?)', [(key, mid) for mid in dict.fromkeys(ids)])
        return self.conversation(key)

    def materials(self):
        with self.connect() as db:
            return [dict(x) for x in db.execute('SELECT * FROM materials ORDER BY created_at DESC')]

    def material(self, key):
        with self.connect() as db:
            row = db.execute('SELECT * FROM materials WHERE id=?', (key,)).fetchone()
            if not row:
                raise LookupError('Material not found.')
            return dict(row)

    def delete_material(self, key):
        self.material(key)
        # Remove bytes first: a filesystem failure must remain visible and retryable.
        (self.uploads / key).unlink(missing_ok=True)
        with self.connect() as db:
            db.execute('DELETE FROM materials WHERE id=?', (key,))

    def chunks(self, ids):
        if not ids:
            return []
        with self.connect() as db:
            return [dict(row) for row in db.execute('''SELECT chunks.*, materials.name FROM chunks JOIN materials
                ON materials.id=chunks.material_id WHERE material_id IN (''' + ','.join('?' for _ in ids) + ') ORDER BY material_id, ordinal', ids)]

    def begin_turn(self, key, request_id, content):
        self.conversation(key)
        with self.connect() as db:
            previous = db.execute('SELECT * FROM turns WHERE conversation_id=? AND request_id=?', (key, request_id)).fetchone()
            if previous:
                if previous['status'] == 'pending':
                    raise ValueError('This response is already being generated.')
                return json.loads(previous['response'])
            db.execute('INSERT INTO turns VALUES (?,?,?,NULL)', (key, request_id, 'pending'))
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?)', (identifier(), key, 'user', content, '[]', 0, now()))
            db.execute("UPDATE conversations SET title=CASE WHEN title='New chat' THEN ? ELSE title END, updated_at=? WHERE id=?", (content[:70], now(), key))
        return None

    def finish_turn(self, key, request_id, content, sources, error=False):
        result = {'content': content, 'sources': sources, 'error': content if error else None}
        with self.connect() as db:
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?)', (identifier(), key, 'assistant', content, json.dumps(sources), int(error), now()))
            db.execute('UPDATE turns SET status=?,response=? WHERE conversation_id=? AND request_id=?',
                       ('failed' if error else 'complete', json.dumps(result), key, request_id))
            db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now(), key))
        return result
