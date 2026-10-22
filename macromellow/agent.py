"""Deterministic label matching, reported portions, and partial meal resolution."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from fractions import Fraction

from .umd import HALLS

HALL_NAMES = {key: name for key, (name, _) in HALLS.items()}
NUTRIENTS = ("calories", "protein_g", "carbohydrates_g", "fat_g")
UNAVAILABLE_MESSAGE = "UMD states nutrition information is not available for this recipe"
STOP_WORDS = {"a", "an", "the", "of", "and", "with", "some", "serving", "servings"}
AUTO_MATCH_MIN_SCORE = .91
ALTERNATIVE_MARGIN = .12


def name_words(value):
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower().replace("&", " and ")
    value = re.sub(r"\bmac(?:aroni)?\s+(?:n|and|'n')\s+cheese\b", "macaroni cheese", value)
    value = re.sub(r"\bmac\s+cheese\b", "macaroni cheese", value)
    value = re.sub(r"\bbbq\b", "barbecue", value)
    value = re.sub(r"\bgarbanzo(?:s)?\b", "chickpea", value)
    words = re.findall(r"[a-z0-9]+", value)
    return [word[:-1] if word.endswith("s") and not word.endswith(("ss", "us")) and len(word) > 3 else word
            for word in words if word not in STOP_WORDS]


def tokens(value):
    return set(name_words(value))


def normalize_intent(intent, previous):
    """Validate food fields; never accept source facts or conversions from the model."""
    from .ollama import normalize_plan
    plan = normalize_plan(intent)
    if plan["action"] == "clarify":
        return previous
    normalized = [{"quantity": 1, "unit": "unspecified", "hall": "", "date": "", **f} for f in plan["foods"]]
    if not normalized:
        raise ValueError("What food would you like me to look up?")
    for food in normalized:
        food["name"] = food["name"].strip()
        food["unit"] = food["unit"].strip() or "unspecified"
    if plan["action"] == "add":
        normalized = previous + normalized
    if len(normalized) > 12:
        raise ValueError("Please ask about at most 12 foods at once.")
    return normalized


def match_signals(query, name):
    wanted, offered = name_words(query), name_words(name)
    if not wanted or not offered:
        return {"coverage": 0, "precision": 0, "characters": 0, "score": 0}
    exact = Counter(wanted) == Counter(offered)
    remaining = offered.copy()
    similarities = []
    for word in wanted:
        if not remaining:
            similarities.append(0)
            continue
        values = [(1.0 if word == other else SequenceMatcher(None, word, other).ratio()
                   if min(len(word), len(other)) >= 4 else 0, i) for i, other in enumerate(remaining)]
        similarity, index = max(values)
        if similarity >= .82:
            remaining.pop(index)
            similarities.append(similarity)
        else:
            similarities.append(0)
    coverage = sum(similarities) / len(wanted)
    precision = sum(similarities) / len(offered)
    characters = SequenceMatcher(None, " ".join(wanted), " ".join(offered)).ratio()
    # Missing a requested ingredient/body part is never a safe automatic match.
    if any(value == 0 for value in similarities):
        value = .5 * coverage + .1 * characters
    elif exact:
        value = 1.0
    else:
        value = .55 * coverage + .25 * precision + .2 * characters
    # Reject cross-dish substitutions even when shared modifiers are numerous.
    dish_types = {"pizza", "cookie", "rice", "soup", "salad", "sauce", "sandwich", "pasta", "chicken", "beef", "fish", "tofu"}
    if (set(wanted) & dish_types) - set(offered):
        value = min(value, .49)
    return {"coverage": round(coverage, 3), "precision": round(precision, 3),
            "characters": round(characters, 3), "score": round(value, 3)}


def score(query, name):
    return match_signals(query, name)["score"]


def record_key(food):
    identity = {key: food.get(key) for key in ("name", "dining_hall", "menu_date", "source_url", "serving_size", *NUTRIENTS)}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]


def rank_foods(query, foods):
    unique = {record_key(food): food for food in foods if isinstance(food.get("name"), str)}
    return sorted(((value, food) for food in unique.values() if (value := score(query, food["name"])) > 0),
                  key=lambda pair: (-pair[0], pair[1]["name"], pair[1].get("dining_hall", "")))


def label_identity(food):
    return tuple(food.get(key) for key in ("name", "menu_date", "source_url", "serving_size", *NUTRIENTS))


def select_ranked(ranked):
    ranked = [(value, food) for value, food in ranked if value >= .55]
    if not ranked:
        return None, []
    best_score, best = ranked[0]
    # Exact, descriptive names beat qualified variants; generic mac & cheese remains ambiguous.
    margin = .035 if best_score == 1 and len(name_words(best["name"])) >= 3 else ALTERNATIVE_MARGIN
    competitors = [food for value, food in ranked if value >= best_score - margin]
    if best_score < AUTO_MATCH_MIN_SCORE or len({label_identity(f) for f in competitors}) > 1:
        return None, [food for _, food in ranked[:4]]
    return best, []


def choose_food(query, foods):
    return select_ranked(rank_foods(query, foods))


def candidate(food, value, query=""):
    return {"record_key": record_key(food), "name": food.get("name"), "dining_hall": food.get("dining_hall"),
            "menu_date": food.get("menu_date"), "meal": food.get("meal"), "station": food.get("station"),
            "serving_size": food.get("serving_size"), "source_url": food.get("source_url"),
            "name_score": round(value, 3), "signals": match_signals(query, food["name"]) if query else {}}


def unit_name(unit):
    unit = unit.strip().lower()
    return {"servings": "serving", "scoops": "scoop", "slices": "slice", "cookies": "each", "item": "each", "items": "each",
            "ea": "each", "ounces": "oz", "ounce": "oz", "grams": "g", "gram": "g", "cups": "cup", "pieces": "piece"}.get(unit, unit)


def listed_portion(value):
    if not isinstance(value, str):
        return None
    match = re.match(r"^\s*(\d+(?:\.\d+)?(?:/\d+)?)\s*(oz|ounces?|g|grams?|cups?|each|ea|items?|slices?|pieces?|scoops?)\b", value, re.I)
    if not match:
        return None
    try:
        amount = float(Fraction(match[1]))
        return (amount, unit_name(match[2])) if amount > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def portion_factor(item, food):
    listed = listed_portion(food.get("serving_size"))
    unit = unit_name(item["unit"])
    if not food.get("serving_size"):
        return None, "UMD does not list a serving size."
    if unit == "serving":
        return item["quantity"], "Reported listed servings."
    amount = item.get("portion_amount", 1)
    measured_unit = unit_name(item.get("portion_unit", unit))
    if listed:
        size, listed_unit = listed
        if measured_unit == listed_unit:
            return round(item["quantity"] * amount / size, 6), "Reported units match the listed UMD unit."
        if {measured_unit, listed_unit} <= {"oz", "g"}:
            reported_grams = item["quantity"] * amount * (28.349523125 if measured_unit == "oz" else 1)
            listed_grams = size * (28.349523125 if listed_unit == "oz" else 1)
            return round(reported_grams / listed_grams, 6), "Converted measured weight using 1 oz = 28.349523125 g."
    return None, f"The reported {item['quantity']:g} {item['unit']} cannot yet be converted to {food.get('serving_size')}."


def calculate(food, quantity):
    return {key: round(food[key] * quantity, 1) if type(food.get(key)) in {int, float}
            and math.isfinite(food[key]) and food[key] >= 0 else None for key in NUTRIENTS}


def resolve(requested, datasets, today, selections=None):
    by_date = {item["menu_date"]: item for item in datasets}
    selections = selections or {}
    attempts, cards, notes, unresolved, selected = [], [], [], [], {}
    needs_refresh = False
    for index, item in enumerate(requested):
        day = item["date"] or (today if today in by_date else max(by_date, default=today))
        hall = HALL_NAMES.get(item["hall"])
        attempt = {"item_index": index, "requested": item.copy(), "effective_date": day, "effective_hall": hall,
                   "status": "", "selected": None, "candidates": []}
        attempts.append(attempt)
        dataset = by_date.get(day)
        if not dataset:
            attempt["status"] = "no saved menu for date"
            unresolved.append({"item_index": index, "field": "date", "question": f"I don't have a saved UMD menu for {day}. Could you refresh it or choose a saved date?", "options": []})
            continue
        if not item["date"] and day != today:
            notes.append(f"Used the latest saved menu ({day}); today's menu is not saved. This does not confirm today's offerings.")
        if any(e.get("error") != UNAVAILABLE_MESSAGE for e in dataset.get("errors", [])):
            notes.append(f"The saved {day} menu has retrieval issues; verify the labels or refresh.")
            needs_refresh = True
        try:
            if (datetime.now(timezone.utc) - datetime.fromisoformat(dataset["retrieved_at"])).total_seconds() > 86400:
                note = f"The saved {day} menu was retrieved over 24 hours ago; refresh it if available."
                if note not in notes:
                    notes.append(note)
                needs_refresh = True
        except (KeyError, TypeError, ValueError):
            pass
        pool = [f for f in dataset["foods"] if not hall or f.get("dining_hall") == hall]
        ranked = rank_foods(item["name"], pool)
        chosen = next((f for f in pool if record_key(f) == selections.get(str(index))), None)
        confirmed_choice = chosen is not None
        if chosen:
            options = []
        else:
            chosen, options = select_ranked(ranked)
        attempt["candidates"] = [candidate(f, v, item["name"]) for v, f in ranked if v >= .55][:4]
        if not chosen:
            attempt["status"] = "ambiguous or low name score" if options else "no reliable match"
            choices = [{**candidate(f, score(item["name"], f["name"]), item["name"]), "hall": f.get("dining_hall"), "date": day} for f in options]
            if choices:
                if len({c["name"] for c in choices}) == 1 and len({c["hall"] for c in choices}) > 1:
                    question = f"Which dining hall was your {item['name']} from? These halls have separate UMD labels."
                else:
                    question = f"For {item['name']}, did you mean " + " or ".join(c["name"] for c in choices[:3]) + "?"
            else:
                question = f"I couldn't find a reliable UMD match for {item['name']} on {day}. Do you know its menu name or dining hall?"
            unresolved.append({"item_index": index, "field": "name", "question": question, "options": choices})
            continue
        value = score(item["name"], chosen["name"])
        selected[str(index)] = record_key(chosen)
        attempt.update(status="selected", selected=candidate(chosen, value, item["name"]))
        factor, conversion = portion_factor(item, chosen)
        attempt["portion_conversion"] = conversion
        if factor is None:
            question = (f"About how big was each {unit_name(item['unit'])} of {item['name']}? "
                        f"UMD lists {chosen.get('serving_size') or 'no serving size'} per serving. "
                        "Does one reported portion match one listed serving, or do you know its weight?")
            unresolved.append({"item_index": index, "field": "portion", "question": question, "options": []})
        equivalent_halls = sorted({f.get("dining_hall", "Unknown") for _, f in ranked if label_identity(f) == label_identity(chosen)})
        if not hall and len(equivalent_halls) > 1:
            notes.append(f"{chosen['name']} uses the same official label at {', '.join(equivalent_halls)}. The source card uses {chosen.get('dining_hall')}; your dining hall wasn't specified.")
        if tokens(item["name"]) != tokens(chosen["name"]):
            notes.append(f"Matched {item['name']} to {chosen['name']} ({value:.2f} name score); check the official label.")
        nutrition = calculate(chosen, factor) if factor is not None else dict.fromkeys(NUTRIENTS)
        card = {"item_index": index, "requested_name": item["name"], "name": chosen["name"],
                "dining_hall": chosen.get("dining_hall"), "menu_date": day, "meal": chosen.get("meal"),
                "station": chosen.get("station"), "quantity": factor, "reported_quantity": item["quantity"], "unit": item["unit"],
                "serving_size": chosen.get("serving_size"), "source_url": chosen.get("source_url"),
                "record_key": record_key(chosen), "portion_confirmed": factor is not None, "conversion": conversion,
                "match": "confirmed choice" if confirmed_choice else "official name" if value == 1 else "high-confidence name",
                "name_score": value, "per_serving": {k: chosen.get(k) for k in NUTRIENTS}, "nutrition": nutrition}
        if factor is not None and any(v is None for v in nutrition.values()):
            notes.append(f"UMD has missing nutrition information for {chosen['name']}; no value was estimated.")
        cards.append(card)
    notes = list(dict.fromkeys(notes))
    confirmed = [c for c in cards if c["portion_confirmed"]]
    subtotal = {key: round(sum(c["nutrition"][key] for c in confirmed), 1)
                if all(c["nutrition"][key] is not None for c in confirmed) else None for key in NUTRIENTS} if confirmed else None
    complete = not unresolved and len(cards) == len(requested) and bool(cards)
    total = subtotal if complete else None
    focus = unresolved[0] if unresolved else None
    if focus:
        known = [c["name"] for c in cards]
        message = ("I found " + ", ".join(known[:4]) + ". " if known else "") + focus["question"]
        if confirmed:
            message += " The subtotal below covers confirmed portions only."
    else:
        message = "Here's your meal from the linked UMD labels. Actual portions may vary."
    kind = ("needs_data" if focus and focus["field"] == "date" else "clarification") if focus else "result"
    calculations = [{"name": c["name"], "quantity": c["quantity"], "reported_quantity": c["reported_quantity"],
                     "unit": c["unit"], "conversion": c["conversion"], "serving_size": c["serving_size"],
                     "source_url": c["source_url"], "per_serving": c["per_serving"], "result": c["nutrition"]} for c in confirmed]
    return {"kind": kind, "message": message, "cards": cards, "total": total,
            "confirmed_subtotal": subtotal if not complete else None, "complete": complete,
            "nutrition_complete": complete and all(v is not None for v in (total or {}).values()),
            "options": focus["options"] if focus else [], "notes": notes, "needs_refresh": needs_refresh,
            "unresolved_items": [{"name": requested[u["item_index"]]["name"], "field": u["field"]} for u in unresolved],
            "selections": selected, "pending": {"focus": focus, "unresolved": unresolved} if unresolved else None,
            "debug": {"matches": attempts, "calculations": calculations, "total": total, "warnings": notes}}
