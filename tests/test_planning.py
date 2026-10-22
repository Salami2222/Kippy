import json
import socket
import unittest
from unittest.mock import patch
from urllib.error import URLError

from macromellow.ollama import OllamaClient, OllamaError, INTENT_SCHEMA, normalize_plan

EXAMPLE = 'I had a macaroni pizza slice, 2 scoops of cilantro lime rice, 1 chocolate chip cookie and a scoop of mac n cheese'
FOUR = [{"name": "macaroni pizza", "quantity": 1, "unit": "slice"},
        {"name": "cilantro lime rice", "quantity": 2, "unit": "scoop"},
        {"name": "chocolate chip cookie", "quantity": 1, "unit": "each"},
        {"name": "mac n cheese", "quantity": 1, "unit": "scoop"}]


def wire(value):
    return {"message": {"content": json.dumps(value)}}


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.client = OllamaClient('http://localhost:11434', 'qwen3.5:9b')

    def interpret(self, response):
        with patch.object(self.client, 'status', return_value={"model_available": True}), patch.object(self.client, '_request', side_effect=response) as request:
            result = self.client.interpret(EXAMPLE, [], '2026-10-19', ['2026-10-19'])
        return result, request

    def test_four_food_contract_with_no_irrelevant_fields(self):
        result, request = self.interpret([wire({"operation": "lookup", "foods": FOUR})])
        self.assertEqual(result['foods'], FOUR)
        self.assertEqual(result['action'], 'set')
        self.assertEqual(result['memory_key'], '')
        self.assertEqual(INTENT_SCHEMA['required'], ['operation'])
        self.assertEqual(result['_diagnostics']['attempts'], 1)
        self.assertEqual(request.call_count, 1)
        self.assertFalse(request.call_args.args[1]['think'])

    def test_partial_relevant_plan_defaults_are_safe(self):
        for raw in [{"operation": "today"}, {"operation": "lookup", "foods": [{"name": "cookie"}]},
                    {"operation": "lookup", "updates": [{"item_index": 1, "quantity": 3}]}]:
            with self.subTest(raw=raw):
                plan = normalize_plan(raw)
                self.assertEqual(plan['question'], '')
                self.assertEqual(plan['action'], 'update' if plan['updates'] else 'set')

    def test_fenced_json_accepted_without_repair(self):
        result, request = self.interpret([{"message": {"content": '```json\n{"operation":"today"}\n```'}}])
        self.assertEqual(result['operation'], 'today')
        self.assertEqual(request.call_count, 1)

    def test_one_repair_for_malformed_missing_empty_and_wrong_output(self):
        invalid = [{"message": {"content": '{bad'}}, {}, {"message": {}}, {"message": {"content": ''}},
                   wire({}), wire([]), wire({"operation": "lookup", "foods": [{"name": "rice", "quantity": "two"}]}),
                   wire({"operation": "lookup", "foods": None}), wire({"operation": "go_fishing"}),
                   {"done_reason":"length","message":{"content":"{\"operation\":\"today\"}"}},
                   {"message":{"content":"", "thinking":"private internal trace"}},
                   wire({"operation": "lookup", "nutrition": 300}), wire({"operation": "lookup", "foods": [{"name": "rice", "hall": "moon"}]})]
        for bad in invalid:
            with self.subTest(bad=bad):
                result, request = self.interpret([bad, wire({"operation": "lookup", "foods": FOUR})])
                self.assertEqual(request.call_count, 2)
                self.assertTrue(result['_diagnostics']['repaired'])
                self.assertEqual(result['foods'], FOUR)
                self.assertTrue(result['_diagnostics']['failures'][0]['category'])

    def test_second_invalid_output_reports_sanitized_category(self):
        with patch.object(self.client, 'status', return_value={"model_available": True}), patch.object(self.client, '_request', return_value={"message": {"content": 'private food text not JSON'}}) as request:
            with self.assertRaises(OllamaError) as raised:
                self.client.interpret(EXAMPLE, [], '2026-10-19', [])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(raised.exception.category, 'invalid_plan')
        self.assertEqual(raised.exception.details['failures'][0]['category'], 'malformed_json')
        self.assertNotIn('private food', str(raised.exception.details))
        self.assertNotIn(EXAMPLE, str(raised.exception.details))

    def test_no_repair_for_model_failure_connection_or_timeout(self):
        for exc in [OllamaError('offline', 'connection'), OllamaError('slow', 'timeout')]:
            with self.subTest(category=exc.category), patch.object(self.client, 'status', return_value={"model_available": True}), patch.object(self.client, '_request', side_effect=exc) as request:
                with self.assertRaises(OllamaError) as raised:
                    self.client.interpret(EXAMPLE, [], '2026-10-19', [])
                self.assertEqual(raised.exception.category, exc.category)
                self.assertEqual(request.call_count, 1)
        with self.assertRaises(OllamaError) as raised:
            self.interpret([{'error': 'private runtime detail'}])
        self.assertEqual(raised.exception.category, 'model_failure')

    def test_transport_offline_and_timeout_categories(self):
        for error, category in [(URLError('offline'), 'connection'), (URLError(socket.timeout()), 'timeout')]:
            with self.subTest(category=category), patch('macromellow.ollama.urlopen', side_effect=error):
                with self.assertRaises(OllamaError) as raised:
                    self.client._request('/api/chat', {})
                self.assertEqual(raised.exception.category, category)

    def test_umd_hall_alias_and_empty_optional_patch_fields(self):
        plan = normalize_plan({"operation":"lookup","updates":[{"item_index":0,"hall":"Yahentamitsi (The Y)","name":"","unit":""}]})
        self.assertEqual(plan['updates'], [{'item_index':0,'hall':'yahentamitsi'}])
        drop = normalize_plan({"operation":"lookup","action":"drop","updates":[{"item_index":2,"name":"","quantity":0}]})
        self.assertEqual(drop['updates'], [{'item_index':2}])

    def test_strict_boundary_rejects_wrong_values(self):
        bad = [{"operation": []}, {"operation":"adjust","adjustment_mode":[]}, {"operation": "lookup", "selection": True}, {"operation": "lookup", "foods": [{"name": "rice", "quantity": float('nan')}]},
               {"operation": "lookup", "updates": [{"item_index": -1}]}, {"operation": "lookup", "foods": [{"name": "rice", "source_url": "https://fake.test"}]}]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_plan(raw)


if __name__ == '__main__':
    unittest.main()
