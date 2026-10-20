"""Dependency-free, localhost-only web inspector for saved UMD nutrition JSON."""

from __future__ import annotations

import json
import threading
import time
import uuid
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .umd import HALLS, retrieve, save_json
from .agent import normalize_intent, resolve
from .ollama import OllamaClient, OllamaError


STATIC_DIR = Path(__file__).with_name("static")
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
UNAVAILABLE_MESSAGE = "UMD states nutrition information is not available for this recipe"


class ChatError(RuntimeError):
    def __init__(self, message: str, debug: dict, status: int) -> None:
        super().__init__(message)
        self.debug = debug
        self.status = status


class DataApp:
    def __init__(self, data_dir: Path, ollama: OllamaClient | None = None) -> None:
        self.data_dir = data_dir
        self.ollama = ollama or OllamaClient()
        self.lock = threading.Lock()
        self.status = {"state": "idle", "message": "", "date": None}
        self.conversations: dict[str, dict] = {}

    def chat(self, message: str, conversation_id: str | None) -> dict:
        started = time.perf_counter()
        data = self.datasets()
        debug = {"user_request": message, "model": getattr(self.ollama, "model", None),
                 "model_interpretation": None, "resolved_foods": [], "matches": [],
                 "calculations": [], "total": None, "warnings": [], "error": None,
                 "ai_response_time_ms": None, "response_time_ms": None}
        if not data["datasets"]:
            return {"conversation_id": None, "kind": "needs_data",
                    "message": "No saved UMD menus yet. Open Browse UMD data and refresh today's menu first.",
                    "cards": [], "total": None, "options": [],
                    "debug": {**debug, "warnings": ["No saved UMD menu data."],
                              "response_time_ms": round((time.perf_counter() - started) * 1000)}}
        with self.lock:
            session = self.conversations.get(conversation_id or "", {"foods": [], "question": ""}).copy()
        stage = "Ollama interpretation"
        ai_started = time.perf_counter()
        try:
            try:
                intent = self.ollama.interpret(message, session["foods"], data["today"],
                                               [item["menu_date"] for item in data["datasets"]], session["question"])
            finally:
                debug["ai_response_time_ms"] = round((time.perf_counter() - ai_started) * 1000)
            debug["model_interpretation"] = {"action": intent["action"], "foods": intent["foods"],
                                             "question": intent.get("question", "")}
            stage = "request validation"
            if intent["action"] == "clarify":
                result = {"kind": "clarification", "message": intent.get("question") or "Which food do you mean?",
                          "cards": [], "total": None, "options": []}
                requested = session["foods"]
                debug["warnings"].append("The model requested clarification before a UMD label could be selected.")
            else:
                requested = normalize_intent(intent, session["foods"])
                stage = "UMD matching"
                result = resolve(requested, data["datasets"], data["today"])
            debug["resolved_foods"] = requested
            debug.update(result.pop("debug", {}))
        except (OllamaError, ValueError) as exc:
            debug["error"] = {"stage": stage, "message": str(exc)}
            debug["response_time_ms"] = round((time.perf_counter() - started) * 1000)
            raise ChatError(str(exc), debug, 503 if isinstance(exc, OllamaError) else 400) from exc
        session = {"foods": requested, "question": result["message"] if result["kind"] == "clarification" else ""}
        with self.lock:
            if not conversation_id or conversation_id not in self.conversations:
                conversation_id = uuid.uuid4().hex
            self.conversations[conversation_id] = session
            if len(self.conversations) > 100:
                self.conversations.pop(next(iter(self.conversations)))
        debug["response_time_ms"] = round((time.perf_counter() - started) * 1000)
        return {"conversation_id": conversation_id, **result, "debug": debug}

    def datasets(self) -> dict:
        by_date: dict[str, tuple[float, dict]] = {}
        load_errors: list[str] = []
        for path in sorted(self.data_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if (data.get("schema_version") != 1 or data.get("source") != "UMD Dining Services"
                        or not isinstance(data.get("foods"), list) or not isinstance(data.get("errors"), list)):
                    raise ValueError("unsupported UMD JSON format")
                menu_date = date.fromisoformat(data["menu_date"]).isoformat()
                retrieved_at = datetime.fromisoformat(data["retrieved_at"])
                if retrieved_at.tzinfo is None:
                    raise ValueError("retrieval timestamp has no timezone")
                retrieved = retrieved_at.timestamp()
                if menu_date not in by_date or retrieved > by_date[menu_date][0]:
                    by_date[menu_date] = (retrieved, data)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                load_errors.append(f"{path.name}: {exc}")
        return {
            "today": datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
            "datasets": [by_date[key][1] for key in sorted(by_date, reverse=True)],
            "load_errors": load_errors,
        }

    def get_status(self) -> dict:
        with self.lock:
            return self.status.copy()

    def start_refresh(self, menu_date: date) -> bool:
        with self.lock:
            if self.status["state"] == "running":
                return False
            self.status = {"state": "running", "message": "Retrieving all three UMD dining halls. This may take several minutes.", "date": menu_date.isoformat()}
        threading.Thread(target=self._refresh, args=(menu_date,), daemon=True).start()
        return True

    def _refresh(self, menu_date: date) -> None:
        try:
            result = retrieve(menu_date, list(HALLS))
            errors = result["errors"]
            serious = [error for error in errors if error.get("error") != UNAVAILABLE_MESSAGE]
            found_halls = {food["dining_hall"] for food in result["foods"]}
            if serious or len(found_halls) != len(HALLS):
                detail = serious[0].get("error", "Incomplete menu") if serious else "One or more halls returned no menu items"
                raise RuntimeError(f"UMD retrieval was incomplete: {detail}. Previous saved data was kept.")
            output = self.data_dir / f"umd-{menu_date.isoformat()}.json"
            save_json(result, output)
            if errors:
                state = "warning"
                message = (f"Saved {len(result['foods']):,} menu appearances. "
                           f"{len(errors)} official labels say nutrition is unavailable; their values remain blank.")
            else:
                state = "success"
                message = f"Saved {len(result['foods']):,} menu appearances from all three halls."
        except Exception as exc:
            state = "error"
            message = str(exc)
        with self.lock:
            self.status = {"state": state, "message": message, "date": menu_date.isoformat()}


class AppHandler(BaseHTTPRequestHandler):
    app: DataApp

    def _send(self, status: int, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route == "/api/datasets":
            self._json(200, self.app.datasets())
        elif route == "/api/status":
            self._json(200, self.app.get_status())
        elif route == "/api/ollama/status":
            self._json(200, self.app.ollama.status())
        elif route in ("/", "/app.css", "/app.js", "/tracker.js"):
            filename = {"/": "index.html", "/app.css": "app.css", "/app.js": "app.js", "/tracker.js": "tracker.js"}[route]
            media_type = {"index.html": "text/html; charset=utf-8", "app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "tracker.js": "text/javascript; charset=utf-8"}[filename]
            try:
                self._send(200, (STATIC_DIR / filename).read_bytes(), media_type)
            except OSError:
                self._json(500, {"error": "Interface file is missing"})
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        route = urlsplit(self.path).path
        if route not in ("/api/refresh", "/api/chat"):
            self._json(404, {"error": "Not found"})
            return
        origin = self.headers.get("Origin")
        expected = f"http://{self.headers.get('Host')}"
        kind = "refresh" if route == "/api/refresh" else "chat"
        if (origin and origin != expected) or self.headers.get("X-MacroMellow-Request") != kind:
            self._json(403, {"error": "This action is only available from the local interface"})
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self._json(415, {"error": "Expected JSON request"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= (256 if kind == "refresh" else 4096):
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Expected an object")
            if route == "/api/chat":
                message = payload["message"]
                conversation_id = payload.get("conversation_id")
                if (not isinstance(message, str) or not 1 <= len(message.strip()) <= 1000
                        or (conversation_id is not None and (not isinstance(conversation_id, str)
                                                               or len(conversation_id) != 32))):
                    raise ValueError("Invalid chat request")
                try:
                    self._json(200, self.app.chat(message.strip(), conversation_id))
                except ChatError as exc:
                    self._json(exc.status, {"error": str(exc), "debug": exc.debug})
                return
            menu_date = date.fromisoformat(payload["date"])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            self._json(400, {"error": "Choose a valid menu date" if kind == "refresh" else "Enter a valid message"})
            return
        today = datetime.now(ZoneInfo("America/New_York")).date()
        if not 0 <= (menu_date - today).days <= 6:
            self._json(400, {"error": "UMD currently offers menus for today through the next six days"})
            return
        if not self.app.start_refresh(menu_date):
            self._json(409, {"error": "A UMD refresh is already running"})
            return
        self._json(202, self.app.get_status())

    def log_message(self, format: str, *args: object) -> None:
        if not (len(args) >= 2 and args[0] == "GET /api/status HTTP/1.1" and args[1] == "200"):
            super().log_message(format, *args)


def serve(port: int, data_dir: Path | None = None, open_browser: bool = True) -> int:
    app = DataApp((data_dir or DEFAULT_DATA_DIR).resolve())
    handler = type("MacroMellowHandler", (AppHandler,), {"app": app})
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as exc:
        print(f"Could not start local server on port {port}: {exc}")
        return 1
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"MacroMellow is running at {url}", flush=True)
    print(f"Reading UMD JSON files from {app.data_dir}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
