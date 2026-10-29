"""Loopback HTTP application, compatible with the existing Tailscale reverse proxy."""
from __future__ import annotations

import functools
import json
import logging
import mimetypes
import os
import re
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import documents, study
from .ollama import OllamaClient, OllamaError
from .storage import Store

STATIC = Path(__file__).parent / 'static'
ID = re.compile(r'^[a-f0-9]{32}$')


def valid_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('Invalid identifier.')
    return value


def allowed_request_origin(headers, peer_ip):
    origin = headers.get('Origin')
    if not origin:
        return True  # Local command-line clients still need the custom write header.
    host = headers.get('Host', '')
    if origin == f'http://{host}':
        return True
    try:
        parsed = urlsplit(origin)
        if (peer_ip not in {'127.0.0.1', '::1', '::ffff:127.0.0.1'} or parsed.scheme != 'https'
                or not parsed.hostname or not parsed.hostname.endswith('.ts.net') or parsed.port is not None
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            return False
        if host.lower() == parsed.netloc.lower():
            return True
        forwarded = headers.get('X-Forwarded-Host')
        proto = headers.get('X-Forwarded-Proto', '').lower()
        if forwarded:
            return proto == 'https' and forwarded.lower() == parsed.netloc.lower()
        return urlsplit('http://' + host).hostname in {'localhost', '127.0.0.1', '::1'} and proto in {'', 'https'}
    except ValueError:
        return False


def allowed_host(host):
    try:
        parsed = urlsplit('http://' + host)
        _ = parsed.port
        return bool(parsed.hostname and (parsed.hostname in {'localhost', '127.0.0.1', '::1'} or parsed.hostname.endswith('.ts.net'))
                    and not parsed.username and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


class App:
    def __init__(self, directory=None, client=None, max_upload_bytes=None):
        directory = directory or os.getenv('KIPPY_DATA_DIR') or Path.home() / '.kippy'
        self.store = Store(directory)
        self.client = client or OllamaClient()
        self.max_upload_bytes = max_upload_bytes or int(os.getenv('KIPPY_MAX_UPLOAD_MB', '25')) * 1024 * 1024
        if not 1 <= self.max_upload_bytes <= 100 * 1024 * 1024:
            raise ValueError('Upload limits must be between 1 byte and 100 MiB.')
        self.lock = threading.Lock()  # Queue inference through one model; serialize lifecycle/document mutations safely.
        try:
            commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=Path(__file__).parent.parent, stderr=subprocess.DEVNULL, text=True).strip()
        except (OSError, subprocess.SubprocessError):
            commit = 'unknown'
        self.version = {'commit': commit, 'pid': os.getpid(), 'started_at': time.time(), 'application': 'Kippy'}

    def chat(self, data):
        # HTTP requests may wait here independently; frontend drafts remain editable.
        with self.lock:
            return self._chat(data)

    def _chat(self, data):
        course = self.store.course(valid_id(data.get('course_id')), include_conversation=False)
        key = data.get('conversation_id')
        if key is None:
            raise ValueError('Choose a saved chat. Reload Kippy after an application upgrade.')
        key = valid_id(key)
        self.store.course_conversation(course['id'],key)
        request_id = valid_id(data.get('request_id'))
        message = data.get('message')
        if not isinstance(message, str) or not message.strip() or len(message) > 8000:
            raise ValueError('Enter a message of 1–8,000 characters.')
        previous = self.store.begin_turn(key, request_id, message.strip())
        if previous is not None:
            return previous
        conv = self.store.conversation(key)
        chunks = self.store.course_chunks(course['id'])
        names = [m['name'] for m in course['materials'] if m['status'] == 'ready']
        try:
            content, sources = study.answer(self.client, message.strip(), conv['messages'][:-1], chunks, names, course)
            return self.store.finish_turn(key, request_id, content, sources)
        except OllamaError as exc:
            return self.store.finish_turn(key, request_id, str(exc), [], error=True)
        except Exception:
            logging.exception('Study response failed')
            return self.store.finish_turn(key, request_id, 'Kippy could not finish this response. Your message and materials are saved. Please try again.', [], error=True)


class Handler(BaseHTTPRequestHandler):
    server_version = 'Kippy'

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        # Avoid request paths containing potentially private filenames in access logs.
        pass

    def reply(self, status, data, mime='application/json; charset=utf-8'):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8') if mime.startswith('application/json') else data
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if not allowed_host(self.headers.get('Host', '')):
            return self.reply(421, {'error': 'Use localhost or the configured Tailscale address.'})
        route = urlsplit(self.path).path
        try:
            if route == '/api/version':
                return self.reply(200, self.app.version)
            if route == '/api/status':
                return self.reply(200, {'state': 'ready', 'application': 'Kippy', 'max_upload_bytes': self.app.max_upload_bytes})
            if route == '/api/ollama/status':
                return self.reply(200, self.app.client.status())
            if route == '/api/courses':
                return self.reply(200, {'courses': self.app.store.courses()})
            if route == '/api/legacy':
                return self.reply(200, self.app.store.legacy())
            parts = route.strip('/').split('/')
            if len(parts) == 5 and parts[:2] == ['api','courses'] and parts[3]=='conversations':
                return self.reply(200,self.app.store.course_conversation(valid_id(parts[2]),valid_id(parts[4])))
            if len(parts) == 3 and parts[:2] == ['api', 'courses']:
                return self.reply(200, self.app.store.course(valid_id(parts[2])))
            if route.startswith('/api/'):
                return self.reply(404, {'error': 'Endpoint not found.'})
            relative = 'index.html' if route == '/' else unquote(route).lstrip('/')
            path = (STATIC / relative).resolve()
            if not path.is_relative_to(STATIC.resolve()) or not path.is_file():
                return self.reply(404, {'error': 'File not found.'})
            mime = {'.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.woff2': 'font/woff2', '.ttf': 'font/ttf'}.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            return self.reply(200, path.read_bytes(), mime + ('; charset=utf-8' if mime.startswith('text/') or path.suffix == '.js' else ''))
        except LookupError as exc:
            self.reply(404, {'error': str(exc)})
        except ValueError as exc:
            self.reply(400, {'error': str(exc)})

    def do_POST(self):
        self.mutate('POST')

    def do_DELETE(self):
        self.mutate('DELETE')

    def mutate(self, method):
        if not allowed_host(self.headers.get('Host', '')):
            return self.reply(421, {'error': 'Use localhost or the configured Tailscale address.'})
        if self.headers.get('X-Kippy-Request') != '1' or not allowed_request_origin(self.headers, self.client_address[0]):
            return self.reply(403, {'error': 'This request must come from the Kippy application.'})
        is_chat = method=='POST' and urlsplit(self.path).path=='/api/chat'
        locked = not is_chat and self.app.lock.acquire(blocking=False)
        if not is_chat and not locked:
            return self.reply(409, {'error': 'Another response or file change is in progress. Please wait and try again.'})
        try:
            parsed = urlsplit(self.path)
            route = parsed.path
            parts = route.strip('/').split('/')
            course_path = len(parts) >= 3 and parts[:2] == ['api', 'courses']
            course_id = valid_id(parts[2]) if course_path else None
            upload_path = course_path and len(parts) == 4 and parts[3] == 'materials'
            if course_id:
                self.app.store.course(course_id)
            if method == 'DELETE' and course_path and len(parts) == 5 and parts[3] == 'materials':
                self.app.store.delete_course_material(course_id, valid_id(parts[4]))
                return self.reply(200, {'deleted': True})
            chat_path = course_path and len(parts)==5 and parts[3]=='conversations'
            if method == 'DELETE' and not (course_path and (len(parts) == 3 or chat_path)):
                return self.reply(404, {'error': 'Endpoint not found.'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                raise ValueError('Invalid request length.')
            limit = self.app.max_upload_bytes if upload_path else 65536
            if length < 1 or length > limit:
                return self.reply(413, {'error': 'Request is empty or exceeds the configured size limit.'})
            self.connection.settimeout(30)
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError('The upload was interrupted; please try again.')
            if upload_path:
                if self.headers.get_content_type() != 'application/octet-stream':
                    raise ValueError('Upload files as application/octet-stream.')
                filename = parse_qs(parsed.query).get('filename', [''])[0]
                return self.reply(201, documents.upload(self.app.store, filename, body, self.app.max_upload_bytes, course_id))
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('Expected application/json.')
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object.')
            if chat_path:
                chat_id=valid_id(parts[4])
                if method=='DELETE':
                    if data.get('confirm_conversation_id')!=chat_id:raise ValueError('Confirm deletion of this chat and its history.')
                    course=self.app.store.delete_course_chat(course_id,chat_id)
                    return self.reply(200,{'deleted':True,'course':course})
                return self.reply(200,self.app.store.rename_course_chat(course_id,chat_id,data.get('name')))
            if method == 'DELETE':
                if data.get('confirm_course_id') != course_id:
                    raise ValueError('Confirm deletion of this course, its conversation, and all its materials.')
                self.app.store.delete_course(course_id)
                return self.reply(200, {'deleted': True})
            if route == '/api/courses':
                return self.reply(201, self.app.store.create_course(data))
            if course_path and len(parts)==4 and parts[3]=='conversations':
                return self.reply(201,self.app.store.create_course_chat(course_id,data.get('name','New Chat'),data.get('request_id')))
            if course_path and len(parts)==4 and parts[3]=='layout':
                return self.reply(200,self.app.store.update_layout(course_id,data))
            if course_path and len(parts)==4 and parts[3]=='split':
                return self.reply(200,self.app.store.split_course(course_id,data.get('request_id'),data.get('fresh',False)))
            if course_path and len(parts) == 3:
                return self.reply(200, self.app.store.update_course(course_id, data))
            if route == '/api/chat':
                return self.reply(200, self.app.chat(data))
            return self.reply(404, {'error': 'Endpoint not found.'})
        except LookupError as exc:
            self.reply(404, {'error': str(exc)})
        except (ValueError, UnicodeError) as exc:
            self.reply(400, {'error': str(exc)[:400]})
        except Exception:
            logging.exception('Kippy request failed')
            self.reply(500, {'error': 'Kippy could not complete this request. Please try again.'})
        finally:
            if locked:self.app.lock.release()


def make_server(port=8000, directory=None, client=None):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.app = App(directory, client)
    return server


def serve(port=8000, data_dir=None, open_browser=True):
    server = make_server(port, data_dir)
    url = f'http://127.0.0.1:{server.server_port}/'
    print(f'Kippy: {url}', flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
