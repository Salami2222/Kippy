"""USDA common-food source, units, and logging tests (offline fixtures)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from macromellow.usda import USDAClient, USDAError, clean, common_key, extract_macros
from macromellow.web import DataApp


EGG = {
    "fdcId": 12345, "description": "Eggs, Grade A, Large, egg whole", "dataType": "Foundation",
    "foodNutrients": [
        {"nutrientId": 1008, "value": 143.0},
        {"nutrientId": 1003, "value": 12.6},
        {"nutrientId": 1005, "value": 0.7},
        {"nutrientId": 1004, "value": 9.5},
    ],
}


class Model:
    model = "test"
    def __init__(self, actions):
        self.actions = iter(actions)

    def interpret(self, *args, **kwargs):
        return next(self.actions)


class FakeUSDA:
    def __init__(self):
        self.calls = 0

    def lookup(self, name):
        self.calls += 1
        return {"fdc_id": 12345, "description": EGG["description"],
                "data_type": "Foundation", "per_100g": extract_macros(EGG)}


class USDATests(unittest.TestCase):
    def test_alias_and_nutrients(self):
        self.assertEqual(common_key("large eggs"), "egg")
        self.assertIsNone(common_key("chocolate eggs"))
        self.assertEqual(extract_macros(EGG)["protein_g"], 12.6)
        self.assertIsNone(extract_macros({"foodNutrients": [{"nutrientId": 1003, "value": 2}]}))

    def test_two_eggs_lookup_and_explicit_log(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Model([
                {"operation": "lookup", "foods": [{"name": "eggs", "quantity": 2, "unit": "each"}]},
                {"operation": "log", "foods": []},
            ])
            app = DataApp(Path(directory), model)
            app.usda = FakeUSDA()
            result = app.chat("how much protein in 2 eggs", None)
            self.assertEqual(result["kind"], "result")
            self.assertEqual(result["total"]["protein_g"], 12.7)
            self.assertEqual(result["total"]["calories"], 143.9)
            self.assertEqual(result["cards"][0]["grams"], 100.6)
            self.assertIn("USDA", result["cards"][0]["source"])
            self.assertEqual(app.memory.tracker()["totals"]["protein_g"], 0)
            logged = app.chat("add that to today's macros", result["conversation_id"])
            self.assertEqual(logged["kind"], "meal_logged")
            self.assertEqual(app.memory.tracker()["totals"]["protein_g"], 12.7)
            self.assertEqual(app.usda.calls, 1)

    def test_unknown_or_unsupported_units_do_not_guess(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Model([
                {"operation": "lookup", "foods": [{"name": "chicken breast", "quantity": 1, "unit": "each"}]},
                {"operation": "lookup", "foods": [{"name": "chocolate eggs", "quantity": 2, "unit": "each"}]},
            ])
            app = DataApp(Path(directory), model)
            app.usda = FakeUSDA()
            result = app.chat("How much protein in one chicken breast?", None)
            self.assertEqual(result["kind"], "clarification")
            self.assertIn("grams or ounces", result["message"])
            self.assertEqual(app.usda.calls, 0)
            second = app.chat("How much protein in chocolate eggs?", None)
            self.assertNotEqual(second.get("source"), "USDA FoodData Central")
            self.assertEqual(app.usda.calls, 0)


if __name__ == "__main__":
    unittest.main()
