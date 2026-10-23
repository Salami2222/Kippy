"""Conservative common-food nutrition from USDA FoodData Central.

Only specifically mapped staple names may be automatically selected. All nutrients
come from USDA API responses (per 100 g), never from language-model output.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


SOURCE = "USDA FoodData Central"
API = "https://api.nal.usda.gov/fdc/v1/foods/search"
CACHE_SECONDS = 90 * 86400

# Exact USDA descriptions. Defaults for a counted item are intentionally limited
# to recognisable standard sizes, and are disclosed in the response.
COMMON_FOODS = {
    "egg": ("Eggs, Grade A, Large, egg whole", 50.3, "large egg"),
    "boiled egg": ("Egg, whole, cooked, hard-boiled", 50.0, "large boiled egg"),
    "banana": ("Bananas, raw", 118.0, "medium banana"),
    "apple": ("Apples, raw, with skin", 182.0, "medium apple"),
    "broccoli": ("Broccoli, raw", None, None),
    "white rice": ("Rice, white, long-grain, regular, enriched, cooked", None, None),
    "brown rice": ("Rice, brown, long-grain, cooked", None, None),
    "chicken breast": ("Chicken, broilers or fryers, breast, meat only, cooked, roasted", None, None),
    "strawberry": ("Strawberries, raw", None, None),
    "orange": ("Oranges, raw, all commercial varieties", 131.0, "medium orange"),
    "almond": ("Nuts, almonds", None, None),
    "peanut butter": ("Peanut butter, smooth style, with salt", None, None),
    "black bean": ("Beans, black, mature seeds, cooked, boiled, without salt", None, None),
}
ALIASES = {
    "eggs": "egg", "large egg": "egg", "large eggs": "egg",
    "hard boiled egg": "boiled egg", "hard boiled eggs": "boiled egg",
    "boiled eggs": "boiled egg",
    "bananas": "banana", "apples": "apple", "strawberries": "strawberry",
    "oranges": "orange", "almonds": "almond", "black beans": "black bean",
    "chicken breasts": "chicken breast", "cooked chicken breast": "chicken breast",
    "cooked chicken breasts": "chicken breast",
}
NUTRIENT_IDS = {1008: "calories", 1003: "protein_g", 1005: "carbohydrates_g", 1004: "fat_g"}


class USDAError(Exception):
    pass


def clean(value):
    return " ".join(re.findall(r"[a-z0-9]+", str(value).lower()))


def common_key(name):
    name = clean(name)
    return ALIASES.get(name, name) if ALIASES.get(name, name) in COMMON_FOODS else None


def is_common_food(name):
    return common_key(name) is not None


def extract_macros(food):
    nutrients = {}
    for entry in food.get("foodNutrients", []):
        try:
            number = int(entry.get("nutrientId", (entry.get("nutrient") or {}).get("id", 0)))
            value = entry.get("value", entry.get("amount"))
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                nutrients[number] = value
        except (ValueError, TypeError, AttributeError):
            continue
    if 1008 not in nutrients:
        nutrients[1008] = nutrients.get(2047, nutrients.get(2048))
    if not all(type(nutrients.get(k)) in (int, float) for k in NUTRIENT_IDS):
        return None
    return {name: float(nutrients[key]) for key, name in NUTRIENT_IDS.items()}


class USDAClient:
    """Server-only API key, persistent positive cache, exact-description selection."""
    def __init__(self, db_path, api_key=None):
        self.db_path = db_path
        self.api_key = api_key if api_key is not None else os.getenv("USDA_API_KEY", "DEMO_KEY")
        with sqlite3.connect(self.db_path) as con:
            con.execute("CREATE TABLE IF NOT EXISTS usda_food_cache "
                        "(food_key TEXT PRIMARY KEY, fetched_at INTEGER NOT NULL, value_json TEXT NOT NULL)")

    def lookup(self, name):
        key = common_key(name)
        if key is None:
            return None
        now = int(time.time())
        with sqlite3.connect(self.db_path) as con:
            cached = con.execute("SELECT fetched_at, value_json FROM usda_food_cache WHERE food_key=?", (key,)).fetchone()
        if cached and now - cached[0] < CACHE_SECONDS:
            return json.loads(cached[1])
        description = COMMON_FOODS[key][0]
        if not self.api_key:
            raise USDAError("Set USDA_API_KEY on the server to look up common-food nutrition.")
        body = json.dumps({"query": description, "dataType": ["Foundation", "SR Legacy"], "pageSize": 100}).encode("utf-8")
        request = Request(API + "?api_key=" + quote(self.api_key, safe=""), body,
                          {"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=12) as response:
                payload = json.load(response)
        except HTTPError as exc:
            if exc.code == 429:
                raise USDAError("USDA's API rate limit was reached. Configure your own USDA_API_KEY.") from None
            raise USDAError("USDA nutrition is temporarily unavailable.") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise USDAError("USDA nutrition is temporarily unavailable.") from None
        exact = [food for food in payload.get("foods", []) if food.get("dataType") in ("Foundation", "SR Legacy")
                 and clean(food.get("description", "")) == clean(description)
                 and type(food.get("fdcId")) is int]
        exact.sort(key=lambda food: 0 if food["dataType"] == "Foundation" else 1)
        selected = next(((food, macros) for food in exact if (macros := extract_macros(food)) is not None), None)
        if selected is None:
            raise USDAError("No complete exact USDA entry was found for this food. I won't guess its macros.")
        food, macros = selected
        value = {"fdc_id": food["fdcId"], "description": food["description"],
                 "data_type": food["dataType"], "per_100g": macros}
        with sqlite3.connect(self.db_path) as con:
            con.execute("INSERT OR REPLACE INTO usda_food_cache VALUES (?,?,?)",
                        (key, now, json.dumps(value)))
        return value


def common_turn(app, plan, session, conversation_id, day, debug, message):
    """Answer one mapped common-food question, and only explicitly log when asked."""
    item = {"quantity": 1, "unit": "unspecified", **plan["foods"][0]}
    # Invalidate any prior result before attempting this new food, including if
    # the new portion cannot be resolved or the API fails.
    session["last_result_id"] = None
    session["foods"] = []
    key = common_key(item["name"])
    if key is None:
        raise ValueError("Unknown common food")
    default_weight, default_name = COMMON_FOODS[key][1:]
    amount = item["quantity"]
    unit = item["unit"].strip().lower()
    if unit in ("g", "gram", "grams"):
        grams = amount
        description = f"{amount:g} g"
    elif unit in ("oz", "ounce", "ounces"):
        grams = amount * 28.349523125
        description = f"{amount:g} oz"
    elif unit in ("unspecified", "each", "piece", "pieces", "item", "items", "serving", "servings") and default_weight:
        grams = amount * default_weight
        description = f"{amount:g} {default_name}{'' if amount == 1 else 's'}"
    else:
        return {"kind": "clarification", "message":
                f"What weight of {item['name']} did you have? USDA values are per 100 g; "
                "please tell me grams or ounces so I don't guess your portion.",
                "cards": [], "total": None}
    if not isinstance(grams, (int, float)) or not math.isfinite(grams) or not 0 < grams <= 100000:
        return {"kind": "clarification", "message": "Please give a realistic portion size.",
                "cards": [], "total": None}
    try:
        food = app.usda.lookup(item["name"])
    except USDAError as exc:
        return {"kind": "clarification", "message": str(exc), "cards": [], "total": None}
    factor = grams / 100.0
    nutrition = {field: round(value * factor, 1) for field, value in food["per_100g"].items()}
    link = f"https://fdc.nal.usda.gov/food-details/{food['fdc_id']}/nutrients"
    card = {"name": food["description"], "requested_name": item["name"],
            "source": SOURCE, "source_url": link, "record_key": f"usda:{food['fdc_id']}",
            "dining_hall": "USDA common foods", "menu_date": day,
            "quantity": round(factor, 5), "reported_quantity": amount, "unit": unit,
            "serving_size": "100 g", "grams": round(grams, 1), "portion_description": description,
            "portion_confirmed": True, "match": "official name",
            "per_serving": food["per_100g"], "nutrition": nutrition, "conversion": "USDA per-100g scaling"}
    digest = hashlib.sha256(json.dumps({"food": card["record_key"], "grams": round(grams, 3)}, sort_keys=True).encode()).hexdigest()
    result = {"kind": "result", "source": SOURCE, "message":
              f"{description.capitalize()} contains about {nutrition['protein_g']:g} g protein "
              f"and {nutrition['calories']:g} calories (USDA reference estimate).",
              "cards": [card], "total": nutrition, "complete": True, "nutrition_complete": True,
              "notes": [f"Using {food['data_type']} data. Portion: {description}; actual foods vary."],
              "fingerprint": digest, "label": description.capitalize()}
    result_id = app.memory.save_result(conversation_id, result)
    result["result_id"] = result_id
    session["foods"] = [{"name": item["name"], "quantity": amount, "unit": unit, "hall": "", "date": ""}]
    session["last_result_id"] = result_id
    debug["matches"] = [{"source": SOURCE, "food_id": food["fdc_id"], "status": "exact USDA description"}]
    debug["calculations"] = [{"name": food["description"], "quantity": factor, "unit": unit,
                              "conversion": "USDA per-100g scaling", "serving_size": "100 g",
                              "source_url": link, "per_serving": food["per_100g"], "result": nutrition}]
    debug["total"] = nutrition
    if plan["operation"] == "log":
        from .meals import explicit_log
        if not explicit_log(message):
            return result
        entry, added = app.memory.log_result(result_id, day, conversation_id)
        session["last_logged_id"] = session["working_logged_id"] = entry["id"]
        if added:
            session["last_action"] = {"type": "log", "entry_id": entry["id"]}
        result.update(kind="meal_logged" if added else "already_logged",
                      message=f"Logged {description} using USDA reference nutrition." if added else "That food is already logged.",
                      tracker=app.memory.tracker())
        debug["tool_action"] = {"operation": "log", "entry_id": entry["id"], "added": added}
    return result
