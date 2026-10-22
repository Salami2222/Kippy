import json
import unittest
from unittest.mock import patch
from macromellow.ollama import OllamaClient, fallback_single_food, parse_model_plan
from macromellow.agent import tokens, choose_food

class RecoveryTests(unittest.TestCase):
    def test_compact_plan(self):
        plan = parse_model_plan({"message": {"content": json.dumps({"action": "set", "operation": "lookup", "foods": [{"name": "banana", "quantity": 2, "unit": "each"}]})}})
        self.assertEqual(plan["foods"][0]["hall"], "")
        self.assertIsNone(plan["adjustment_values"]["protein_g"])

    def test_simple_fallback(self):
        plan = fallback_single_food("I had 2 bananas, can you update my macros")
        self.assertEqual(plan["operation"], "log")
        self.assertEqual(plan["foods"][0]["quantity"], 2)
        pizza = fallback_single_food("I had 2 slices of mac n cheese pizza, update my macros")
        self.assertEqual(pizza["foods"][0]["name"], "mac n cheese pizza")
        self.assertEqual(pizza["foods"][0]["unit"], "slice")
        self.assertIsNone(fallback_single_food("I had chicken and rice"))

    def test_model_retry(self):
        client = OllamaClient()
        with patch.object(client, "status", return_value={"model_available": True}):
            with patch.object(client, "_request", side_effect=[{"message": {"content": ""}}, {"message": {"content": json.dumps({"action": "set", "operation": "lookup", "foods": [{"name": "banana", "quantity": 2, "unit": "each"}]})}}]) as request:
                result = client.interpret("I ate 2 bananas", [], "2026-10-21", ["2026-10-21"])
        self.assertEqual(result["foods"][0]["quantity"], 2)
        self.assertEqual(request.call_count, 2)
        self.assertFalse(request.call_args_list[0].args[1]["think"])

    def test_aliases(self):
        self.assertEqual(tokens("mac n cheese pizza"), tokens("macaroni and cheese pizza"))
        self.assertEqual(tokens("bananna"), tokens("banana"))
        records = [{"name": "Macaroni and Cheese Pizza", "dining_hall": "The Y", "source_url": "https://nutrition.umd.edu/label.aspx?x=1"}]
        chosen, _ = choose_food("mac n cheese pizza", records)
        self.assertEqual(chosen["name"], "Macaroni and Cheese Pizza")

if __name__ == "__main__":
    unittest.main()
