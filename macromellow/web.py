"""Local web app for UMD data, the nutrition agent, and SQLite memory."""

from __future__ import annotations

import json
import hashlib
import re
import threading
import time
import uuid
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from zoneinfo import ZoneInfo

from .umd import HALLS, retrieve, save_json
from .agent import normalize_intent, resolve, tokens
from .ollama import OllamaClient, OllamaError, validate_response
from .memory import MemoryStore, numbers
from .conversation import Conversations


STATIC_DIR = Path(__file__).with_name("static")
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
UNAVAILABLE_MESSAGE = "UMD states nutrition information is not available for this recipe"


def validate_adjustment(message: str, intent: dict, today: str) -> tuple[str, dict]:
    command = re.match(r"^(?:please\s+)?(add|subtract|remove|set)\b", message.strip(), re.I)
    if not command or "?" in message or re.search(r"\b(not|don't|never|if|unless|yesterday|tomorrow|goal|goals)\b", message, re.I):
        raise ValueError("Please explicitly say add, subtract, or set today's macro amounts, for example: Add 35g of protein.")
    mode = {"remove": "subtract"}.get(command[1].lower(), command[1].lower())
    if (intent.get("adjustment_mode") != mode or intent.get("log_date", "") not in {"", today}
            or any(day != today for day in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", message))):
        raise ValueError("Please clarify the adjustment mode and today's amounts.")
    raw = intent.get("adjustment_values")
    if not isinstance(raw, dict) or set(raw) - {"calories", "protein_g", "carbohydrates_g", "fat_g"}:
        raise ValueError("Invalid macro adjustment.")
    values = numbers({key: value for key, value in raw.items() if value is not None}, nonnegative=True)
    found = {}
    for match in re.finditer(r"(?<![\w.\-])([0-9]+(?:\.[0-9]+)?)\s*(?:(?:g|grams?)\s*(?:of\s+)?)?(calories|kcal|protein|carbs|carbohydrates|fat)\b", message, re.I):
        key = {"calories": "calories", "kcal": "calories", "protein": "protein_g", "carbs": "carbohydrates_g",
               "carbohydrates": "carbohydrates_g", "fat": "fat_g"}[match[2].lower()]
        if key in found:
            raise ValueError("Please specify each macro amount once.")
        found[key] = round(float(match[1]), 2)
    if found != values:
        raise ValueError("Please give literal amounts such as 35g protein or 200 calories. I won't guess adjustment values.")
    return mode, values



def allowed_request_origin(headers, peer_ip: str) -> bool:
    """Check browser Origin for local HTTP or HTTPS through Tailscale Serve.

    Serve terminates HTTPS and forwards to our loopback-only HTTP listener.
    Trust its X-Forwarded-Host/Proto only when the actual TCP peer is loopback.
    Never allow arbitrary external origins or infer authorization from an
    unverified X-Forwarded-* header.
    """
    origin = headers.get("Origin")
    if not origin:
        return True  # Retain existing behavior for local CLI / non-browser requests.
    host = headers.get("Host", "")
    if origin == f"http://{host}" and host:
        return True
    if peer_ip not in {"127.0.0.1", "::1"}:
        return False
    try:
        parsed = urlsplit(origin)
        return (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.hostname.endswith(".ts.net")
            and parsed.hostname != "ts.net"
            and parsed.port is None
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and (
                host.lower() == parsed.netloc.lower()
                or (
                    headers.get("X-Forwarded-Proto", "").lower() == "https"
                    and headers.get("X-Forwarded-Host", "").lower() == parsed.netloc.lower()
                )
            )
        )
    except ValueError:
        return False


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
        self.memory = MemoryStore(data_dir / "macromellow.sqlite3")
        self.conversations = Conversations(self)

    def _meal_target(self, target: str, session: dict) -> dict | None:
        active = [meal for meal in self.memory.meals(30) if meal["status"] == "active"]
        if target and len(target) == 32:
            return next((meal for meal in active if meal["id"] == target), None)
        if target and target.lower() not in {"last", "latest", "recent", "this", "that"}:
            matches = [meal for meal in active if target.lower() in meal["label"].lower()
                       or any(target.lower() in card["name"].lower() for card in meal.get("cards", []))]
            return matches[0] if len(matches) == 1 else None
        return next((meal for meal in active if meal["id"] == session.get("last_logged_id")), None)

    @staticmethod
    def _safe_to_log(result: dict, requested: list[dict], day: str) -> str | None:
        if result["kind"] != "result" or not result.get("cards") or not result.get("total"):
            return "I need a reliable UMD match for every food before logging it."
        if any(result["total"].get(key) is None for key in ("calories", "protein_g", "carbohydrates_g", "fat_g")):
            return "UMD nutrition is incomplete, so I cannot add this meal automatically."
        if any(card["menu_date"] != day for card in result["cards"]):
            return "The saved menu date does not match the day you want to log. Which date should I use?"
        for card, item in zip(result["cards"], requested):
            if card["match"] != "official name":
                return f"Please confirm the exact UMD food name for {item['name']} before I log it."
            if not card.get("serving_size"):
                return f"UMD has no serving size for {card['name']}; please clarify the portion."
            if item["unit"].lower() not in {"serving", "servings"}:
                if not (item["unit"].lower() in {"each", "item", "items"}
                        and re.match(r"^1\s+(each|ea|item)\b", card["serving_size"].lower())):
                    return f"How many listed UMD servings of {card['name']} should I log?"
        return None

    def chat(self, message: str, conversation_id: str | None) -> dict:
        started = time.perf_counter()
        data = self.datasets()
        debug = {"user_request": message, "model": getattr(self.ollama, "model", None),
                 "model_interpretation": None, "resolved_foods": [], "matches": [],
                 "calculations": [], "total": None, "warnings": [], "error": None,
                 "ai_response_time_ms": None, "response_time_ms": None, "tool_action": None}
        session = self.memory.conversation(conversation_id)
        if not conversation_id:
            conversation_id = uuid.uuid4().hex
        memories = self.memory.memories()
        recent_meals = [{"id": meal["id"], "date": meal["date"], "label": meal["label"],
                         "foods": [card["name"] for card in meal.get("cards", [])]}
                        for meal in self.memory.meals(12) if meal["status"] == "active"]
        stage = "Ollama interpretation"
        ai_started = time.perf_counter()
        try:
            try:
                intent = self.ollama.interpret(
                    message, session["foods"], data["today"],
                    [item["menu_date"] for item in data["datasets"]], session["question"],
                    session.get("recent", []),
                    [{"category": memory["category"], "key": memory["key"], "value": memory["value"]}
                     for memory in memories], recent_meals)
            finally:
                debug["ai_response_time_ms"] = round((time.perf_counter() - ai_started) * 1000)
            validate_response(intent)
            operation = intent.get("operation", "lookup")
            debug["model_interpretation"] = intent
            stage = "request validation"
            requested = session["foods"]
            if intent["action"] == "clarify" or intent.get("response_type") == "clarification":
                result = {"kind": "clarification", "message": intent.get("question") or intent.get("response_text") or "Could you clarify what you would like me to do?",
                          "cards": [], "total": None, "options": []}
                debug["warnings"].append("The model requested clarification; no action was executed.")
            elif operation == "converse":
                response_type = intent["response_type"]
                text = intent.get("response_text", "").strip()
                result = {"kind": "conversation",
                          "response_type": response_type, "message": text if response_type != "acknowledgment" else "",
                          "cards": [], "total": None}
            elif operation == "adjust":
                stage = "manual macro adjustment"
                mode, values = validate_adjustment(message, intent, self.memory.today())
                snapshot = self.memory.adjust(mode, values)
                result = {"kind": "adjusted", "response_type": "action", "acknowledgment_only": True,
                          "message": "Updated today's editable macro totals.", "cards": [], "total": None,
                          "tracker": snapshot}
                debug["tool_action"] = {"operation": "adjust", "mode": mode, "values": values,
                                        "verified_totals": snapshot["totals"]}
            elif operation in {"today", "history", "recall"}:
                if operation == "today":
                    tracker = self.memory.tracker()
                    result = {"kind": "tracker", "message": "Here are today's logged totals and goals.",
                              "cards": [], "total": tracker["totals"], "tracker": tracker}
                elif operation == "history":
                    meals = self.memory.meals()
                    requested_day = intent.get("log_date", "")
                    if requested_day:
                        try:
                            requested_day = date.fromisoformat(requested_day).isoformat()
                        except ValueError:
                            raise ValueError("The requested meal history date is invalid.")
                        meals = [meal for meal in meals if meal["date"] == requested_day]
                    result = {"kind": "history", "message": f"I found {len(meals)} recent logged meal(s).",
                              "cards": [], "total": None, "meals": meals}
                else:
                    result = {"kind": "memories", "message": f"I found {len(memories)} saved personal memory item(s).",
                              "cards": [], "total": None, "memories": memories}
                debug["tool_action"] = operation
            elif operation in {"remember", "forget"}:
                stage = "personal memory"
                if operation == "remember" and re.search(r"\b(remember|save|favorite|prefer|like)\b", message, re.I):
                    value = intent.get("memory_value", "")
                    previous_result = self.memory.result(session.get("last_result_id")) if session.get("last_result_id") else None
                    evidence = tokens(message)
                    if previous_result:
                        evidence |= tokens(" ".join(card["name"] for card in previous_result.get("cards", [])))
                    if not isinstance(value, str) or not tokens(value) <= evidence:
                        result = {"kind": "clarification", "message": "Please state the exact preference or favorite to remember.",
                                  "cards": [], "total": None}
                    else:
                        saved = self.memory.save_memory(intent.get("memory_category", ""),
                                                        intent.get("memory_key", ""), value)
                        result = {"kind": "memory_saved", "message": f"Saved {saved['key']} as a personal memory. This does not log a meal.",
                                  "cards": [], "total": None, "memories": self.memory.memories()}
                        debug["tool_action"] = {"operation": "remember", "memory": saved}
                elif operation == "forget" and re.search(r"\b(forget|delete|remove)\b", message, re.I):
                    key = intent.get("memory_key", "").strip().lower()
                    matches = [memory for memory in memories if memory["key"].lower() == key]
                    if len(matches) != 1:
                        result = {"kind": "clarification", "message": "Which saved memory should I delete? Use its exact name.",
                                  "cards": [], "total": None}
                    else:
                        self.memory.delete_memory(matches[0]["id"])
                        result = {"kind": "memory_deleted", "message": f"Deleted the saved memory {matches[0]['key']}.",
                                  "cards": [], "total": None, "memories": self.memory.memories()}
                        debug["tool_action"] = {"operation": "forget", "memory_id": matches[0]["id"]}
                else:
                    result = {"kind": "clarification", "message": "Please explicitly say what personal preference to save or forget.",
                              "cards": [], "total": None}
            elif operation in {"remove", "undo"}:
                stage = "meal ledger"
                if operation == "remove" and re.search(r"\b(remove|delete|undo|take out)\b", message, re.I):
                    meal = self._meal_target(intent.get("target", ""), session)
                    if meal:
                        self.memory.remove_meal(meal["id"])
                        session["last_action"] = {"type": "remove", "entry_id": meal["id"]}
                        result = {"kind": "meal_removed", "message": f"Removed {meal['label']} from {meal['date']}. You can undo this.",
                                  "cards": [], "total": None, "tracker": self.memory.tracker()}
                        debug["tool_action"] = {"operation": "remove", "entry_id": meal["id"]}
                    else:
                        result = {"kind": "clarification", "message": "Which logged meal should I remove? Please give its name or ID.",
                                  "cards": [], "total": None}
                elif operation == "undo" and re.search(r"\b(undo|revert)\b", message, re.I):
                    action = session.get("last_action")
                    if action and action["type"] == "log":
                        self.memory.remove_meal(action["entry_id"])
                    elif action and action["type"] == "remove":
                        self.memory.restore_meal(action["entry_id"])
                    elif action and action["type"] == "edit":
                        self.memory.remove_meal(action["new_id"])
                        self.memory.restore_meal(action["old_id"])
                    else:
                        action = None
                    if action:
                        session["last_action"] = None
                        result = {"kind": "undone", "message": "Undid the last meal change in this conversation.",
                                  "cards": [], "total": None, "tracker": self.memory.tracker()}
                        debug["tool_action"] = {"operation": "undo", "action": action}
                    else:
                        result = {"kind": "clarification", "message": "There is no recent meal change in this conversation to undo.",
                                  "cards": [], "total": None}
                else:
                    result = {"kind": "clarification", "message": "Please explicitly say which meal to remove or undo.",
                              "cards": [], "total": None}
            elif operation in {"log", "edit"} and not intent["foods"] and not (operation == "log" and session.get("last_result_id")):
                result = {"kind": "clarification", "message": "Which food and listed serving amount should I use for that meal?",
                          "cards": [], "total": None}
            else:
                reuse = operation == "log" and not intent["foods"] and session.get("last_result_id")
                if reuse:
                    result = self.memory.result(session["last_result_id"])
                    requested = session["foods"]
                    if not result:
                        raise ValueError("The earlier nutrition result is unavailable. Please describe the meal again.")
                    result_id = session["last_result_id"]
                    debug["matches"] = [{"requested": item, "status": "reused previous verified result",
                                         "selected": card, "candidates": []}
                                        for item, card in zip(requested, result["cards"])]
                else:
                    requested = normalize_intent(intent, session["foods"])
                    stage = "UMD matching"
                    result = resolve(requested, data["datasets"], data["today"])
                    debug.update(result.pop("debug", {}))
                if result["kind"] == "result":
                    if not reuse:
                        label = intent.get("label", "").strip()[:80] or "Meal"
                        fingerprint = hashlib.sha256(json.dumps(requested, sort_keys=True).encode()).hexdigest()
                        result_id = self.memory.save_result(conversation_id, {**result, "fingerprint": fingerprint, "label": label})
                    result["result_id"] = result_id
                    session["last_result_id"] = result_id
                    if operation in {"log", "edit"}:
                        stage = "meal ledger"
                        pattern = (r"\b(add|log|track|record|update\s+my\s+macros|update\s+(?:today.s|my)\s+totals)\b" if operation == "log" else
                                   r"\b(actually|change|correct|edit|replace|instead)\b|make that")
                        explicit = re.search(pattern, message, re.I)
                        target_meal = self._meal_target(intent.get("target", ""), session) if operation == "edit" else None
                        day = intent.get("log_date") or (target_meal["date"] if target_meal else data["today"])
                        try:
                            day = date.fromisoformat(day).isoformat()
                        except (TypeError, ValueError):
                            raise ValueError("The log date is invalid. Please specify the date.")
                        caution = self._safe_to_log(result, requested, day)
                        if operation == "log" and not reuse and not all(
                                tokens(item["name"]) <= tokens(message) for item in requested):
                            caution = "Please name each food you want logged, or first ask me to verify the meal's UMD labels."
                        if not explicit or caution:
                            result = {"kind": "clarification", "message": caution or "Please explicitly ask me to log or correct this meal.",
                                      "cards": result["cards"], "total": result["total"], "notes": result.get("notes", []),
                                      "result_id": result_id}
                        elif operation == "edit":
                            meal = target_meal
                            if not meal:
                                result = {"kind": "clarification", "message": "Which logged meal should I correct? Please name it.",
                                          "cards": result["cards"], "total": result["total"]}
                            else:
                                updated = self.memory.replace_meal(meal["id"], result_id)
                                session["last_logged_id"] = updated["id"]
                                session["last_action"] = {"type": "edit", "new_id": updated["id"], "old_id": meal["id"]}
                                result["kind"] = "meal_edited"
                                result["message"] = f"Corrected {updated['label']} on {updated['date']} using the linked UMD labels."
                                result["tracker"] = self.memory.tracker()
                                debug["tool_action"] = {"operation": "edit", "entry_id": updated["id"]}
                        else:
                            logged, added = self.memory.log_result(result_id, day, conversation_id,
                                                                   allow_repeat=bool(re.search(r"\b(again|another)\b", message, re.I)),
                                                                   label_override=intent.get("label") or None)
                            if added:
                                session["last_logged_id"] = logged["id"]
                                session["last_action"] = {"type": "log", "entry_id": logged["id"]}
                            result["kind"] = "meal_logged" if added else "already_logged"
                            result["message"] = (f"Logged {logged['label']} for {day} using the linked UMD labels."
                                                 if added else "That meal is already logged in this conversation; I did not add it twice.")
                            result["tracker"] = self.memory.tracker()
                            debug["tool_action"] = {"operation": "log", "entry_id": logged["id"], "added": added}
            debug["resolved_foods"] = requested
        except (OllamaError, ValueError) as exc:
            debug["error"] = {"stage": stage, "message": str(exc)}
            debug["response_time_ms"] = round((time.perf_counter() - started) * 1000)
            raise ChatError(str(exc), debug, 503 if isinstance(exc, OllamaError) else 400) from exc
        session["foods"] = requested
        session["question"] = result["message"] if result["kind"] == "clarification" else ""
        session["recent"] = (session.get("recent", []) + [
            {"role": "user", "text": message[:250]},
            {"role": "assistant", "text": result["message"][:250]}])[-6:]
        self.memory.save_conversation(conversation_id, session)
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
        elif route == "/api/tracker":
            self._json(200, self.app.memory.tracker())
        elif route == "/api/meals":
            self._json(200, {"meals": self.app.memory.meals()})
        elif route == "/api/conversation":
            try:
                conversation_id = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
                self._json(200, self.app.conversations.history(conversation_id))
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
        elif route == "/api/memories":
            self._json(200, {"memories": self.app.memory.memories()})
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
        actions = {"/api/tracker/adjust", "/api/tracker/goals", "/api/tracker/reset",
                   "/api/tracker/import", "/api/tracker/add-result", "/api/meal/remove", "/api/meal/restore",
                   "/api/memory/save", "/api/memory/update", "/api/memory/delete", "/api/reaction"}
        if route not in ("/api/refresh", "/api/chat", *actions):
            self._json(404, {"error": "Not found"})
            return
        kind = "refresh" if route == "/api/refresh" else "chat" if route == "/api/chat" else "state"
        if (not allowed_request_origin(self.headers, self.client_address[0])
                or self.headers.get("X-MacroMellow-Request") != kind):
            self._json(403, {"error": "This action is only available from the local interface"})
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self._json(415, {"error": "Expected JSON request"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= (256 if kind == "refresh" else 12000):
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
                    self._json(200, self.app.conversations.turn(message.strip(), conversation_id, payload.get("request_id")))
                except ChatError as exc:
                    self._json(exc.status, {"error": str(exc), "debug": exc.debug})
                return
            if route in actions:
                try:
                    if route == "/api/reaction":
                        result = self.app.conversations.react(payload["conversation_id"], payload["message_id"], payload["active"])
                    elif route == "/api/tracker/adjust":
                        result = self.app.memory.adjust(payload["mode"], payload["values"])
                    elif route == "/api/tracker/goals":
                        result = self.app.memory.set_goals(payload["goals"])
                    elif route == "/api/tracker/reset":
                        result = self.app.memory.reset()
                    elif route == "/api/tracker/import":
                        result = self.app.memory.import_legacy(payload["migration_key"], payload["state"], payload["goals"])
                    elif route == "/api/tracker/add-result":
                        entry, added = self.app.memory.log_result(payload["result_id"], self.app.memory.today(),
                                                                   payload.get("conversation_id"))
                        result = {"entry": entry, "added": added, "tracker": self.app.memory.tracker()}
                    elif route == "/api/meal/remove":
                        entry = self.app.memory.remove_meal(payload["entry_id"])
                        result = {"entry": entry, "tracker": self.app.memory.tracker()}
                    elif route == "/api/meal/restore":
                        entry = self.app.memory.restore_meal(payload["entry_id"])
                        result = {"entry": entry, "tracker": self.app.memory.tracker()}
                    elif route == "/api/memory/save":
                        result = {"memory": self.app.memory.save_memory(payload["category"], payload["key"], payload["value"]),
                                  "memories": self.app.memory.memories()}
                    elif route == "/api/memory/update":
                        result = {"memory": self.app.memory.update_memory(payload["id"], payload["category"],
                                                                            payload["key"], payload["value"]),
                                  "memories": self.app.memory.memories()}
                    else:
                        result = {"deleted": self.app.memory.delete_memory(payload["id"]),
                                  "memories": self.app.memory.memories()}
                    self._json(200, result)
                except (ValueError, KeyError, TypeError) as exc:
                    self._json(400, {"error": str(exc)})
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
