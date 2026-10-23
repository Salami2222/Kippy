"""Conservative common-food nutrition from USDA FoodData Central.

Only specifically mapped staple names may be automatically selected. All nutrients
come from USDA API responses (per 100 g), never from language-model output.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from contextlib import closing
from http.client import HTTPException
from threading import Lock
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
    "medium banana": "banana", "medium apple": "apple", "medium orange": "orange",
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


def common_candidate(name):
    """Route unsupported variations to clarification, never to a UMD substitute."""
    if is_common_food(name):
        return True
    words = set(clean(name).split())
    modifiers = {"raw", "cooked", "boiled", "hard", "fried", "scrambled", "poached", "grilled",
                 "breaded", "steamed", "baked", "roasted", "dried", "large", "small", "medium", "jumbo",
                 "whole", "whites", "white", "yolks", "yolk", "skin", "skinless", "with", "without"}
    for base in (*COMMON_FOODS, *ALIASES):
        base_words = set(base.split())
        if base_words <= words and words - base_words <= modifiers:
            return True
    return False


def extract_macros(food):
    """Accept complete, finite USDA nutrients with their published units."""
    entries = food.get("foodNutrients") if isinstance(food, dict) else None
    if not isinstance(entries, list):
        return None
    nutrients = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        nested = entry.get("nutrient")
        nested = nested if isinstance(nested, dict) else {}
        try:
            number = int(entry.get("nutrientId", nested.get("id", 0)))
        except (ValueError, TypeError):
            continue
        if number not in (*NUTRIENT_IDS, 2047, 2048):
            continue
        value = entry.get("value", entry.get("amount"))
        unit = str(entry.get("unitName", nested.get("unitName", ""))).lower()
        expected = "kcal" if number in (1008, 2047, 2048) else "g"
        if (unit != expected or type(value) not in (int, float) or not math.isfinite(value)
                or not 0 <= value <= (1000 if expected == "kcal" else 100)):
            return None
        if number in nutrients and nutrients[number] != value:
            return None
        nutrients[number] = value
    if 1008 not in nutrients:
        nutrients[1008] = nutrients.get(2047, nutrients.get(2048))
    if not all(type(nutrients.get(k)) in (int, float) for k in NUTRIENT_IDS):
        return None
    return {name: float(nutrients[key]) for key, name in NUTRIENT_IDS.items()}


def valid_cached(value, description):
    if (not isinstance(value, dict) or value.get("validation_version") != 1 or type(value.get("fdc_id")) is not int or value["fdc_id"] <= 0
            or value.get("data_type") not in ("Foundation", "SR Legacy")
            or clean(value.get("description")) != clean(description)):
        return False
    macros = value.get("per_100g")
    return isinstance(macros, dict) and set(macros) == set(NUTRIENT_IDS.values()) and all(
        type(v) in (int, float) and math.isfinite(v) and 0 <= v <= (1000 if k == "calories" else 100)
        for k, v in macros.items())


class USDAClient:
    """Server-only API key, validated positive cache, exact-description selection."""
    def __init__(self, db_path, api_key=None):
        self.db_path = db_path
        self.api_key = api_key if api_key is not None else os.getenv("USDA_API_KEY", "DEMO_KEY")
        self.lock = Lock()
        with closing(sqlite3.connect(self.db_path)) as con, con:
            con.execute("CREATE TABLE IF NOT EXISTS usda_food_cache "
                        "(food_key TEXT PRIMARY KEY, fetched_at INTEGER NOT NULL, value_json TEXT NOT NULL)")

    def lookup(self, name):
        key = common_key(name)
        if key is None:
            return None
        with self.lock:
            return self._lookup(key)

    def _lookup(self, key):
        now = int(time.time())
        description = COMMON_FOODS[key][0]
        try:
            with closing(sqlite3.connect(self.db_path)) as con:
                cached = con.execute("SELECT fetched_at, value_json FROM usda_food_cache WHERE food_key=?", (key,)).fetchone()
            if cached and type(cached[0]) is int and 0 <= now - cached[0] < CACHE_SECONDS:
                try:
                    value = json.loads(cached[1])
                except (ValueError, TypeError):
                    value = None
                if valid_cached(value, description):
                    return value
        except sqlite3.Error:
            raise USDAError("The USDA nutrition cache is unavailable. Please try again later.") from None
        if not self.api_key:
            raise USDAError("Set USDA_API_KEY on the server to look up common-food nutrition.")
        body = json.dumps({"query": description, "dataType": ["Foundation", "SR Legacy"], "pageSize": 100}).encode("utf-8")
        request = Request(API + "?api_key=" + quote(self.api_key, safe=""), body,
                          {"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=12) as response:
                raw = response.read(8_388_609)
                if len(raw) > 8_388_608:
                    raise ValueError("Response exceeds size limit")
                payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get("foods"), list):
                raise ValueError("Expected foods list")
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if code == 429:
                raise USDAError("USDA's API rate limit was reached. Configure your own USDA_API_KEY or retry later.") from None
            if code in (401, 403):
                raise USDAError("USDA rejected access. Check the server's USDA_API_KEY and try again.") from None
            raise USDAError("USDA nutrition is temporarily unavailable.") from None
        except (URLError, TimeoutError, OSError, ValueError, HTTPException):
            raise USDAError("USDA nutrition is temporarily unavailable.") from None
        exact = [food for food in payload["foods"] if isinstance(food, dict)
                 and food.get("dataType") in ("Foundation", "SR Legacy")
                 and clean(food.get("description", "")) == clean(description)
                 and type(food.get("fdcId")) is int and food["fdcId"] > 0]
        selected = None
        for data_type in ("Foundation", "SR Legacy"):
            matches = {food["fdcId"]: (food, macros) for food in exact
                       if food["dataType"] == data_type and (macros := extract_macros(food)) is not None}
            if len(matches) > 1:
                raise USDAError("Multiple exact USDA records match this food. I won't select one silently.")
            if matches:
                selected = next(iter(matches.values()))
                break
        if selected is None:
            raise USDAError("No complete exact USDA entry was found for this food. I won't guess its macros.")
        food, macros = selected
        value = {"validation_version": 1, "fdc_id": food["fdcId"], "description": food["description"],
                 "data_type": food["dataType"], "per_100g": macros}
        try:
            with closing(sqlite3.connect(self.db_path)) as con, con:
                con.execute("INSERT OR REPLACE INTO usda_food_cache VALUES (?,?,?)", (key, now, json.dumps(value)))
        except sqlite3.Error:
            raise USDAError("The USDA nutrition cache could not be saved. Please try again later.") from None
        return value


def mentions_umd(message):
    return bool(re.search(r"\b(umd|dining hall|south campus|251(?: north)?|the y|yahentamitsi)\b", message, re.I))


def resolve_common(client, requested, day, log_date, message):
    """Produce source cards; the existing meal pipeline owns all actions and memory."""
    def unresolved(text, field="portion", notes=None, requires_new_food=False):
        focus = {"item_index": 0, "field": field, "options": []}
        return {"kind": "clarification", "message": text, "cards": [], "total": None,
                "options": [], "notes": notes or [], "selections": {},
                "pending": {"focus": focus, "unresolved": [focus], "requires_new_food": requires_new_food},
                "debug": {"matches": [], "calculations": [], "total": None, "warnings": notes or [text]}}
    if len(requested) != 1 or requested[0].get("hall"):
        return unresolved("Please look up one common food at a time. I won't combine USDA foods with UMD dining labels in this version.", "source", requires_new_food=True)
    item = requested[0]
    if not is_common_food(item["name"]):
        return unresolved(f"I don't have an exact supported USDA mapping for {item['name']}. Please name a supported food/preparation and portion; I won't substitute another dish.", "source", requires_new_food=True)
    # Never silently stamp a historical/future request as today's food, even if the model omits its date.
    if (any(value != day for value in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", message))
            or (item.get("date") and item["date"] != day) or (log_date and log_date != day)
            or re.search(r"\b(yesterday|tomorrow|last (?:night|week|monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b", message, re.I)):
        return unresolved("USDA common-food logging currently supports today only. Please confirm the food and portion for today.", "date", requires_new_food=True)
    key = common_key(item["name"])
    preparations = set(re.findall(r"\b(raw|cooked|boiled|fried|scrambled|poached|grilled|breaded|steamed|baked|roasted|dried)\b", message, re.I))
    allowed = {"egg": {"raw"}, "boiled egg": {"boiled", "cooked"},
               "white rice": {"cooked", "boiled"}, "brown rice": {"cooked", "boiled"},
               "chicken breast": {"cooked", "roasted"}, "black bean": {"cooked", "boiled"}}
    if {word.lower() for word in preparations} - allowed.get(key, {"raw"}):
        return unresolved("The preparation you described does not match this USDA reference. Please specify an exact supported food/preparation; I won't substitute a different dish.", "source", requires_new_food=True)
    if key == "egg" and re.search(r"\b(whites?|yolks?|small|medium|jumbo|duck|quail)\b", message, re.I):
        return unresolved("This reference is for large whole chicken eggs. Please confirm that food and portion, or give a supported exact food.", "source", requires_new_food=True)
    default_weight, default_name = COMMON_FOODS[key][1:]
    amount = item["quantity"]
    unit = item["unit"].strip().lower()
    measured_unit = item.get("portion_unit", unit)
    portion_amount = item.get("portion_amount", 1)
    if measured_unit in ("g", "gram", "grams", "oz", "ounce", "ounces"):
        measured = amount * portion_amount
        grams = measured * (28.349523125 if measured_unit in ("oz", "ounce", "ounces") else 1)
        description = f"{measured:g} {measured_unit}"
        assumption = "Using your reported edible food weight."
    elif unit in ("unspecified", "each", "piece", "pieces", "item", "items") and default_weight:
        grams = amount * default_weight
        description = f"{amount:g} {default_name}{'' if amount == 1 else 's'}"
        assumption = f"Assuming {default_weight:g} g edible weight per {default_name}; not a measured portion."
    else:
        return unresolved(f"What weight of {item['name']} did you have? USDA values are per 100 g; please tell me grams or ounces so I don't guess your portion.")
    if type(grams) not in (int, float) or not math.isfinite(grams) or not 0 < grams <= 100000:
        return unresolved("Please give a realistic portion size.")
    try:
        food = client.lookup(item["name"])
    except USDAError as exc:
        return unresolved(str(exc), "source")
    factor = grams / 100.0
    nutrition = {field: round(value * factor, 1) for field, value in food["per_100g"].items()}
    link = f"https://fdc.nal.usda.gov/food-details/{food['fdc_id']}/nutrients"
    notes = [assumption, f"Using {food['data_type']} data; preparation and added ingredients can change nutrition."]
    if key == "egg":
        notes.append("This is the USDA whole-egg reference, not a prepared egg dish; added oil, butter, milk, or fillings are not included.")
    card = {"name": food["description"], "requested_name": item["name"], "source": SOURCE,
            "source_url": link, "record_key": f"usda:{food['fdc_id']}", "dining_hall": "USDA common foods", "menu_date": day,
            "quantity": round(factor, 5), "reported_quantity": amount, "unit": unit,
            **{k: item[k] for k in ("portion_amount", "portion_unit") if k in item},
            "serving_size": "100 g", "grams": round(grams, 2), "portion_description": description,
            "portion_assumption": assumption, "portion_confirmed": True, "match": "official name",
            "per_serving": food["per_100g"], "nutrition": nutrition, "conversion": "USDA per-100g scaling"}
    return {"kind": "result", "source": SOURCE,
            "message": f"{description.capitalize()}: about {nutrition['protein_g']:g} g protein and {nutrition['calories']:g} calories (USDA reference estimate).",
            "cards": [card], "total": nutrition, "complete": True, "nutrition_complete": True,
            "options": [], "notes": notes, "selections": {}, "pending": None,
            "debug": {"matches": [{"item_index": 0, "requested": item.copy(), "selected": card.copy(), "source": SOURCE,
                "food_id": food["fdc_id"], "status": "exact USDA description", "effective_date": day, "source_url": link}],
                "calculations": [{"name": food["description"], "quantity": factor, "unit": unit,
                    "conversion": "USDA per-100g scaling", "serving_size": "100 g", "source_url": link,
                    "per_serving": food["per_100g"], "result": nutrition}], "total": nutrition, "warnings": notes}}
