"""Small client for a local or Tailscale-reachable Ollama server."""

from __future__ import annotations

import json
import os
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OllamaError(RuntimeError):
    """A connection, model, or response problem safe to show in the UI."""


INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["set", "add", "clarify"]},
        "foods": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "quantity": {"type": "number"},
                "unit": {"type": "string"},
                "hall": {"type": "string"},
                "date": {"type": "string"},
            },
            "required": ["name", "quantity", "unit", "hall", "date"],
            "additionalProperties": False,
        }},
        "question": {"type": "string"},
    },
    "required": ["action", "foods", "question"],
    "additionalProperties": False,
}


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
                  last_question: str = "") -> dict:
        state = self.status()
        if not state["model_available"]:
            raise OllamaError(state["message"])
        system = (
            "Extract meal lookup intent only. Do not calculate nutrition or claim a UMD food exists. "
            "Return only the requested JSON schema. Use 'set' for a new meal or a correction: then foods "
            "must be the complete revised meal. Use 'add' only if the user explicitly adds food to the "
            "previous meal. Use 'clarify' only if the user's food request cannot be identified. "
            "Keep food names as the user's words; do not replace them with guessed menu names. "
            "Use numeric quantity; for vague amounts such as 'some' or 'a scoop', use 1 and preserve "
            "the phrase in unit. For omitted amounts use 1 and unit 'unspecified'. "
            "For counted foods use unit 'each'; for explicit servings use unit 'serving'. "
            "Hall must be 'yahentamitsi', 'south', '251-north', or empty string. "
            "Date must be YYYY-MM-DD or empty string. Resolve today/yesterday relative to the supplied "
            "Maryland date. Preserve known hall/date on follow-up corrections unless changed. "
            "For a nutrition question about a food, extract that food as a meal lookup. "
            "Never put numeric nutrition values in the response."
        )
        context = {"today": today, "saved_dates": dates, "previous_foods": previous,
                   "last_clarification": last_question, "user_message": message}
        response = self._request("/api/chat", {
            "model": self.model, "messages": [{"role": "system", "content": system},
                                          {"role": "user", "content": json.dumps(context)}],
            "stream": False, "format": INTENT_SCHEMA, "options": {"temperature": 0},
        }, timeout=90)
        try:
            intent = json.loads(response["message"]["content"])
            if intent.get("action") not in {"set", "add", "clarify"} or not isinstance(intent.get("foods"), list):
                raise ValueError("Invalid intent")
            if len(intent["foods"]) > 12:
                raise ValueError("Too many foods")
            return intent
        except (KeyError, TypeError, ValueError) as exc:
            raise OllamaError("Ollama could not interpret that request. Please rephrase it.") from exc
