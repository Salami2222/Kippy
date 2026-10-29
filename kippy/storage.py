"""Single-user SQLite persistence. No dependency on the former application's schema."""
from __future__ import annotations

import json
import sqlite3
import shutil
import logging
import re
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
            # Additive migration: original tables and records remain rollback-readable.
            db.executescript('''
                CREATE TABLE IF NOT EXISTS courses (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, code TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS course_conversations (
                    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS course_materials (
                    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                    material_id TEXT NOT NULL UNIQUE REFERENCES materials(id) ON DELETE CASCADE,
                    PRIMARY KEY (course_id, material_id));
                CREATE TABLE IF NOT EXISTS legacy_imports (
                    kind TEXT NOT NULL, legacy_id TEXT NOT NULL,
                    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                    PRIMARY KEY (kind, legacy_id, course_id));
            ''')
            # Upgrade the former one-conversation-per-course link atomically.
            db.execute('BEGIN IMMEDIATE')
            if any(r['name'] == 'course_id' and r['pk'] for r in db.execute('PRAGMA table_info(course_conversations)')):
                db.execute('ALTER TABLE course_conversations RENAME TO course_conversations_v1')
                db.execute('CREATE TABLE course_conversations (course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE, conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE)')
                db.execute('INSERT INTO course_conversations SELECT course_id,conversation_id FROM course_conversations_v1')
                db.execute("UPDATE conversations SET title='Original Chat' WHERE id IN (SELECT conversation_id FROM course_conversations_v1)")
                db.execute('DROP TABLE course_conversations_v1')
            db.execute('CREATE INDEX IF NOT EXISTS course_conversations_course ON course_conversations(course_id)')
            db.execute("""CREATE TABLE IF NOT EXISTS course_layouts (
                course_id TEXT PRIMARY KEY REFERENCES courses(id) ON DELETE CASCADE,
                mode TEXT NOT NULL DEFAULT 'single', single_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
                left_id TEXT REFERENCES conversations(id) ON DELETE SET NULL, right_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
                active_panel TEXT NOT NULL DEFAULT 'left', split_initialized INTEGER NOT NULL DEFAULT 0)""")
            db.execute("""CREATE TABLE IF NOT EXISTS chat_splits (
                course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE, request_id TEXT NOT NULL,
                PRIMARY KEY(course_id,request_id))""")
            db.execute('CREATE TABLE IF NOT EXISTS chat_creations (course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE, request_id TEXT NOT NULL, conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL, PRIMARY KEY(course_id,request_id))')
            for c in db.execute('SELECT course_id,conversation_id FROM course_conversations ORDER BY rowid').fetchall():
                db.execute('INSERT OR IGNORE INTO course_layouts(course_id,single_id,left_id) VALUES (?,?,?)', (c[0],c[1],c[1]))
            self._recover_course_deletions(db)
            # A crash never leaves an apparently active upload or inference forever.
            db.execute("UPDATE materials SET status='failed', error='Processing interrupted. Remove and upload again.' WHERE status='processing'")
            for row in db.execute("SELECT conversation_id, request_id FROM turns WHERE status='pending'").fetchall():
                message = 'Response interrupted by server restart. Please ask again.'
                result = {'content': message, 'sources': [], 'error': message}
                db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?)', (identifier(), row['conversation_id'], 'assistant', message, '[]', 1, now()))
                db.execute("UPDATE turns SET status='failed', response=? WHERE conversation_id=? AND request_id=?",
                           (json.dumps(result), row['conversation_id'], row['request_id']))


    def _recover_course_deletions(self, db):
        # A crash between staging file removals and committing SQL is reversible.
        for directory in self.uploads.glob('.deleting-*'):
            if directory.is_symlink() or not directory.is_dir() or not re.fullmatch(r'\.deleting-[a-f0-9]{32}', directory.name):
                continue
            try:
                for path in directory.iterdir():
                    if path.is_symlink() or not path.is_file() or not re.fullmatch(r'[a-f0-9]{32}', path.name):
                        continue
                    exists = db.execute('SELECT 1 FROM materials WHERE id=?', (path.name,)).fetchone()
                    if exists:
                        target = self.uploads / path.name
                        if not target.exists():
                            path.replace(target)
                    else:
                        path.unlink()
                if not any(directory.iterdir()):
                    directory.rmdir()
            except OSError:
                logging.warning('Course file deletion recovery pending; files retained in quarantine.')

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
            db.execute("UPDATE conversations SET title=CASE WHEN title='New chat' AND NOT EXISTS (SELECT 1 FROM course_conversations WHERE conversation_id=conversations.id) THEN ? ELSE title END, updated_at=? WHERE id=?", (content[:70], now(), key))
        return None

    def finish_turn(self, key, request_id, content, sources, error=False):
        result = {'content': content, 'sources': sources, 'error': content if error else None}
        with self.connect() as db:
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?)', (identifier(), key, 'assistant', content, json.dumps(sources), int(error), now()))
            db.execute('UPDATE turns SET status=?,response=? WHERE conversation_id=? AND request_id=?',
                       ('failed' if error else 'complete', json.dumps(result), key, request_id))
            db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now(), key))
        return result


    @staticmethod
    def course_fields(data):
        fields = {}
        for key, limit in [('name', 120), ('code', 40), ('description', 600)]:
            value = data.get(key, '')
            if not isinstance(value, str) or len(value.strip()) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
                raise ValueError(f'Invalid course {key} (maximum {limit} characters).')
            fields[key] = value.strip()
        if not fields['name']:
            raise ValueError('A course name is required.')
        return fields

    def courses(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM courses ORDER BY created_at, rowid')]

    def course(self, key, include_conversation=True):
        with self.connect() as db:
            row = db.execute('SELECT * FROM courses WHERE id=?', (key,)).fetchone()
            if not row:
                raise LookupError('Course not found.')
            result = dict(row)
            result['conversations'] = [dict(r) for r in db.execute('SELECT c.* FROM conversations c JOIN course_conversations cc ON cc.conversation_id=c.id WHERE cc.course_id=? ORDER BY c.created_at,c.rowid', (key,))]
            layout = db.execute('SELECT * FROM course_layouts WHERE course_id=?', (key,)).fetchone()
            result['workspace'] = dict(layout) if layout else {'course_id':key,'mode':'single','single_id':None,'left_id':None,'right_id':None,'active_panel':'left','split_initialized':0}
        result['materials'] = self.course_materials(key)
        # Compatibility view for older local clients; inference always validates its target.
        chosen = result['workspace']['single_id'] or (result['conversations'][0]['id'] if result['conversations'] else None)
        result['conversation'] = self.course_conversation(key,chosen) if chosen and include_conversation else None
        return result

    def course_conversation(self, key, chat_id):
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM course_conversations WHERE course_id=? AND conversation_id=?',(key,chat_id)).fetchone():
                raise LookupError('Conversation not found in this course.')
        result = self.conversation(chat_id)
        result['material_ids'] = [m['id'] for m in self.course_materials(key) if m['status']=='ready']
        return result

    @staticmethod
    def chat_name(value):
        if not isinstance(value,str) or not value.strip() or len(value.strip())>120 or any(ord(c)<32 for c in value):
            raise ValueError('Enter a chat name of 1–120 characters without control characters.')
        return value.strip()

    def create_course_chat(self, key, name='New Chat', request_id=None):
        self.course(key);name=self.chat_name(name);chat_id,stamp=identifier(),now()
        if request_id is not None and (not isinstance(request_id,str) or not re.fullmatch(r'[a-f0-9]{32}',request_id)):
            raise ValueError('Invalid chat creation identifier.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT conversation_id FROM chat_creations WHERE course_id=? AND request_id=?',(key,request_id)).fetchone() if request_id else None
            if old:
                if old[0] is None:raise ValueError('This creation request belongs to a deleted chat. Start a new chat explicitly.')
                chat_id=old[0]
            else:
                db.execute('INSERT INTO conversations VALUES (?,?,?,?)',(chat_id,name,stamp,stamp))
                db.execute('INSERT INTO course_conversations VALUES (?,?)',(key,chat_id))
                if request_id:db.execute('INSERT INTO chat_creations VALUES (?,?,?)',(key,request_id,chat_id))
        return self.course_conversation(key,chat_id)

    def rename_course_chat(self,key,chat_id,name):
        self.course_conversation(key,chat_id);name=self.chat_name(name)
        with self.connect() as db:
            db.execute('UPDATE conversations SET title=?,updated_at=? WHERE id=?',(name,now(),chat_id))
        return self.course_conversation(key,chat_id)

    def update_layout(self,key,data):
        course=self.course(key);layout=course['workspace'];ids={c['id'] for c in course['conversations']}
        for field in ('mode','single_id','left_id','right_id','active_panel'):
            if field in data:layout[field]=data[field]
        if layout['mode'] not in ('single','double') or layout['active_panel'] not in ('left','right'):
            raise ValueError('Invalid chat layout.')
        if layout['mode']=='double' and not layout['split_initialized']:
            raise ValueError('Create a fresh independent pair before using Double Chat.')
        for field in ('single_id','left_id','right_id'):
            if layout[field] is not None and (not isinstance(layout[field],str) or layout[field] not in ids):raise ValueError('Choose a chat belonging to this course.')
        if layout['left_id'] and layout['left_id']==layout['right_id']:raise ValueError('Choose different chats for the two panels.')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO course_layouts VALUES (?,?,?,?,?,?,?)',(key,layout['mode'],layout['single_id'],layout['left_id'],layout['right_id'],layout['active_panel'],layout['split_initialized']))
        return self.course(key)['workspace']

    def split_course(self,key,request_id,fresh=False):
        self.course(key)
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-f0-9]{32}',request_id):raise ValueError('Invalid split request identifier.')
        if not isinstance(fresh,bool):raise ValueError('Invalid fresh-pair option.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT 1 FROM chat_splits WHERE course_id=? AND request_id=?',(key,request_id)).fetchone()
            layout=db.execute('SELECT * FROM course_layouts WHERE course_id=?',(key,)).fetchone()
            if not old and (fresh or not layout or not layout['split_initialized']):
                left,right,stamp=identifier(),identifier(),now()
                for cid,title in [(left,'Chat 1'),(right,'Chat 2')]:
                    db.execute('INSERT INTO conversations VALUES (?,?,?,?)',(cid,title,stamp,stamp))
                    db.execute('INSERT INTO course_conversations VALUES (?,?)',(key,cid))
                db.execute("INSERT OR REPLACE INTO course_layouts VALUES (?,'double',?,?,?,'left',1)",(key,left,left,right))
            elif not old:
                db.execute("UPDATE course_layouts SET mode='double' WHERE course_id=?",(key,))
            db.execute('INSERT OR IGNORE INTO chat_splits VALUES (?,?)',(key,request_id))
        return self.course(key)

    def delete_course_chat(self,key,chat_id):
        self.course_conversation(key,chat_id)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM conversations WHERE id=?',(chat_id,))
            layout=dict(db.execute('SELECT * FROM course_layouts WHERE course_id=?',(key,)).fetchone())
            ids=[r[0] for r in db.execute('SELECT c.id FROM conversations c JOIN course_conversations cc ON cc.conversation_id=c.id WHERE cc.course_id=? ORDER BY c.created_at,c.rowid',(key,))]
            if not layout['single_id']:layout['single_id']=next(iter(ids),None)
            if not layout['left_id']:layout['left_id']=next((c for c in ids if c!=layout['right_id']),None)
            if not layout['right_id'] and layout['split_initialized']:layout['right_id']=next((c for c in ids if c!=layout['left_id']),None)
            if not layout[layout['active_panel']+'_id']:layout['active_panel']='left' if layout['left_id'] else 'right'
            db.execute('UPDATE course_layouts SET single_id=?,left_id=?,right_id=?,active_panel=? WHERE course_id=?',(layout['single_id'],layout['left_id'],layout['right_id'],layout['active_panel'],key))
        return self.course(key)

    def course_materials(self, key):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT m.* FROM materials m JOIN course_materials cm
                ON cm.material_id=m.id WHERE cm.course_id=? ORDER BY m.created_at DESC, m.rowid DESC''', (key,))]

    def legacy(self):
        with self.connect() as db:
            conversations = [dict(r) for r in db.execute('''SELECT c.* FROM conversations c WHERE NOT EXISTS
                (SELECT 1 FROM course_conversations cc WHERE cc.conversation_id=c.id) AND NOT EXISTS
                (SELECT 1 FROM legacy_imports li WHERE li.kind='conversation' AND li.legacy_id=c.id)
                ORDER BY c.updated_at DESC''')]
            for conv in conversations:
                conv['material_ids'] = [r[0] for r in db.execute('SELECT material_id FROM selections WHERE conversation_id=?', (conv['id'],))]
            materials = [dict(r) for r in db.execute('''SELECT m.*, EXISTS(SELECT 1 FROM legacy_imports li
                WHERE li.kind='material' AND li.legacy_id=m.id) AS imported FROM materials m
                WHERE NOT EXISTS(SELECT 1 FROM course_materials cm WHERE cm.material_id=m.id) ORDER BY m.created_at DESC''')]
        return {'conversations': conversations, 'materials': materials}

    def create_course(self, data):
        fields = self.course_fields(data)
        old_key = data.get('legacy_conversation_id') or None
        ids = data.get('legacy_material_ids', [])
        if not isinstance(ids, list) or len(ids) > 300 or any(not isinstance(i, str) for i in ids):
            raise ValueError('Choose at most 300 existing files per import.')
        if old_key is not None and not isinstance(old_key, str):
            raise ValueError('Invalid existing conversation.')
        key, conv_id, stamp = identifier(), identifier(), now()
        copied = []
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                old = None
                if old_key:
                    if not any(c['id'] == old_key for c in self.legacy()['conversations']):
                        raise ValueError('This existing conversation was already imported or is unavailable.')
                    old = self.conversation(old_key)
                    ids = [*ids, *old['material_ids']]
                    # Historical cited files travel with their conversation too.
                    ids += [src['material_id'] for m in old['messages'] for src in m['sources']
                            if isinstance(src, dict) and isinstance(src.get('material_id'), str)
                            and db.execute('SELECT 1 FROM materials WHERE id=?', (src['material_id'],)).fetchone()]
                ids = list(dict.fromkeys(ids))
                if len(ids) > 300:
                    raise ValueError('Import fewer files at once (maximum 300).')
                db.execute('INSERT INTO courses VALUES (?,?,?,?,?,?)', (key, fields['name'], fields['code'], fields['description'], stamp, stamp))
                db.execute('INSERT INTO conversations VALUES (?,?,?,?)', (conv_id, 'Original Chat' if old else 'New Chat', stamp, stamp))
                db.execute('INSERT INTO course_conversations VALUES (?,?)', (key, conv_id))
                db.execute('INSERT INTO course_layouts(course_id,single_id,left_id) VALUES (?,?,?)',(key,conv_id,conv_id))
                mapping = {}
                for mid in ids:
                    if not re.fullmatch(r'[a-f0-9]{32}', mid):
                        raise ValueError('Invalid existing material.')
                    row = db.execute('SELECT * FROM materials WHERE id=? AND NOT EXISTS (SELECT 1 FROM course_materials WHERE material_id=?)', (mid, mid)).fetchone()
                    if not row:
                        raise ValueError('Only original unassigned files can be imported.')
                    new_id = identifier(); mapping[mid] = new_id
                    if (self.uploads / mid).exists():
                        copied.append(self.uploads / new_id)
                        shutil.copyfile(self.uploads / mid, self.uploads / new_id)
                    elif row['status'] == 'ready':
                        raise ValueError(f"Original file missing: {row['name']}. Its existing records remain preserved.")
                    db.execute('INSERT INTO materials VALUES (?,?,?,?,?,?,?,?)', (new_id, row['name'], row['type'], row['size'], row['status'], row['warning'], row['error'], row['created_at']))
                    db.execute('INSERT INTO course_materials VALUES (?,?)', (key, new_id))
                    db.execute('INSERT INTO chunks(material_id,ordinal,location,text) SELECT ?,ordinal,location,text FROM chunks WHERE material_id=?', (new_id, mid))
                    db.execute("INSERT INTO legacy_imports VALUES ('material',?,?)", (mid, key))
                if old:
                    for m in old['messages']:
                        refs = [{**src, 'material_id': mapping.get(src.get('material_id'), src.get('material_id'))} for src in m['sources']]
                        db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?)', (identifier(), conv_id, m['role'], m['content'], json.dumps(refs), m['error'], m['created_at']))
                    db.execute("INSERT INTO legacy_imports VALUES ('conversation',?,?)", (old_key, key))
        except Exception:
            for path in copied:
                path.unlink(missing_ok=True)
            raise
        return self.course(key)

    def update_course(self, key, data):
        existing = self.course(key)
        fields = self.course_fields({**existing, **data})
        with self.connect() as db:
            db.execute('UPDATE courses SET name=?,code=?,description=?,updated_at=? WHERE id=?',
                       (fields['name'], fields['code'], fields['description'], now(), key))
        return self.course(key)

    def add_course_material(self, key, material_id):
        self.course(key)
        with self.connect() as db:
            db.execute('INSERT INTO course_materials VALUES (?,?)', (key, material_id))

    def course_chunks(self, key):
        # Scope is enforced by the SQL join, regardless of any client-supplied IDs.
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT ch.*,m.name FROM chunks ch JOIN materials m ON m.id=ch.material_id
                JOIN course_materials cm ON cm.material_id=m.id
                WHERE cm.course_id=? AND m.status='ready' ORDER BY m.created_at,m.id,ch.ordinal''', (key,))]

    def delete_course_material(self, key, mid):
        self.course(key)
        if not any(m['id'] == mid for m in self.course_materials(key)):
            raise LookupError('Material not found in this course.')
        self.delete_material(mid)

    def delete_course(self, key):
        course = self.course(key)
        trash = self.uploads / ('.deleting-' + identifier())
        moved = []
        try:
            trash.mkdir()
            for m in course['materials']:
                path = self.uploads / m['id']
                if path.exists():
                    target = trash / m['id']; path.replace(target); moved.append((path, target))
            with self.connect() as db:
                db.executemany('DELETE FROM conversations WHERE id=?', [(c['id'],) for c in course['conversations']])
                db.executemany('DELETE FROM materials WHERE id=?', [(m['id'],) for m in course['materials']])
                db.execute('DELETE FROM courses WHERE id=?', (key,))
        except Exception:
            for path, target in reversed(moved):
                target.replace(path)
            if trash.exists():
                trash.rmdir()
            raise
        try:
            shutil.rmtree(trash)
        except OSError:
            logging.warning('Course deleted; quarantined file cleanup pending in uploads.')
