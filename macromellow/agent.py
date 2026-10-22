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
AUTO_MATCH_MIN_SCORE = 0.86
ALTERNATIVE_MARGIN = 0.06


FOOD_ALIASES = {
    "mac n cheese": "macaroni and cheese",
    "mac and cheese": "macaroni and cheese",
    "mac n cheeze": "macaroni and cheese",
    "banannas": "bananas",
    "bananna": "banana",
    "banannna": "banana",
    "choc chip": "chocolate chip",
    "choclate": "chocolate",
    "cilanto": "cilantro",
    "maccaroni": "macaroni",
}


def food_words(value: str) -> list[str]:
    value = value.lower().replace("&", " and ")
    for short, canonical in FOOD_ALIASES.items():
        value = re.sub(r"\\b" + re.escape(short) + r"\\b", canonical, value)
    return re.findall(r"[a-z0-9]+", value)


def tokens(value: str) -> set[str]:
    words = food_words(value)
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


def rank_foods(query: str, foods: list[dict]) -> list[tuple[float, dict]]:
    unique = {(food.get("dining_hall"), food.get("name"), food.get("source_url")): food
              for food in foods if isinstance(food.get("name"), str)}
    return sorted(((value, food) for food in unique.values()
                   if (value := score(query, food["name"])) > 0),
                  key=lambda pair: (-pair[0], pair[1]["name"]))


def choose_food(query: str, foods: list[dict]) -> tuple[dict | None, list[dict]]:
    return select_ranked(rank_foods(query, foods))


def select_ranked(ranked: list[tuple[float, dict]]) -> tuple[dict | None, list[dict]]:
    ranked = [(value, food) for value, food in ranked if value >= 0.55]
    if not ranked:
        return None, []
    best_score, best = ranked[0]
    alternatives = [food for value, food in ranked[:5] if value >= best_score - ALTERNATIVE_MARGIN]
    if best_score < AUTO_MATCH_MIN_SCORE or len({(food["name"], food.get("dining_hall"), food.get("source_url"))
                                for food in alternatives}) > 1:
        return None, [food for _, food in ranked[:4]]
    return best, []


def candidate(food: dict, value: float) -> dict:
    return {"name": food.get("name"), "dining_hall": food.get("dining_hall"),
            "menu_date": food.get("menu_date"), "meal": food.get("meal"),
            "station": food.get("station"), "serving_size": food.get("serving_size"),
            "source_url": food.get("source_url"), "name_score": round(value, 3)}


def calculate(food: dict, quantity: float) -> dict:
    values = {}
    for key in NUTRIENTS:
        value = food.get(key)
        values[key] = round(value * quantity, 1) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return values


def resolve(requested: list[dict], datasets: list[dict], today: str) -> dict:
    by_date = {item["menu_date"]: item for item in datasets}
    dates = sorted(by_date, reverse=True)
    attempts: list[dict] = []
    cards: list[dict] = []
    notes: list[str] = []
    unresolved: list[str] = []
    needs_refresh = False

    def response(kind: str, message: str, total: dict | None = None,
                 options: list[dict] | None = None) -> dict:
        calculations = []
        for card in cards:
            calculations.append({
                "name": card["name"], "quantity": card["quantity"],
                "serving_size": card["serving_size"],
                "source_url": card["source_url"],
                "per_serving": card["per_serving"], "result": card["nutrition"],
            })
        return {"kind": kind, "message": message, "cards": cards, "total": total,
                "options": options or [], "notes": notes, "needs_refresh": needs_refresh,
                "debug": {"matches": attempts, "calculations": calculations,
                          "total": total, "warnings": notes}}

    if not dates:
        return response("needs_data", "No saved UMD menus yet. Refresh today's menu, then try again.")
    for item in requested:
        day = item["date"] or (today if today in by_date else dates[0])
        hall = HALL_NAMES.get(item["hall"])
        attempt = {"requested": item.copy(), "effective_date": day, "effective_hall": hall,
                   "status": "", "selected": None, "candidates": []}
        attempts.append(attempt)
        dataset = by_date.get(day)
        if dataset is None:
            attempt["status"] = "no saved menu for date"
            unresolved.append(f"No saved UMD menu for {day}. Refresh that date if UMD still offers it.")
            notes.append(f"No UMD values were calculated for “{item['name']}” because {day} has no saved menu.")
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
        pool = [food for food in dataset["foods"] if not hall or food.get("dining_hall") == hall]
        ranked = rank_foods(item["name"], pool)
        chosen, options = select_ranked(ranked)
        attempt["candidates"] = [candidate(food, value) for value, food in ranked if value >= 0.35][:4]
        if chosen is None:
            if options:
                attempt["status"] = "ambiguous or low name score"
                label = f"Multiple or uncertain matches for “{item['name']}”"
                unresolved.append(label + ". Please use a more specific menu name.")
                notes.append(f"No UMD label was selected for “{item['name']}”; the candidates are too similar or the name score is too low.")
                return response("clarification", " ".join(unresolved), options=[
                    {"name": f["name"], "hall": f.get("dining_hall"), "date": day}
                    for f in options])
            attempt["status"] = "no reliable match"
            notes.append(f"No UMD label was selected for “{item['name']}”; do not use a different food's nutrition values.")
            unresolved.append(f"No reliable match for “{item['name']}” on {day}"
                              + (f" at {hall}." if hall else ".") + " Try a menu name or another date.")
            continue
        name_score = next(value for value, food in ranked if food is chosen)
        attempt["status"] = "selected"
        attempt["selected"] = candidate(chosen, name_score)
        if not item["date"] and day == today:
            notes.append(f"No date was specified for “{item['name']}”; used today's saved menu ({day}).")
        if not hall:
            notes.append(f"No dining hall was specified for “{item['name']}”; selected its unique label at {chosen.get('dining_hall') or 'an unknown hall'}.")
        quantity = item["quantity"]
        unit = item["unit"]
        if unit.lower() not in {"serving", "servings"}:
            notes.append(f"Assumed {quantity:g} {unit if unit != 'unspecified' else 'unspecified portion(s)'} of “{item['name']}” "
                         f"equals {quantity:g} listed UMD serving(s) ({chosen.get('serving_size') or 'size unavailable'} each).")
        if chosen.get("serving_size") is None:
            notes.append(f"UMD does not list a serving size for “{chosen['name']}”; the portion cannot be checked.")
        if tokens(item["name"]) != tokens(chosen["name"]):
            notes.append(f"Matched “{item['name']}” to “{chosen['name']}” by name (heuristic score {name_score:.2f}); verify the label before relying on this estimate.")
        per_serving = {key: chosen.get(key) for key in NUTRIENTS}
        card = {"requested_name": item["name"], "name": chosen["name"],
                "dining_hall": chosen.get("dining_hall"), "menu_date": day,
                "meal": chosen.get("meal"), "station": chosen.get("station"),
                "quantity": quantity, "unit": unit, "serving_size": chosen.get("serving_size"),
                "source_url": chosen.get("source_url"), "match": "official name" if tokens(item["name"]) == tokens(chosen["name"]) else "closest clear match",
                "name_score": round(name_score, 3), "per_serving": per_serving,
                "nutrition": calculate(chosen, quantity)}
        if any(card["nutrition"][key] is None for key in NUTRIENTS):
            notes.append(f"UMD has missing nutrition information for “{chosen['name']}”; no value was estimated.")
        cards.append(card)
    if unresolved:
        return response("needs_data" if any(text.startswith("No saved") for text in unresolved) else "clarification",
                        " ".join(unresolved))
    total = {key: round(sum(card["nutrition"][key] for card in cards), 1)
             if all(card["nutrition"][key] is not None for card in cards) else None
             for key in NUTRIENTS}
    return response("result", "Estimated from official UMD nutrition labels. "
                    "Values are per listed serving and actual portions may vary.", total)
