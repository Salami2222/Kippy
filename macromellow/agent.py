"""Resolve model-extracted meal requests against saved UMD labels."""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from difflib import SequenceMatcher

from .umd import HALLS


HALL_NAMES = {key: name for key, (name, _) in HALLS.items()}
NUTRIENTS = ("calories", "protein_g", "carbohydrates_g", "fat_g")
UNAVAILABLE_MESSAGE = "UMD states nutrition information is not available for this recipe"
STOP_WORDS = {"a", "an", "the", "of", "and", "with", "some", "serving", "servings"}


def tokens(value: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", value.lower())
    return {word[:-1] if word.endswith("s") and len(word) > 3 else word
            for word in words if word not in STOP_WORDS}


def normalize_intent(intent: dict, previous: list[dict]) -> list[dict]:
    """Validate model output before using it for lookup or calculation."""
    if intent["action"] == "clarify":
        return previous
    foods = intent["foods"]
    if not foods:
        raise ValueError("Please describe a food to look up.")
    normalized = []
    for item in foods:
        if not isinstance(item, dict):
            raise ValueError("The model returned an invalid food request.")
        name, unit, hall, day = (item.get(key) for key in ("name", "unit", "hall", "date"))
        quantity = item.get("quantity")
        if (not isinstance(name, str) or not name.strip() or len(name) > 120
                or not isinstance(unit, str) or len(unit) > 40
                or isinstance(quantity, bool) or not isinstance(quantity, (int, float))
                or not math.isfinite(quantity) or not 0 < quantity <= 100
                or hall not in (*HALLS, "") or not isinstance(day, str)):
            raise ValueError("The model returned an invalid food request. Please rephrase it.")
        if day:
            try:
                day = date.fromisoformat(day).isoformat()
            except ValueError as exc:
                raise ValueError("The model returned an invalid date. Please rephrase it.") from exc
        normalized.append({"name": name.strip(), "quantity": quantity, "unit": unit.strip() or "unspecified",
                           "hall": hall, "date": day})
    if intent["action"] == "add":
        normalized = previous + normalized
    if len(normalized) > 12:
        raise ValueError("Please ask about at most 12 foods at once.")
    return normalized


def score(query: str, name: str) -> float:
    wanted, offered = tokens(query), tokens(name)
    if not wanted or not offered:
        return 0
    overlap = len(wanted & offered) / len(wanted)
    if overlap == 0:
        return 0
    if wanted == offered:
        return 1
    if wanted <= offered:
        return 0.83 + 0.1 * len(wanted) / len(offered)
    return 0.56 * overlap + 0.12 * SequenceMatcher(None, query.lower(), name.lower()).ratio()


def choose_food(query: str, foods: list[dict]) -> tuple[dict | None, list[dict]]:
    unique = {(food.get("dining_hall"), food.get("name"), food.get("source_url")): food
              for food in foods if isinstance(food.get("name"), str)}
    ranked = sorted(((score(query, food["name"]), food) for food in unique.values()),
                    key=lambda pair: (-pair[0], pair[1]["name"]))
    ranked = [(value, food) for value, food in ranked if value >= 0.55]
    if not ranked:
        return None, []
    best_score, best = ranked[0]
    alternatives = [food for value, food in ranked[:5] if value >= best_score - 0.06]
    if best_score < 0.8 or len({(food["name"], food.get("dining_hall"), food.get("source_url"))
                                for food in alternatives}) > 1:
        return None, [food for _, food in ranked[:4]]
    return best, []


def calculate(food: dict, quantity: float) -> dict:
    values = {}
    for key in NUTRIENTS:
        value = food.get(key)
        values[key] = round(value * quantity, 1) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return values


def resolve(requested: list[dict], datasets: list[dict], today: str) -> dict:
    by_date = {item["menu_date"]: item for item in datasets}
    dates = sorted(by_date, reverse=True)
    if not dates:
        return {"kind": "needs_data", "message": "No saved UMD menus yet. Refresh today's menu, then try again.",
                "cards": [], "total": None, "options": []}
    cards, notes, unresolved = [], [], []
    needs_refresh = False
    for item in requested:
        day = item["date"] or (today if today in by_date else dates[0])
        dataset = by_date.get(day)
        if dataset is None:
            unresolved.append(f"No saved UMD menu for {day}. Refresh that date if UMD still offers it.")
            continue
        serious_errors = [error for error in dataset.get("errors", [])
                          if error.get("error") != UNAVAILABLE_MESSAGE]
        if serious_errors:
            note = f"The saved {day} menu reports {len(serious_errors)} retrieval issue(s); check the source labels or refresh."
            if note not in notes:
                notes.append(note)
            needs_refresh = True
        try:
            retrieved = datetime.fromisoformat(dataset["retrieved_at"])
            if (datetime.now(timezone.utc) - retrieved).total_seconds() > 86400:
                note = f"The saved {day} menu was retrieved over 24 hours ago; refresh it if UMD still offers that date."
                if note not in notes:
                    notes.append(note)
                needs_refresh = True
        except (KeyError, TypeError, ValueError):
            pass
        if not item["date"] and day != today:
            note = f"Used the latest saved menu ({day}); today's menu is not saved."
            if note not in notes:
                notes.append(note)
        hall = HALL_NAMES.get(item["hall"])
        pool = [food for food in dataset["foods"] if not hall or food.get("dining_hall") == hall]
        chosen, options = choose_food(item["name"], pool)
        if chosen is None:
            if options:
                label = f"Multiple or uncertain matches for “{item['name']}”"
                unresolved.append(label + ". Please use a more specific menu name.")
                return {"kind": "clarification", "message": " ".join(unresolved), "cards": cards,
                        "total": None, "options": [{"name": f["name"], "hall": f.get("dining_hall"),
                                                   "date": day} for f in options]}
            unresolved.append(f"No reliable match for “{item['name']}” on {day}"
                              + (f" at {hall}." if hall else ".") + " Try a menu name or another date.")
            continue
        quantity = item["quantity"]
        unit = item["unit"]
        if unit.lower() not in {"serving", "servings"}:
            notes.append(f"Assumed {quantity:g} {unit if unit != 'unspecified' else 'unspecified portion(s)'} of “{item['name']}” "
                         f"equals {quantity:g} listed UMD serving(s) ({chosen.get('serving_size') or 'size unavailable'} each).")
        card = {"requested_name": item["name"], "name": chosen["name"],
                "dining_hall": chosen.get("dining_hall"), "menu_date": day,
                "meal": chosen.get("meal"), "station": chosen.get("station"),
                "quantity": quantity, "unit": unit, "serving_size": chosen.get("serving_size"),
                "source_url": chosen.get("source_url"), "match": "official name" if tokens(item["name"]) == tokens(chosen["name"]) else "closest clear match",
                "nutrition": calculate(chosen, quantity)}
        if any(card["nutrition"][key] is None for key in NUTRIENTS):
            notes.append(f"UMD has missing nutrition information for “{chosen['name']}”; no value was estimated.")
        cards.append(card)
    if unresolved:
        return {"kind": "needs_data" if any(text.startswith("No saved") for text in unresolved) else "clarification",
                "message": " ".join(unresolved), "cards": cards, "total": None, "options": []}
    total = {key: round(sum(card["nutrition"][key] for card in cards), 1)
             if all(card["nutrition"][key] is not None for card in cards) else None
             for key in NUTRIENTS}
    return {"kind": "result", "message": "Estimated from official UMD nutrition labels. "
            "Values are per listed serving and actual portions may vary.", "cards": cards,
            "total": total, "notes": notes, "options": [], "needs_refresh": needs_refresh}
