"""Small client for a local or Tailscale-reachable Ollama server."""

from __future__ import annotations

import json
import os
import re
import socket
import math
import time
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OllamaError(RuntimeError):
    """Safe user-facing error plus sanitized diagnostic metadata."""
    def __init__(self, message, category="model_output", details=None):
        super().__init__(message)
        self.category = category
        self.details = details or {}


OPERATIONS = {"lookup", "log", "edit", "remove", "undo", "today", "history", "remember", "recall", "forget", "converse", "adjust"}
FOOD_PROPERTIES = {
    "name": {"type": "string"}, "quantity": {"type": "number"}, "unit": {"type": "string"},
    "hall": {"type": "string"}, "date": {"type": "string"},
    "portion_amount": {"type": "number"}, "portion_unit": {"type": "string"},
}
# Only operation is universal. Python fills and validates defaults for the chosen operation.
INTENT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["operation"],
    "properties": {
        "operation": {"type": "string", "enum": sorted(OPERATIONS)},
        "action": {"type": "string", "enum": ["set", "add", "update", "drop", "clarify"]},
        "foods": {"type": "array", "maxItems": 12, "items": {"type": "object", "properties": FOOD_PROPERTIES,
                                                               "required": ["name"], "additionalProperties": False}},
        "updates": {"type": "array", "maxItems": 12, "items": {"type": "object", "properties": {
            "item_index": {"type": "integer"}, **FOOD_PROPERTIES,
        }, "required": ["item_index"], "additionalProperties": False}},
        "selection": {"type": "integer"},
        **{key: {"type": "string"} for key in ["question", "target", "label", "log_date", "previous_meal_id",
            "memory_category", "memory_key", "memory_value", "response_text"]},
        "response_type": {"type": "string", "enum": ["acknowledgment", "message", "clarification", "action"]},
        "adjustment_mode": {"type": "string", "enum": ["", "add", "subtract", "set"]},
        "adjustment_values": {"type": "object", "properties": {
            key: {"type": ["number", "null"]} for key in ("calories", "protein_g", "carbohydrates_g", "fat_g")
        }, "additionalProperties": False},
    },
}


class PlanError(ValueError):
    def __init__(self, category, field="plan"):
        super().__init__("Mellow returned an invalid planning field.")
        self.category, self.field = category, field


def decode_plan(response):
    if not isinstance(response, dict):
        raise PlanError("envelope_type")
    if response.get("error"):
        raise OllamaError("The local model reported a failure. Please try again.", "model_failure")
    envelope = response.get("message")
    if not isinstance(envelope, dict):
        raise PlanError("missing_message")
    content = envelope.get("content")
    if not isinstance(content, str):
        raise PlanError("content_type")
    content = content.strip()
    if not content:
        raise PlanError("thinking_only" if envelope.get("thinking") else "empty_content")
    if len(content) > 24000:
        raise PlanError("output_too_large")
    # Accept a single fenced JSON object; never fish an object out of arbitrary prose.
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", content, re.I)
    if fenced:
        content = fenced[1]
    try:
        result = json.loads(content)
    except (ValueError, RecursionError) as exc:
        raise PlanError("malformed_json") from exc
    if response.get("done_reason") == "length":
        raise PlanError("truncated_output")
    return normalize_plan(result)


def normalize_plan(raw):
    if not isinstance(raw, dict):
        raise PlanError("plan_type")
    if set(raw) - set(INTENT_SCHEMA["properties"]):
        raise PlanError("unknown_field")
    plan = dict(raw)
    # Compatibility with earlier action/foods responses. An empty object is never a valid plan.
    if "operation" not in plan:
        if plan.get("foods") or plan.get("action") == "clarify":
            plan["operation"] = "lookup"
        else:
            raise PlanError("missing_operation")
    if not isinstance(plan["operation"], str) or plan["operation"] not in OPERATIONS:
        raise PlanError("invalid_operation", "operation")
    for key in ["action", "response_type"]:
        if key in plan and not isinstance(plan[key], str):
            raise PlanError("wrong_type", key)
    plan.setdefault("action", "update" if plan.get("updates") or "selection" in plan else "set")
    plan.setdefault("response_type", "clarification" if plan["action"] == "clarify" else "message")
    if plan["action"] not in {"set", "add", "update", "drop", "clarify"}:
        raise PlanError("invalid_action", "action")
    if plan["response_type"] not in {"acknowledgment", "message", "clarification", "action"}:
        raise PlanError("invalid_response_type", "response_type")
    for key in ["question", "target", "label", "log_date", "previous_meal_id", "memory_category", "memory_key", "memory_value", "response_text"]:
        plan.setdefault(key, "")
        if not isinstance(plan[key], str) or len(plan[key]) > (2000 if key in {"question", "response_text"} else 500):
            raise PlanError("wrong_type", key)
    if plan["log_date"]:
        try:
            plan["log_date"] = date.fromisoformat(plan["log_date"]).isoformat()
        except ValueError as exc:
            raise PlanError("invalid_date", "log_date") from exc
    if plan["previous_meal_id"] and not re.fullmatch(r"[a-f0-9]{32}", plan["previous_meal_id"]):
        raise PlanError("invalid_meal_reference", "previous_meal_id")
    for key in ("foods", "updates"):
        plan.setdefault(key, [])
        if not isinstance(plan[key], list) or len(plan[key]) > 12:
            raise PlanError("invalid_array", key)
        normalized = []
        for food in plan[key]:
            allowed = set(FOOD_PROPERTIES) | ({"item_index"} if key == "updates" else set())
            if not isinstance(food, dict) or set(food) - allowed:
                raise PlanError("invalid_food_fields", key)
            f = dict(food)
            if key == "updates":
                # Empty optional patch text is a model placeholder, never an instruction to erase context.
                f = {field: value for field, value in f.items() if value != ""}
                if plan["action"] == "drop":
                    f = {"item_index": f.get("item_index")}
                if type(f.get("item_index")) is not int or not 0 <= f["item_index"] < 12:
                    raise PlanError("invalid_item_index", key)
            else:
                if "name" not in f:
                    raise PlanError("missing_food_name", key)
            for field, value in f.items():
                if field in {"quantity", "portion_amount"}:
                    if type(value) not in {int, float} or not math.isfinite(value) or not 0 < value <= 10000:
                        raise PlanError("invalid_quantity", field)
                elif field != "item_index" and (not isinstance(value, str) or len(value) > 120):
                    raise PlanError("wrong_type", field)
            if "name" in f and not f["name"].strip():
                raise PlanError("empty_food_name")
            if "hall" in f:
                h = re.sub(r"[^a-z0-9]+", " ", f["hall"].strip().lower()).strip()
                f["hall"] = {"the y": "yahentamitsi", "y": "yahentamitsi", "yahentamitsi the y": "yahentamitsi", "the y yahentamitsi": "yahentamitsi", "yahentamitsi dining hall": "yahentamitsi", "south campus": "south", "south campus dining hall": "south", "251 north": "251-north", "251 north dining hall": "251-north", "251 north dining": "251-north"}.get(h, h)
                if f["hall"] not in {"", "yahentamitsi", "south", "251-north"}:
                    raise PlanError("invalid_hall", "hall")
            if f.get("date"):
                try:
                    f["date"] = date.fromisoformat(f["date"]).isoformat()
                except ValueError as exc:
                    raise PlanError("invalid_date", "date") from exc
            normalized.append(f)
        plan[key] = normalized
    if "selection" in plan and (type(plan["selection"]) is not int or not 1 <= plan["selection"] <= 4):
        raise PlanError("invalid_selection")
    if "adjustment_mode" in plan and (not isinstance(plan["adjustment_mode"], str) or plan["adjustment_mode"] not in {"", "add", "subtract", "set"}):
        raise PlanError("invalid_adjustment_mode")
    values = plan.get("adjustment_values", {})
    if not isinstance(values, dict) or set(values) - {"calories", "protein_g", "carbohydrates_g", "fat_g"}:
        raise PlanError("invalid_adjustment_values")
    for value in values.values():
        if value is not None and (type(value) not in {int, float} or not math.isfinite(value)):
            raise PlanError("invalid_adjustment_values")
    if plan["operation"] == "converse":
        text = plan["response_text"]
        if plan["response_type"] == "action" or (plan["response_type"] == "message" and not text.strip()):
            raise PlanError("empty_or_invalid_conversation")
        if re.search(r"\b(logged|added|updated|removed|deleted|saved|reset|recorded|changed|tracked)\b", text, re.I):
            raise PlanError("unverified_action_claim")
        if re.search(r"\d", text) and re.search(r"\b(calories|kcal|protein|carbs|carbohydrates|fat|grams)\b", text, re.I):
            raise PlanError("unverified_nutrition_claim")
    return plan


def validate_response(intent):
    """Backwards-compatible validation entry point."""
    normalize_plan(intent)


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
                raise OllamaError("Ollama endpoint or model was not found. Check OLLAMA_BASE_URL and OLLAMA_MODEL.", "model_missing") from exc
            raise OllamaError("The local model service returned an error. Please try again.", "http_error", {"http_status": exc.code}) from exc
        except (URLError, TimeoutError, OSError) as exc:
            if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), socket.timeout):
                raise OllamaError("Mellow took too long to respond. Check the desktop connection or try again.", "timeout") from exc
            raise OllamaError("I cannot reach the local model. Check that the Windows desktop and Tailscale are connected.", "connection") from exc
        except (ValueError, UnicodeDecodeError) as exc:
            raise OllamaError("The local model service returned an unreadable response.", "transport_json") from exc

    def status(self) -> dict:
        try:
            models = self._request("/api/tags").get("models", [])
            if not isinstance(models, list):
                raise OllamaError("Ollama returned an invalid model list.")
            installed = {entry["name"] for entry in models if isinstance(entry, dict) and isinstance(entry.get("name"), str)}
            available = self.model in installed
            return {"connected": True, "model_available": available, "model": self.model,
                    "message": "AI ready" if available else f"Ollama is connected, but {self.model} is not installed on the desktop."}
        except OllamaError as exc:
            return {"connected": False, "model_available": False, "model": self.model, "message": str(exc), "error_category": exc.category}

    def interpret(self, message: str, previous: list[dict], today: str, dates: list[str],
                  last_question: str = "", recent: list[dict] | None = None,
                  memories: list[dict] | None = None, recent_meals: list[dict] | None = None,
                  pending: dict | None = None) -> dict:
        state = self.status()
        if not state["model_available"]:
            raise OllamaError(state["message"], state.get("error_category", "model_missing"))
        system = (
            "You are Mellow's planning component. Return one small JSON object, only fields relevant to this request. "
            "operation: lookup for reported meals and food questions; log ONLY for an explicit add/log/track request; "
            "edit/remove/undo for explicit logged-meal changes; today/history for tracker queries; "
            "remember/recall/forget for preferences; converse for ordinary conversation; adjust for literal manual macros. "
            "Do not calculate or invent nutrition, labels, source URLs, or successful actions. Preferences never imply eating. "
            "Extract EVERY individual food and its reported portion. Preserve vague units such as scoop; never turn scoops "
            "or slices into servings without the user's confirmation or an exact listed unit. "
            "Example: I had a macaroni pizza slice, 2 scoops of cilantro lime rice, 1 chocolate chip cookie and a scoop of mac n cheese -> "
            '{"operation":"lookup","foods":[{"name":"macaroni pizza","quantity":1,"unit":"slice"},'
            '{"name":"cilantro lime rice","quantity":2,"unit":"scoop"},'
            '{"name":"chocolate chip cookie","quantity":1,"unit":"each"},'
            '{"name":"mac n cheese","quantity":1,"unit":"scoop"}]}. '
            "Separate chicken and rice into two foods, but preserve dish names like mac and cheese as one food. "
            "Use hall yahentamitsi (The Y), south, 251-north, or omit. Dates YYYY-MM-DD relative to supplied Maryland today. "
            "New meals use action set (default); adding another item uses add. Follow-ups use action update and updates: "
            "each patch has item_index (zero-based in previous_foods) plus ONLY fields the user changed. "
            "Example Actually it was 3 scoops -> updates:[{item_index:1,quantity:3,unit:scoop}] if rice is item 1. "
            "Never replace a whole multi-food meal with the one item being clarified. If the target is uncertain ask a short question. "
            "action drop and updates with item_index remove foods from the working meal; use edit if it was logged. "
            "selection is 1-based among pending candidates for replies like the first one. "
            "portion_amount/portion_unit describe the measured size of EACH reported unit (e.g. each scoop was 3 oz). "
            "For same as yesterday use previous_meal_id from a real recent_logged_meals entry, never guess a meal. "
            "Use an empty foods array for How many calories are in this? or Add that to today's macros, reusing working context. "
            "For an unresolved request retain the pending lookup/log/edit operation on follow-ups. "
            "If clarification is truly required action clarify and question; otherwise let deterministic matching ask about labels/portions. "
            "For converse use response_type message with response_text, or acknowledgment only if no answer is expected. "
            "Never answer a question with acknowledgment only. Converse cannot claim an action succeeded or supply nutrition numbers. "
            "Manual adjust: adjustment_mode add/subtract/set, adjustment_values from literal user numbers only. "
            "Other fields target, label, log_date, memory_category/key/value are optional and only for relevant operations. "
            "Reacting is scoped to its exact message. Reaction approvals already execute in Python; do not repeat them."
        )
        context = {"today": today, "saved_dates": dates, "previous_foods": previous,
                   "pending_clarification": pending, "last_clarification": last_question,
                   "recent_interactions": (recent or [])[-6:], "personal_memories": (memories or [])[:30],
                   "recent_logged_meals": (recent_meals or [])[:12], "user_message": message}
        messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(context)}]
        failures = []
        deadline = time.monotonic() + 90
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OllamaError("Mellow took too long to respond. Please try again.", "timeout")
            response = self._request("/api/chat", {"model": self.model, "messages": messages,
                "stream": False, "think": False, "format": INTENT_SCHEMA, "options": {"temperature": 0, "num_predict": 2048}}, timeout=max(1, int(remaining)))
            try:
                plan = decode_plan(response)
                plan["_diagnostics"] = {"attempts": attempt + 1, "repaired": bool(attempt), "failures": failures}
                return plan
            except PlanError as exc:
                failures.append({"category": exc.category, "field": exc.field})
                if attempt:
                    raise OllamaError("I couldn't read the local model's meal plan. Please try again; no tracker changes were made.",
                                      "invalid_plan", {"attempts": 2, "failures": failures}) from exc
                # Repair without echoing malformed model text or conversation content into logs.
                messages.append({"role": "user", "content": "Your planning output failed validation: " + exc.category +
                                 " (field " + exc.field + "). Return one valid JSON object for the original request. Include operation; omit unrelated fields. "
                                 "Hall is yahentamitsi, south, 251-north, or omitted. For drop only supply item_index in updates. "
                                 "For update supply item_index and changed fields only, not empty placeholders."})
