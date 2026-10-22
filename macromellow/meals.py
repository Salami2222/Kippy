"""Working meal state: targeted clarification, corrections, and guarded logging."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import date, timedelta

from .agent import HALL_NAMES, normalize_intent, resolve, tokens, unit_name


def explicit_log(message):
    return bool(re.search(r"\b(add|log|track|record|update\s+my\s+macros|update\s+(?:today.s|my)\s+totals)\b", message, re.I)
                and not re.search(r"\b(don't|not|never|without|if)\b|\bI (?:prefer|like|love)\b|\bremember\b", message, re.I))


def local_reply(message, session):
    """Resolve only unambiguous replies to an existing, specific question."""
    pending = session.get("pending") or {}
    foods = session.get("foods", [])
    text = message.strip().lower().rstrip('.!')
    operation = pending.get("operation", "lookup")
    remove = re.fullmatch(r"(?:wait[, ]+)?(?:please )?(?:remove|take out|drop) (?:the )?(.+)", text)
    if remove:
        wanted = tokens(remove[1])
        matches = [i for i, food in enumerate(foods) if wanted and wanted <= tokens(food["name"])]
        if len(matches) == 1:
            return {"operation": "edit" if session.get("working_logged_id") else operation,
                    "action": "drop", "updates": [{"item_index": matches[0]}]}
    hall_change = re.fullmatch(r"(?:the )?(.+?) (?:was|is) from (?:the )?(.+)", text)
    halls = {"y": "yahentamitsi", "yahentamitsi": "yahentamitsi", "south campus": "south", "south campus dining hall": "south", "251 north": "251-north"}
    if hall_change and hall_change[2] in halls:
        wanted = tokens(hall_change[1])
        matches = [i for i, food in enumerate(foods) if wanted and wanted <= tokens(food["name"])]
        if len(matches) == 1:
            return {"operation": "edit" if session.get("working_logged_id") else operation, "updates": [{"item_index": matches[0], "hall": halls[hall_change[2]]}]}
    focus = pending.get("focus")
    if not focus:
        return None
    text = message.strip().lower().rstrip('.!')
    index = focus["item_index"]
    operation = pending.get("operation", "lookup")
    options = focus.get("options", [])
    ordinal = re.fullmatch(r"(?:(?:the|i meant|i mean|use)\s+)?(first|second|third|fourth|1|2|3|4)(?:\s+one)?(?:\s+please)?", text)
    if ordinal and options:
        selection = {"first": 1, "second": 2, "third": 3, "fourth": 4}.get(ordinal[1], int(ordinal[1]) if ordinal[1].isdigit() else 0)
        if selection <= len(options):
            return {"operation": operation, "selection": selection}
    if options:
        exact = [i for i, option in enumerate(options) if tokens(text) == tokens(option["name"]) or
                 tokens(re.sub(r"^(?:the|i meant|i mean|use)\s+", "", text)) == tokens(option["name"])]
        if len(exact) == 1:
            return {"operation": operation, "selection": exact[0] + 1}
    if focus["field"] == "portion":
        if re.fullmatch(r"(?:yes[, ]+)?(?:one |each )?(?:scoop|portion|slice) (?:is|was|equals|matches) (?:one |1 |a )?(?:listed |umd )?serving", text):
            return {"operation": operation, "action": "update", "updates": [{"item_index": index, "unit": "serving"}]}
        measured = re.fullmatch(r"(?:each (?:scoop|portion|slice) (?:was|is) |about )?(\d+(?:\.\d+)?)\s*(oz|ounces?|g|grams?)(?: each)?", text)
        if measured:
            return {"operation": operation, "action": "update", "updates": [{"item_index": index,
                    "portion_amount": float(measured[1]), "portion_unit": unit_name(measured[2])}]}
    halls = {"the y": "yahentamitsi", "yahentamitsi": "yahentamitsi", "south campus": "south", "south": "south", "251 north": "251-north"}
    if text in halls:
        return {"operation": operation, "action": "update", "updates": [{"item_index": index, "hall": halls[text]}]}
    return None


def selection_evidence(message, focus, selection):
    text = message.lower().strip().rstrip('.!')
    order = re.fullmatch(r"(?:(?:the|i meant|i mean|use)\s+)?(first|second|third|fourth|1|2|3|4)(?:\s+one)?(?:\s+please)?", text)
    if order:
        return {"first": 1, "second": 2, "third": 3, "fourth": 4}.get(order[1], int(order[1]) if order[1].isdigit() else 0) == selection
    if re.fullmatch(r"(?:the )?(?:regular|plain|original)(?: one)?(?: please)?", text):
        sets = [tokens(c["name"]) for c in focus["options"]]
        plain = [i for i, values in enumerate(sets) if all(values < other for j, other in enumerate(sets) if i != j)]
        return plain == [selection - 1]
    meaningful = tokens(text) - {"i", "meant", "mean", "one", "please", "use", "at", "from", "on", "it", "was", "is"}
    scores = [len(meaningful & tokens(c["name"] + " " + c.get("hall", ""))) for c in focus["options"]]
    return bool(scores) and scores[selection - 1] > 0 and scores.count(max(scores)) == 1 and scores[selection - 1] == max(scores)


def apply_plan(plan, session, message=""):

    previous = copy.deepcopy(session.get("foods", []))
    selected = dict(session.get("selections", {}))
    focus = (session.get("pending") or {}).get("focus")
    if "selection" in plan:
        if not focus or not 1 <= plan["selection"] <= len(focus.get("options", [])):
            raise ValueError("Which food option did you mean? Please choose from the current options.")
        if not selection_evidence(message, focus, plan["selection"]):
            raise ValueError("Which option do you mean? I won’t select a food without a clear choice.")
        option = focus["options"][plan["selection"] - 1]
        index = focus["item_index"]
        selected[str(index)] = option["record_key"]
        previous[index]["name"] = option["name"]
        previous[index]["hall"] = next((key for key, value in HALL_NAMES.items() if value == option["hall"]), "")
        previous[index]["date"] = option["date"]
        return previous, selected
    action = plan["action"]
    updates = plan["updates"]
    # Older clients sometimes return a single food for a clarification. Merge it rather than losing the meal.
    if (action == "set" and session.get("pending") and plan["foods"]
            and not re.match(r"^(?:I (?:had|ate)\b|(?:start )?(?:a )?new meal\b)", message, re.I)):
        updates = []
        for food in plan["foods"]:
            matches = [i for i, old in enumerate(previous) if tokens(food["name"]) == tokens(old["name"])]
            if not matches:
                matches = [i for i, old in enumerate(previous) if tokens(food["name"]) & tokens(old["name"])]
            if len(matches) != 1:
                raise ValueError("Which food in your meal should I update? The other foods are still saved.")
            # Omitted/empty context in a legacy repeated meal must not erase known hall/date/portion.
            patch = {k: v for k, v in food.items() if v != "" and not (k == "unit" and v == "unspecified")}
            updates.append({"item_index": matches[0], **patch})
        action = "update"
    if action in {"update", "drop"}:
        if not previous or not updates:
            raise ValueError("Which food in the working meal should I change?")
        if len({u["item_index"] for u in updates}) != len(updates):
            raise ValueError("Please specify each food correction once.")
        for update in updates:
            index = update["item_index"]
            if index >= len(previous):
                raise ValueError("That food isn't in this meal. Which item should I change?")
            others = [tokens(f["name"]) for f in previous]
            mentioned = [i for i, names in enumerate(others) if tokens(message) & (names - set().union(*(v for j, v in enumerate(others) if j != i)))]
            if action == "drop":
                target = re.search(r"(?:remove|drop|take out)\s+(?:the\s+)?(.+)", message, re.I)
                named = [i for i, f in enumerate(previous) if target and tokens(target[1]) and tokens(target[1]) <= tokens(f["name"])]
                if named != [index] and (not mentioned or index not in mentioned):
                    raise ValueError("Which food should I remove? Please name the specific item; I kept your meal unchanged.")
                continue
            if mentioned and index not in mentioned:
                raise ValueError("Which food should I change? I kept the rest of your meal unchanged.")
            if "quantity" in update and len(previous) > 1 and not mentioned:
                previous_amount = re.search(r"\bnot\s+(\d+(?:\.\d+)?)\b", message, re.I)
                narrowed = [i for i, f in enumerate(previous) if previous_amount and f["quantity"] == float(previous_amount[1])]
                focus_target = focus and focus["field"] == "portion" and focus["item_index"] == index
                if not focus_target and narrowed != [index]:
                    raise ValueError("Which food's quantity changed? Your other portions are still saved.")
            changed = {k: v for k, v in update.items() if k != "item_index"}
            if any(key in changed and changed[key] != previous[index].get(key) for key in ("name", "hall", "date")):
                selected.pop(str(index), None)
            if "unit" in changed and "portion_amount" not in changed:
                previous[index].pop("portion_amount", None)
                previous[index].pop("portion_unit", None)
            previous[index].update(changed)
        if action == "drop":
            removed = {u["item_index"] for u in updates}
            kept = [(i, food) for i, food in enumerate(previous) if i not in removed]
            selected = {str(new): selected[str(old)] for new, (old, _) in enumerate(kept) if str(old) in selected}
            previous = [food for _, food in kept]
        return previous, selected
    if not plan["foods"]:
        return previous, selected
    requested = normalize_intent(plan, previous)
    return requested, selected if action == "add" else {}


def historical_meal(app, message, plan):
    """Only real, verified meal records can provide 'same as yesterday' context."""
    meals = [m for m in app.memory.meals(100) if m["status"] == "active" and m.get("cards")
             and all(c.get("portion_confirmed") is True for c in m["cards"])]
    if re.search(r"\byesterday\b", message, re.I):
        day = (date.fromisoformat(app.memory.today()) - timedelta(days=1)).isoformat()
        meals = [m for m in meals if m["date"] == day]
    named = [m for m in meals if m["label"].lower() not in {"meal", ""} and m["label"].lower() in message.lower()]
    if named:
        meals = named
    if len(meals) != 1 or (plan.get("previous_meal_id") and meals[0]["id"] != plan["previous_meal_id"]):
        raise ValueError("Which previous meal do you mean? Please name the meal and date so I don't pick the wrong one.")
    meal = meals[0]
    return [{"name": c["name"], "quantity": c["quantity"], "unit": "serving",
             "hall": next((k for k, v in HALL_NAMES.items() if v == c["dining_hall"]), ""),
             "date": plan.get("log_date") or app.memory.today() if plan["operation"] == "log" else c["menu_date"]}
            for c in meal["cards"]]


def nutrition_turn(app, message, plan, session, conversation_id, data, debug):
    pending = session.get("pending") or {}
    operation = plan["operation"]
    following = bool(plan["updates"] or "selection" in plan or (not plan["foods"] and session.get("foods")))
    if (plan["action"] in {"update", "drop"} and operation in {"lookup", "remove"}
            and session.get("working_logged_id") and re.search(r"\b(actually|change|correct|edit|replace|remove|instead)\b|make that|was from", message, re.I)):
        operation = "edit"
        plan = {**plan, "target": session["working_logged_id"]}
    if operation == "remove" and plan["action"] == "drop":
        operation = "lookup"
    metadata = {"operation": operation, "authorized": explicit_log(message) if operation == "log" else
                bool(re.search(r"\b(actually|change|correct|edit|replace|remove|instead)\b|make that|was from", message, re.I)) if operation == "edit" else False,
                "target": plan["target"], "label": plan["label"], "log_date": plan["log_date"]}
    canceled = bool(re.search(r"\b(?:don't|do not|never)\s+(?:add|log|track|record|update)|\bjust (?:look up|lookup)\b", message, re.I))
    if canceled:
        operation = "lookup"
        metadata.update(operation="lookup", authorized=False)
    if pending and following and not canceled and operation in {"lookup", "log", "edit"}:
        if operation == "lookup" and pending.get("operation") in {"log", "edit"}:
            operation = pending["operation"]
        metadata = {**pending.get("request", {}), **{k: v for k, v in metadata.items() if v}}
        metadata["operation"] = operation
    if operation == "log" and not pending and not metadata["log_date"]:
        metadata["log_date"] = data["today"]
    try:
        requested, selections = apply_plan(plan, session, message)
    except ValueError as exc:
        debug["warnings"].append(str(exc))
        return {"kind": "clarification", "message": str(exc), "cards": [], "total": None}

    if plan.get("previous_meal_id") or re.search(r"\bsame (?:thing|meal|lunch|dinner).*yesterday\b", message, re.I):
        try:
            requested, selections = historical_meal(app, message, plan), {}
        except ValueError as exc:
            return {"kind": "clarification", "message": str(exc), "cards": [], "total": None}
        debug["history_reference"] = "Reused verified portions from a historical meal; labels are resolved for the displayed date."
    if not requested:
        return {"kind": "clarification", "message": "What foods and portions should I use for this meal?", "cards": [], "total": None}
    # Old whole-meal corrections remain supported, but losing foods during a follow-up is never silent.
    changed = requested != session.get("foods")
    if changed:
        session["last_result_id"] = None
        if plan["action"] == "set" and not following and operation == "lookup":
            session["working_logged_id"] = None
    session["foods"] = requested
    result = resolve(requested, data["datasets"], data["today"], selections)
    session["selections"] = result.pop("selections")
    pending_result = result.pop("pending")
    debug.update(result.pop("debug"))
    for attempt in debug["matches"]:
        # Anchor resolved and unresolved items to the menu date used on this turn.
        if not requested[attempt["item_index"]]["date"]:
            requested[attempt["item_index"]]["date"] = attempt["effective_date"]
    debug["resolved_foods"] = requested
    if pending_result:
        session["pending"] = {**pending_result, "operation": operation, "request": metadata}
        debug["pending_clarification"] = session["pending"]
        return result
    session["pending"] = None
    label = metadata.get("label") or "Meal"
    # Canonical labels/servings prevent alias/wording changes from bypassing duplicate protection.
    fingerprint = hashlib.sha256(json.dumps([{k: c[k] for k in ("record_key", "quantity")} for c in result["cards"]], sort_keys=True).encode()).hexdigest()
    prior_id = session.get("last_result_id")
    prior = app.memory.result(prior_id) if prior_id else None
    result_id = prior_id if prior and prior.get("fingerprint") == fingerprint else app.memory.save_result(
        conversation_id, {**result, "fingerprint": fingerprint, "label": label})
    result["result_id"] = result_id
    session["last_result_id"] = result_id
    if operation not in {"log", "edit"}:
        return result
    meal = app._meal_target(metadata.get("target", ""), session) if operation == "edit" else None
    day = metadata.get("log_date") or (meal["date"] if meal else data["today"])
    day = date.fromisoformat(day).isoformat()
    caution = app._safe_to_log(result, requested, day)
    # A fresh log must name its foods, or explicitly refer to already established working context.
    if operation == "log" and plan["foods"] and not following and not all(tokens(f["name"]) <= tokens(message) for f in requested):
        caution = "Please name the foods to log, or first verify their UMD labels with me."
    if not metadata.get("authorized") or caution or (operation == "edit" and not meal):
        result.update(kind="clarification", message=caution or ("Which logged meal should I correct?" if operation == "edit" and not meal else "Would you like me to log this verified meal? Please explicitly ask me to add it."))
        session["pending"] = {"focus": None, "unresolved": [], "operation": operation, "request": metadata}
        return result
    if operation == "edit":
        updated = app.memory.replace_meal(meal["id"], result_id)
        session["working_logged_id"] = updated["id"]
        session["last_logged_id"] = updated["id"]
        session["last_action"] = {"type": "edit", "new_id": updated["id"], "old_id": meal["id"]}
        result.update(kind="meal_edited", message=f"Updated {updated['label']} on {updated['date']} using the linked UMD labels.")
        debug["tool_action"] = {"operation": "edit", "entry_id": updated["id"]}
    else:
        logged, added = app.memory.log_result(result_id, day, conversation_id, label_override=label)
        if added:
            session["working_logged_id"] = logged["id"]
            session["last_logged_id"] = logged["id"]
            session["last_action"] = {"type": "log", "entry_id": logged["id"]}
        result.update(kind="meal_logged" if added else "already_logged", message=f"Logged {logged['label']} for {day}." if added else "That meal is already logged; I didn't add it twice.")
        debug["tool_action"] = {"operation": "log", "entry_id": logged["id"], "added": added}
    result["tracker"] = app.memory.tracker()
    return result
