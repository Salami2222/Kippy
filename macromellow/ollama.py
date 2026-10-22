"""Small client for a local or Tailscale-reachable Ollama server."""

from __future__ import annotations

import json
import os
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OllamaError(RuntimeError):
    """A connection, model, or response problem safe to show in the UI."""


INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["set", "add", "clarify"]},
        "operation": {"type": "string", "enum": ["lookup", "log", "edit", "remove", "undo", "today", "history", "remember", "recall", "forget", "converse", "adjust"]},
        "response_type": {"type": "string", "enum": ["acknowledgment", "message", "clarification", "action"]},
        "response_text": {"type": "string"},
        "adjustment_mode": {"type": "string", "enum": ["", "add", "subtract", "set"]},
        "adjustment_values": {"type": "object", "properties": {
            key: {"type": ["number", "null"]} for key in ("calories", "protein_g", "carbohydrates_g", "fat_g")
        }, "required": ["calories", "protein_g", "carbohydrates_g", "fat_g"], "additionalProperties": False},
        "target": {"type": "string"},
        "label": {"type": "string"},
        "log_date": {"type": "string"},
        "memory_category": {"type": "string"},
        "memory_key": {"type": "string"},
        "memory_value": {"type": "string"},
        "foods": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "quantity": {"type": "number"},
                "unit": {"type": "string"},
                "hall": {"type": "string"},
                "date": {"type": "string"},
            },
            "required": ["name", "quantity", "unit"],
            "additionalProperties": False,
        }},
        "question": {"type": "string"},
    },
    "required": ["action", "operation", "foods"],
    "additionalProperties": False,
}


# Keep the wire schema small: compact local models can fail when forced to fill
# unrelated memory, nutrition-adjustment and conversation fields every turn.
DEFAULT_FIELDS = {
    "target": "", "label": "", "log_date": "", "memory_category": "",
    "memory_key": "", "memory_value": "", "question": "", "response_text": "",
    "response_type": "action", "adjustment_mode": "",
}
NUTRIENT_KEYS = ("calories", "protein_g", "carbohydrates_g", "fat_g")


def parse_model_plan(response: dict) -> dict:
    """Parse and normalize a model reply; never execute a partially valid plan."""
    if not isinstance(response, dict) or not isinstance(response.get("message"), dict):
        raise ValueError("missing message")
    raw = response["message"].get("content")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("empty content")
    raw = raw.strip()
    # Some older Ollama/model combinations include a JSON markdown fence.
    if raw.startswith("```"):
        raw = re.sub(r"^\x60{3}(?:json)?\s*|\s*\x60{3}$", "", raw, flags=re.I).strip()
    plan = json.loads(raw)
    if not isinstance(plan, dict):
        raise ValueError("non-object JSON")
    for key, default in DEFAULT_FIELDS.items():
        plan.setdefault(key, default)
    if plan.get("operation") == "converse" and plan.get("response_type") == "action":
        plan["response_type"] = "message"
    if plan.get("action") == "clarify" and plan.get("response_type") == "action":
        plan["response_type"] = "clarification"
    plan.setdefault("adjustment_values", {key: None for key in NUTRIENT_KEYS})
    if isinstance(plan.get("adjustment_values"), dict):
        for key in NUTRIENT_KEYS:
            plan["adjustment_values"].setdefault(key, None)
    if isinstance(plan.get("foods"), list):
        for food in plan["foods"]:
            if isinstance(food, dict):
                food.setdefault("hall", "")
                food.setdefault("date", "")
                food.setdefault("unit", "unspecified")
                food.setdefault("quantity", 1)
    validate_response(plan)
    return plan


def fallback_single_food(message: str) -> dict | None:
    """Conservative recovery for one plainly stated food when BOTH AI replies fail."""
    beginning = re.match(
        r"^\s*i\s+(?:just\s+)?(?:had|ate)\s+"
        r"(?:(?P<qty>\d+(?:\.\d+)?|a|an|one|two|three)\s+)?"
        r"(?:(?P<unit>slices?|scoops?|servings?|pieces?)\s+of\s+)?"
        r"(?P<name>[^,;.!?]+)",
        message, flags=re.I,
    )
    if not beginning:
        return None
    name = beginning.group("name").strip()
    name = re.split(r"\s+(?:can\s+you|please|update\s+my\s+macros|and\s+(?:log|track|add|update))\b", name, maxsplit=1, flags=re.I)[0].strip()
    if not name or len(name) > 100 or re.search(r"\s+and\s+|\s+plus\s+", name, flags=re.I):
        return None
    number = (beginning.group("qty") or "1").lower()
    quantity = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3}.get(number)
    if quantity is None:
        quantity = float(number)
    if quantity <= 0 or quantity > 100:
        return None
    unit = (beginning.group("unit") or "unspecified").lower()
    unit = {"slice": "slice", "slices": "slice", "scoop": "scoop", "scoops": "scoop",
            "piece": "piece", "pieces": "piece", "servings": "serving",
            "serving": "serving"}.get(unit, "unspecified")
    if unit == "unspecified" and beginning.group("qty"):
        unit = "each"
    wants_log = bool(re.search(r"\b(update\s+my\s+macros|add\s+(?:it|that|this)\s+to\s+(?:my|today)|log|track|record)\b", message, flags=re.I))
    plan = {"action": "set", "operation": "log" if wants_log else "lookup",
            "foods": [{"name": name, "quantity": quantity, "unit": unit, "hall": "", "date": ""}],
            "question": "", "response_type": "action", "response_text": ""}
    return parse_model_plan({"message": {"content": json.dumps(plan)}})


class OllamaClient:
    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL") or "qwen3.5:9b"
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("OLLAMA_BASE_URL must start with http:// or https://")

    def _request(self, path: str, payload: dict | None = None, timeout: int = 10) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(self.base_url + path, data=body,
                          headers={"Content-Type": "application/json"} if body else {},
                          method="POST" if body else "GET")
        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise ValueError("Expected a JSON object")
            return result
        except HTTPError as exc:
            if exc.code == 404:
                raise OllamaError("Ollama endpoint or model was not found. Check OLLAMA_BASE_URL and OLLAMA_MODEL.") from exc
            raise OllamaError(f"Ollama returned HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), socket.timeout):
                raise OllamaError("Ollama timed out. Check the desktop connection or try again.") from exc
            raise OllamaError("Cannot reach Ollama. Check that the Windows desktop and Tailscale are connected.") from exc
        except (ValueError, UnicodeDecodeError) as exc:
            raise OllamaError("Ollama returned an unreadable response.") from exc

    def status(self) -> dict:
        try:
            models = self._request("/api/tags").get("models", [])
            if not isinstance(models, list):
                raise OllamaError("Ollama returned an invalid model list.")
            installed = {entry.get("name") for entry in models if isinstance(entry, dict)}
            available = self.model in installed
            return {"connected": True, "model_available": available, "model": self.model,
                    "message": "AI ready" if available else f"Ollama is connected, but {self.model} is not installed on the desktop."}
        except OllamaError as exc:
            return {"connected": False, "model_available": False, "model": self.model, "message": str(exc)}

    def interpret(self, message: str, previous: list[dict], today: str, dates: list[str],
                  last_question: str = "", recent: list[dict] | None = None,
                  memories: list[dict] | None = None, recent_meals: list[dict] | None = None) -> dict:
        state = self.status()
        if not state["model_available"]:
            raise OllamaError(state["message"])
        system = (
            "You are Mellow, a warm, concise nutrition companion. Handle every message, including conversation. "
            "Use operation converse for greetings, thanks, confirmations with no pending action, or general conversation. "
            "response_type is acknowledgment only when no information or explanation is expected; otherwise message. "
            "For questions always provide a message or route to a factual backend tool. Use clarification if uncertain. "
            "For converse, response_text is a brief conversational reply; never claim an action occurred, invent "
            "nutrition facts, or give numerical nutrition values. Explain unsupported requests honestly. "
            "For backend operations response_type is action or message; the backend supplies factual text. "
            "Use adjust only for explicitly requested manual macro changes with literal user-provided amounts. "
            "Extract adjustment_mode add/subtract/set and adjustment_values (unspecified nutrients null). "
            "Never calculate calories from macros. Only today's manual adjustments are supported through chat. "
            "A reaction references only its message_id; never use it as blanket permission or as approval for "
            "removals, resets, memory deletion, or unrelated actions. Reaction events marked inactive are withdrawn. "
            "Meal reaction approvals are executed by the backend; never execute them again. "
            "Plan a safe nutrition assistant action. Do not calculate nutrition or claim a UMD food exists. "
            "Return only the requested JSON schema. Operation is lookup for questions, log only when the user "
            "explicitly asks to add/log a meal, edit for a correction to a logged meal, remove for deletion, "
            "undo for reversing the last change, today for daily totals/goals, history for logged meals, "
            "remember/recall/forget for personal memories. Never infer eating from a preference or question. "
            "Use 'set' for a new meal or a correction: then foods "
            "must be the complete revised meal. Use 'add' only if the user explicitly adds food to the "
            "previous meal. Use 'clarify' only if the user's food request cannot be identified. "
            "When the user says to log the immediately preceding verified lookup without new food details, "
            "use operation log with an empty foods array so the backend reuses that result. "
            "For a correction to an already logged meal use operation edit with the complete revised foods. "
            "Keep food names as the user's words; do not replace them with guessed menu names. "
            "Use numeric quantity; for vague amounts such as 'some' or 'a scoop', use 1 and preserve "
            "the phrase in unit. For omitted amounts use 1 and unit 'unspecified'. "
            "For counted foods use unit 'each'; for explicit servings use unit 'serving'. "
            "Hall must be 'yahentamitsi', 'south', '251-north', or empty string. "
            "Date must be YYYY-MM-DD or empty string. Resolve today/yesterday relative to the supplied "
            "Maryland date. Preserve known hall/date on follow-up corrections unless changed. "
            "For a nutrition question about a food, extract that food as a meal lookup. "
            "Target identifies a logged meal by ID or name; use empty string when absent. "
            "Label is a meal name such as lunch if stated, otherwise empty. Log_date is the intended date "
            "YYYY-MM-DD or empty for today. Memory category is preference, favorite_restaurant, "
            "favorite_meal, favorite_food, common_meal, or frequent_food. Memory fields are plain "
            "user-provided text, never UMD facts. "
            "Ask for clarification if a removal, edit, or log target is unclear. "
            "Only adjustment_values may contain literal nutrition numbers supplied by the user. "
            "When the user says update my macros after describing food, choose operation log, "
            "not adjust. Extract food even if no UMD entry is available. "
            "Understand obvious typos and aliases (e.g. bananna, mac n cheese), but preserve the "
            "food the user intended. Ask which food when the intended meaning is unclear."
        )
        context = {"today": today, "saved_dates": dates, "previous_foods": previous,
                   "last_clarification": last_question, "recent_interactions": (recent or [])[-6:],
                   "personal_memories": (memories or [])[:30], "recent_logged_meals": (recent_meals or [])[:12],
                   "user_message": message}
        payload = {
            "model": self.model, "messages": [{"role": "system", "content": system},
                                          {"role": "user", "content": json.dumps(context)}],
            "stream": False, "think": False, "format": INTENT_SCHEMA,
            "options": {"temperature": 0, "num_predict": 1200},
        }
        response = self._request("/api/chat", payload, timeout=90)
        try:
            return parse_model_plan(response)
        except (KeyError, TypeError, ValueError) as first_error:
            # A smaller format request can recover when a local model fails the
            # strict JSON schema; never retry connectivity errors or unsafe actions.
            retry = {**payload, "format": "json", "messages": payload["messages"] + [
                {"role": "user", "content": (
                    "Return one valid JSON object only, with action (set/add/clarify), "
                    "operation, foods (list), and any relevant optional fields. "
                    "No explanation, markdown, or thinking. For described foods "
                    "extract names, numeric quantities and units. "
                    "Ask a clarification if genuinely uncertain."
                )},
            ]}
            try:
                return parse_model_plan(self._request("/api/chat", retry, timeout=90))
            except (KeyError, TypeError, ValueError) as second_error:
                recovered = fallback_single_food(message)
                if recovered is not None:
                    return recovered
                raise OllamaError(
                    "Mellow received an invalid structured response from Ollama twice "
                    f"({type(first_error).__name__}, {type(second_error).__name__}). "
                    "The model is reachable, but its response could not be used safely."
                ) from second_error


def validate_response(intent: dict) -> None:
    """Validate presentation fields even for injected/local planner implementations."""
    if not isinstance(intent, dict) or intent.get("action") not in {"set", "add", "clarify"}:
        raise ValueError("Invalid agent plan. Please rephrase your request.")
    if not isinstance(intent.get("foods"), list) or len(intent["foods"]) > 12:
        raise ValueError("Invalid food request.")
    if intent.get("operation", "lookup") not in {"lookup", "log", "edit", "remove", "undo", "today", "history", "remember", "recall", "forget", "converse", "adjust"}:
        raise ValueError("Unsupported agent operation.")
    for key in ("target", "label", "log_date", "memory_category", "memory_key", "memory_value"):
        if not isinstance(intent.get(key, ""), str) or len(intent.get(key, "")) > 500:
            raise ValueError("Invalid agent action field.")
    if intent.get("response_type", "message") not in {"acknowledgment", "message", "clarification", "action"}:
        raise ValueError("Invalid structured response type. Please rephrase your request.")
    for key in ("response_text", "question"):
        value = intent.get(key, "")
        if not isinstance(value, str) or len(value) > 2000:
            raise ValueError("Invalid response text. Please rephrase your request.")
    if intent.get("operation") == "converse":
        if intent.get("response_type") not in {"acknowledgment", "message", "clarification"}:
            raise ValueError("Invalid conversational response.")
        if intent["response_type"] == "message" and not intent.get("response_text", "").strip():
            raise ValueError("Mellow returned an empty answer. Please try again.")
        text = intent.get("response_text", "")
        if re.search(r"\b(logged|added|updated|removed|deleted|saved|reset|recorded|changed|tracked)\b", text, re.I):
            raise ValueError("Mellow returned an unverified action claim. No action was executed; please rephrase your request.")
        if re.search(r"\d", text) and re.search(r"\b(calories|kcal|protein|carbs|carbohydrates|fat|grams)\b", text, re.I):
            raise ValueError("Nutrition numbers must come from a verified lookup or tracker tool. Please ask about a specific food or your totals.")
