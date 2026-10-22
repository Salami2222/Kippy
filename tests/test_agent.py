import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from macromellow.agent import choose_food, normalize_intent, resolve
from macromellow.ollama import OllamaClient, OllamaError
from macromellow.umd import save_json
from macromellow.web import ChatError, DataApp


DAY = "2026-10-19"
HALL = "Yahentamitsi (The Y)"


def food(name, hall=HALL, calories=180, protein=32, carbs=0, fat=5, serving="1 each"):
    return {"name": name, "dining_hall": hall, "menu_date": DAY, "serving_size": serving,
            "calories": calories, "protein_g": protein, "carbohydrates_g": carbs, "fat_g": fat,
            "source_url": "https://nutrition.umd.edu/label.aspx?RecNumAndPort=123%2A1"}


def item(name, quantity=1, unit="serving", hall="yahentamitsi", day=DAY):
    return {"name": name, "quantity": quantity, "unit": unit, "hall": hall, "date": day}


class StubOllama:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def interpret(self, message, previous, today, dates, last_question="", recent=None, memories=None, recent_meals=None):
        self.calls.append((message, previous, last_question))
        return next(self.outputs)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.dataset = {"schema_version": 1, "source": "UMD Dining Services", "menu_date": DAY,
                        "retrieved_at": "2026-10-19T19:00:00-04:00", "errors": [],
                        "foods": [food("Grilled Chicken Breast"), food("Brown Rice", calories=110, protein=2, carbs=23, fat=1)]}

    def test_food_quantity_and_hall_are_validated(self):
        normalized = normalize_intent({"action": "set", "foods": [item("chicken breasts", 5, "each")], "question": ""}, [])
        self.assertEqual(normalized[0]["quantity"], 5)
        self.assertEqual(normalized[0]["hall"], "yahentamitsi")
        with self.assertRaises(ValueError):
            normalize_intent({"action": "set", "foods": [item("chicken", -4)], "question": ""}, [])

    def test_clear_match_and_multiple_servings(self):
        result = resolve([item("grilled chicken breast", 5, "each")], [self.dataset], DAY)
        self.assertEqual(result["kind"], "result")
        self.assertEqual(result["total"], {"calories": 900, "protein_g": 160, "carbohydrates_g": 0, "fat_g": 25})
        self.assertEqual(result["cards"][0]["source_url"], self.dataset["foods"][0]["source_url"])
        self.assertEqual(result["cards"][0]["conversion"], "Reported units match the listed UMD unit.")
        match = result["debug"]["matches"][0]
        self.assertEqual(match["selected"]["source_url"], self.dataset["foods"][0]["source_url"])
        self.assertEqual(match["selected"]["name_score"], 1)
        calculation = result["debug"]["calculations"][0]
        self.assertEqual(calculation["per_serving"]["calories"], 180)
        self.assertEqual(calculation["quantity"], 5)
        self.assertEqual(calculation["result"]["calories"], 900)

    def test_multiple_foods_sum_in_python(self):
        result = resolve([item("grilled chicken breast", 2), item("brown rice", 1.5)], [self.dataset], DAY)
        self.assertEqual(len(result["cards"]), 2)
        self.assertEqual(result["total"]["calories"], 525)
        self.assertEqual(result["total"]["protein_g"], 67)

    def test_ambiguous_food_asks_for_specific_menu_name(self):
        self.dataset["foods"].extend([food("Roasted Chicken"), food("Chicken Fajita Bake")])
        result = resolve([item("chicken")], [self.dataset], DAY)
        self.assertEqual(result["kind"], "clarification")
        self.assertGreaterEqual(len(result["options"]), 2)
        self.assertIsNone(result["total"])
        self.assertEqual(result["debug"]["matches"][0]["status"], "ambiguous or low name score")
        self.assertIsNone(result["debug"]["matches"][0]["selected"])
        self.assertTrue(all(candidate["source_url"] for candidate in result["debug"]["matches"][0]["candidates"]))

    def test_same_food_across_halls_needs_hall(self):
        self.dataset["foods"].append(food("Grilled Chicken Breast", hall="251 North", calories=210))
        result = resolve([item("grilled chicken breast", hall="")], [self.dataset], DAY)
        self.assertEqual(result["kind"], "clarification")
        self.assertEqual(len(result["options"]), 2)

    def test_no_false_match_for_missing_chicken_breast(self):
        self.dataset["foods"] = [food("Grilled Chipotle Lime Chicken", serving="4 oz")]
        result = resolve([item("chicken breasts", 5, "each")], [self.dataset], DAY)
        self.assertEqual(result["kind"], "clarification")
        self.assertEqual(result["cards"], [])

    def test_multiple_food_name_typos_require_confirmation(self):
        selected, alternatives = choose_food("griled chiken breast", self.dataset["foods"])
        self.assertIsNone(selected)
        self.assertTrue(any(food["name"] == "Grilled Chicken Breast" for food in alternatives))

    def test_near_exact_food_match_with_competing_variant_requires_confirmation(self):
        foods = [food("Grilled Chicken Breast"), food("Grilled Chicken Breast Strips", calories=210)]
        selected, alternatives = choose_food("grilled chiken breast", foods)
        self.assertIsNone(selected)
        self.assertEqual({food["name"] for food in alternatives},
                         {"Grilled Chicken Breast", "Grilled Chicken Breast Strips"})

    def test_missing_nutrition_stays_missing_in_card_and_total(self):
        self.dataset["foods"][0]["protein_g"] = None
        result = resolve([item("grilled chicken breast", 2)], [self.dataset], DAY)
        self.assertIsNone(result["cards"][0]["nutrition"]["protein_g"])
        self.assertIsNone(result["total"]["protein_g"])
        self.assertEqual(result["total"]["calories"], 360)
        self.assertIsNone(result["debug"]["calculations"][0]["per_serving"]["protein_g"])
        self.assertTrue(any("missing nutrition" in note for note in result["debug"]["warnings"]))

    def test_follow_up_correction_replaces_previous_quantity(self):
        ollama = StubOllama([
            {"action": "set", "foods": [item("grilled chicken breast", 5, "each")], "question": ""},
            {"action": "set", "foods": [item("grilled chicken breast", 4, "each")], "question": ""},
        ])
        with tempfile.TemporaryDirectory() as directory:
            save_json(self.dataset, Path(directory) / "menu.json")
            app = DataApp(Path(directory), ollama)
            first = app.chat("I ate 5 chicken breasts at The Y", None)
            second = app.chat("Actually make that 4", first["conversation_id"])
        self.assertEqual(first["total"]["calories"], 900)
        self.assertEqual(second["total"]["calories"], 720)
        self.assertEqual(ollama.calls[1][1][0]["quantity"], 5)
        self.assertEqual(second["debug"]["user_request"], "Actually make that 4")
        self.assertEqual(second["debug"]["model_interpretation"]["foods"][0]["quantity"], 4)
        self.assertIsInstance(second["debug"]["ai_response_time_ms"], int)
        self.assertIsInstance(second["debug"]["response_time_ms"], int)

    def test_ai_failure_has_debug_error_and_timing(self):
        class OfflineOllama:
            model = "qwen3.5:9b"

            def interpret(self, *args, **kwargs):
                raise OllamaError("Cannot reach Ollama")

        with tempfile.TemporaryDirectory() as directory:
            save_json(self.dataset, Path(directory) / "menu.json")
            with self.assertRaises(ChatError) as raised:
                DataApp(Path(directory), OfflineOllama()).chat("chicken", None)
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(raised.exception.debug["user_request"], "chicken")
        self.assertEqual(raised.exception.debug["error"]["stage"], "Ollama interpretation")
        self.assertIsInstance(raised.exception.debug["ai_response_time_ms"], int)

    def test_missing_saved_data_is_reported_after_interpreting_food_request(self):
        ollama = StubOllama([{"action": "set", "operation": "lookup", "foods": [item("chicken")]}])
        with tempfile.TemporaryDirectory() as directory:
            result = DataApp(Path(directory), ollama).chat("chicken", None)
        self.assertEqual(result["kind"], "needs_data")
        self.assertEqual(len(ollama.calls), 1)

    def test_offline_ollama_and_missing_model(self):
        client = OllamaClient("http://127.0.0.1:11434", "qwen3.5:9b")
        with patch("macromellow.ollama.urlopen", side_effect=URLError("offline")):
            self.assertFalse(client.status()["connected"])
            with self.assertRaises(OllamaError):
                client.interpret("chicken", [], DAY, [DAY])
        with patch.object(client, "_request", return_value={"models": [{"name": "other:latest"}]}):
            self.assertFalse(client.status()["model_available"])

    def test_structured_ollama_request_contains_schema(self):
        client = OllamaClient("http://127.0.0.1:11434", "qwen3.5:9b")
        with patch.object(client, "status", return_value={"model_available": True}):
            with patch.object(client, "_request", return_value={"message": {"content": json.dumps({"action": "set", "foods": [item("rice")], "question": ""})}}) as request:
                self.assertEqual(client.interpret("rice", [], DAY, [DAY])["foods"][0]["name"], "rice")
                payload = request.call_args.args[1]
                self.assertEqual(payload["model"], "qwen3.5:9b")
                self.assertEqual(payload["format"]["type"], "object")
                self.assertIn("operation", payload["format"]["required"])
                self.assertEqual(payload["format"]["required"], ["operation"])
                self.assertNotIn("log_date", payload["format"]["required"])  # optional fields default in Python
                self.assertEqual(json.loads(payload["messages"][1]["content"])["today"], DAY)
                self.assertFalse(payload["stream"])


if __name__ == "__main__":
    unittest.main()
