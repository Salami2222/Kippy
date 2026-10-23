"""Milestones are atomic with real ledger writes; reads/restoration never emit them."""
import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from macromellow.memory import MemoryStore
from macromellow.web import AppHandler


class ProteinCelebrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.instant = datetime(2026, 10, 22, 3, 59, tzinfo=timezone.utc)
        self.store = MemoryStore(Path(self.temp.name) / 'memory.sqlite3', now=lambda: self.instant)

    def update(self, mode, values):
        with self.store.capture_celebrations() as events:
            self.store.adjust(mode, values)
        return events

    def post(self, route, payload):
        handler = object.__new__(AppHandler)
        handler.app = SimpleNamespace(memory=self.store)
        handler.path = route
        body = json.dumps(payload).encode()
        handler.headers = {'Host': '127.0.0.1:8000', 'Origin': 'http://127.0.0.1:8000',
                           'Content-Type': 'application/json', 'Content-Length': str(len(body)),
                           'X-MacroMellow-Request': 'state'}
        handler.client_address = ('127.0.0.1', 1234)
        handler.rfile = io.BytesIO(body)
        handler._send = Mock()
        handler.do_POST()
        status, data, _ = handler._send.call_args.args
        return status, json.loads(data)

    def test_exact_crossing_persists_after_restart_and_does_not_repeat(self):
        self.store.set_goals({'protein_g': 100})
        self.assertEqual(self.update('set', {'protein_g': 99}), [])
        self.assertEqual(self.update('add', {'protein_g': 1}), [{'kind': 'protein_goal', 'date': '2026-10-21'}])
        self.assertNotIn('celebration', self.store.tracker())
        self.store = MemoryStore(self.store.path, now=lambda: self.instant)
        self.update('subtract', {'protein_g': 50})
        self.assertEqual(self.update('add', {'protein_g': 60}), [])
        self.store.reset()
        self.assertEqual(self.update('set', {'protein_g': 120}), [])

    def test_new_york_midnight_renews_eligibility_with_same_goals(self):
        self.store.set_goals({'protein_g': 10})
        self.assertEqual(len(self.update('set', {'protein_g': 11})), 1)
        self.instant = datetime(2026, 10, 22, 4, 0, tzinfo=timezone.utc)
        self.assertEqual(self.store.tracker()['totals']['protein_g'], 0)
        self.assertEqual(self.update('set', {'protein_g': 10})[0]['date'], '2026-10-22')
        self.assertEqual(self.store.tracker('2026-10-21')['totals']['protein_g'], 11)

    def test_only_valid_protein_goal_and_total_crossing_count(self):
        for goal in (None, 0):
            self.store.set_goals({'protein_g': goal, 'calories': 1, 'fat_g': 1, 'carbohydrates_g': 1})
            self.assertEqual(self.update('add', {'protein_g': 20, 'calories': 2, 'fat_g': 2, 'carbohydrates_g': 2}), [])
        self.store.set_goals({'protein_g': 100})
        self.assertEqual(self.update('set', {'protein_g': 90}), [])
        with self.store.capture_celebrations() as events:
            self.store.set_goals({'protein_g': 80})
            self.store.tracker()
        self.assertEqual(events, [])
        self.assertEqual(self.update('add', {'calories': 5}), [])
        self.assertEqual(self.update('add', {'protein_g': 1}), [])
        self.store.set_goals({'protein_g': 120})
        self.assertEqual(len(self.update('add', {'protein_g': 30})), 1)

    def test_import_history_and_restore_do_not_emit_or_claim(self):
        self.store.set_goals({'protein_g': 10})
        with self.store.capture_celebrations() as events:
            self.store.import_legacy('old', {'date': self.store.today(), 'totals': {
                'protein_g': 12, 'calories': 0, 'fat_g': 0, 'carbohydrates_g': 0}}, {'protein_g': 10})
            self.store.adjust('add', {'protein_g': 20}, '2026-10-20')
            self.store.tracker()
        self.assertEqual(events, [])
        self.update('set', {'protein_g': 9})
        self.assertEqual(len(self.update('add', {'protein_g': 1})), 1)

    def test_concurrent_writes_claim_only_one_event(self):
        self.store.set_goals({'protein_g': 10})
        with ThreadPoolExecutor(max_workers=2) as pool:
            events = list(pool.map(lambda _: self.update('add', {'protein_g': 10}), range(2)))
        self.assertEqual(sum(map(len, events)), 1)
        self.assertEqual(self.store.tracker()['totals']['protein_g'], 20)

    def test_http_only_fresh_success_response_carries_event(self):
        self.post('/api/tracker/goals', {'goals': {'protein_g': 10}})
        status, response = self.post('/api/tracker/adjust', {'mode': 'add', 'values': {'protein_g': 10}})
        self.assertEqual(status, 200)
        self.assertEqual(response['celebration']['kind'], 'protein_goal')
        self.assertNotIn('celebration', self.store.tracker())
        self.assertNotIn('celebration', self.post('/api/tracker/adjust', {'mode': 'add', 'values': {'protein_g': 5}})[1])
        self.assertNotIn('celebration', self.post('/api/tracker/goals', {'goals': {'protein_g': 12}})[1])
        self.assertEqual(self.post('/api/tracker/adjust', {'mode': 'add', 'values': {'protein_g': 'bad'}})[0], 400)

    def test_http_does_not_mutate_cached_chat_payload(self):
        handler = object.__new__(AppHandler)
        handler._send = Mock()
        handler.celebration_events = [{'kind': 'protein_goal', 'date': self.store.today()}]
        payload = {'tracker': self.store.tracker(), 'message': 'Logged'}
        handler._json(200, payload)
        self.assertNotIn('celebration', payload['tracker'])
        self.assertIn('celebration', json.loads(handler._send.call_args.args[1])['tracker'])

    def test_confirmed_meal_logging_replacement_duplicates_and_restoration(self):
        self.store.set_goals({'protein_g': 10})
        def result(protein):
            return self.store.save_result('c' * 32, {'kind': 'result', 'fingerprint': str(protein),
                'total': {'calories': 1, 'protein_g': protein, 'carbohydrates_g': 0, 'fat_g': 0},
                'cards': [{'menu_date': self.store.today(), 'portion_confirmed': True}]})
        first = result(9)
        with self.store.capture_celebrations() as events:
            entry, added = self.store.log_result(first, self.store.today(), 'c' * 32)
        self.assertTrue(added)
        self.assertEqual(events, [])
        replacement = result(10)
        with self.store.capture_celebrations() as events:
            updated = self.store.replace_meal(entry['id'], replacement)
        self.assertEqual(len(events), 1)
        with self.store.capture_celebrations() as events:
            self.assertFalse(self.store.log_result(replacement, self.store.today(), 'c' * 32)[1])
            self.store.remove_meal(updated['id'])
            self.store.restore_meal(updated['id'])
        self.assertEqual(events, [])
        # A new day and a fresh meal reaching the goal also emits the milestone.
        self.instant = datetime(2026, 10, 22, 4, 0, tzinfo=timezone.utc)
        with self.store.capture_celebrations() as events:
            self.store.log_result(result(11), self.store.today(), 'c' * 32)
        self.assertEqual(len(events), 1)
