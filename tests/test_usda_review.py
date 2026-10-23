"""PR review regressions: source selection, failures, cache, and existing meal safety."""
import copy
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from http.client import IncompleteRead
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from macromellow.usda import USDAClient, USDAError, CACHE_SECONDS, extract_macros
from macromellow.web import DataApp
from macromellow.umd import save_json
from test_agent import DAY, food, item
from test_usda import EGG, FakeUSDA, Model


def plan(operation='lookup', foods=None, **kwargs):
    return {'operation': operation, 'foods': foods or [], **kwargs}


def eggs(quantity=2, unit='each', **kwargs):
    return {'name': 'eggs', 'quantity': quantity, 'unit': unit, **kwargs}


class USDAClientReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = USDAClient(Path(self.temp.name)/'cache.db', api_key='private-test-key')

    def lookup(self, payload):
        with patch('macromellow.usda.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
            return self.client.lookup('eggs')

    def test_units_missing_values_duplicates_and_nonfinite_nutrients_fail_closed(self):
        for field, value in [('unitName', 'kJ'), ('value', -1), ('value', float('nan')), ('value', True)]:
            bad = copy.deepcopy(EGG); bad['foodNutrients'][0][field] = value
            self.assertIsNone(extract_macros(bad))
        for nutrients in (None, {}, [None], EGG['foodNutrients'][:-1]):
            self.assertIsNone(extract_macros({**EGG, 'foodNutrients': nutrients}))
        conflict = copy.deepcopy(EGG)
        conflict['foodNutrients'].append({**EGG['foodNutrients'][1], 'value': 40})
        self.assertIsNone(extract_macros(conflict))
        fallback = copy.deepcopy(EGG); fallback['foodNutrients'][0]['nutrientId'] = 2047
        self.assertEqual(extract_macros(fallback)['calories'], 143)
        nested = {'foodNutrients': [{'nutrient': {'id': n['nutrientId'], 'unitName': n['unitName']}, 'amount': n['value']} for n in EGG['foodNutrients']]}
        self.assertEqual(extract_macros(nested), extract_macros(EGG))

    def test_wrong_source_incomplete_or_ambiguous_records_are_not_selected(self):
        for foods in ([{**EGG, 'dataType': 'Branded'}], [{**EGG, 'fdcId': -1}],
                      [{**EGG, 'foodNutrients': []}], [EGG, {**EGG, 'fdcId': 99999}]):
            with self.subTest(foods=foods), self.assertRaises(USDAError):
                self.lookup({'foods': foods})
        value = self.lookup({'foods': [None, 'bad', {**EGG, 'dataType': 'SR Legacy', 'fdcId': 222}, EGG]})
        self.assertEqual(value['fdc_id'], EGG['fdcId'])

    def test_http_failures_are_safe_and_never_cached_or_leak_key(self):
        for error in [HTTPError('https://example.invalid/?api_key=private-test-key', code, 'secret', {}, None) for code in (401,403,429,500)] + [URLError('private-test-key'), TimeoutError('private-test-key'), IncompleteRead(b'private-test-key')]:
            with patch('macromellow.usda.urlopen', side_effect=error), self.assertRaises(USDAError) as caught:
                self.client.lookup('eggs')
            self.assertNotIn('private-test-key', str(caught.exception))
        with closing(sqlite3.connect(self.client.db_path)) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM usda_food_cache').fetchone()[0], 0)

    def test_malformed_and_oversized_responses_fail_safely(self):
        for raw in (b'not JSON', b'[]', b'{"foods":null}', b'{"foods":{}}', b' ' * 8_388_609):
            with patch('macromellow.usda.urlopen', return_value=io.BytesIO(raw)), self.assertRaises(USDAError):
                self.client.lookup('eggs')

    def test_cache_is_persistent_expires_and_revalidates_corrupt_or_future_entries(self):
        with patch('macromellow.usda.time.time', return_value=1000):
            original = self.lookup({'foods': [EGG]})
        restarted = USDAClient(self.client.db_path, api_key='')
        with patch('macromellow.usda.time.time', return_value=1001), patch('macromellow.usda.urlopen', side_effect=AssertionError('cached')):
            self.assertEqual(restarted.lookup('eggs'), original)
        with patch('macromellow.usda.time.time', return_value=1000 + CACHE_SECONDS):
            with self.assertRaises(USDAError): restarted.lookup('eggs')
        for stamp, value in [(1000, json.dumps({k:v for k,v in original.items() if k != 'validation_version'})), (1000, '{bad'), (1000, '{}'), (999999999999, json.dumps(original)),
                             (1000, json.dumps({**original, 'description': 'Egg substitute'})),
                             (1000, json.dumps({**original, 'per_100g': {'protein_g': 999}}))]:
            with closing(sqlite3.connect(self.client.db_path)) as con, con:
                con.execute('UPDATE usda_food_cache SET fetched_at=?,value_json=?', (stamp,value))
            with patch('macromellow.usda.time.time', return_value=1001), self.assertRaises(USDAError):
                restarted.lookup('eggs')


class USDAConversationReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.day = datetime(2026,10,19,16,tzinfo=timezone.utc)

    def app(self, actions):
        app = DataApp(self.path, Model(actions))
        app.memory.now = lambda: self.day
        real = app.datasets
        app.datasets = lambda: {**real(), 'today': DAY}
        app.usda = FakeUSDA()
        return app

    def test_lookup_explicit_log_duplicate_correction_undo_and_restart(self):
        app = self.app([plan(foods=[eggs()]), plan('log', [eggs()]), plan('log', [eggs()]),
                        plan('log', updates=[{'item_index':0,'quantity':3,'unit':'each'}]), plan('undo'),
                        plan('lookup')])
        first = app.conversations.turn('How much protein is in 2 eggs?', None)
        cid = first['conversation_id']
        self.assertEqual(app.memory.tracker()['totals']['protein_g'], 0)
        second = app.conversations.turn('Add 2 eggs to my macros.',cid)
        self.assertEqual(second['kind'], 'meal_logged')
        self.assertEqual(app.conversations.turn('Add 2 eggs to my macros.',cid)['kind'], 'already_logged')
        correction = app.conversations.turn('Actually make that 3 eggs.',cid)
        self.assertEqual(correction['kind'],'meal_edited')
        self.assertEqual(correction['tracker']['totals']['protein_g'],19)
        undone = app.conversations.turn('Undo that correction',cid)
        self.assertEqual(undone['tracker']['totals']['protein_g'],12.7)
        after = app.conversations.turn('How much protein is in this?',cid)
        self.assertEqual(after['total']['protein_g'],12.7)
        restarted = DataApp(self.path, Model([])); restarted.memory.now=lambda:self.day
        self.assertEqual(restarted.memory.tracker()['totals']['protein_g'],12.7)
        self.assertEqual(restarted.memory.conversation(cid)['foods'][0]['quantity'],2)

    def test_measured_portions_and_followup_weight_do_not_need_umd_menus(self):
        for quantity,unit,grams in [(100,'g',100),(2,'oz',56.69904625)]:
            app = self.app([plan(foods=[eggs(quantity,unit)])])
            result=app.chat(f'Protein in {quantity} {unit} eggs?',None)
            self.assertEqual(result['total']['protein_g'],round(grams / 100 * 12.6,1))
        app=self.app([plan('log',[{'name':'chicken breast','quantity':1,'unit':'each'}])])
        first=app.chat('Add a chicken breast',None)
        self.assertEqual(first['kind'],'clarification')
        # The local pending-portion parser handles this without another model call.
        later=app.chat('150 g',first['conversation_id'])
        self.assertEqual(later['kind'],'meal_logged')
        self.assertEqual(later['cards'][0]['grams'],150)

    def test_counted_servings_preparation_and_past_dates_require_clarification(self):
        cases=[('Add 2 servings of eggs',eggs(2,'serving'),{}),
               ('Add 2 scrambled eggs',eggs(),{}),
               ('Add 2 scrambled eggs',{'name':'scrambled eggs','quantity':2,'unit':'each'},{}),('Add 2 egg whites',eggs(),{}),
               ('Add 2 eggs yesterday',eggs(),{}),('Add 2 eggs on 2026-10-18',eggs(),{}),
               ('Add 2 eggs',eggs(),{'log_date':'2026-10-18'})]
        for message,food_item,metadata in cases:
            with self.subTest(message=message):
                app=self.app([plan('log',[food_item],**metadata)])
                result=app.chat(message,None)
                self.assertEqual(result['kind'],'clarification')
                self.assertEqual(app.usda.calls,0)
                self.assertEqual(app.memory.meals(),[])

    def test_failed_new_lookup_cannot_log_previous_food(self):
        app=self.app([plan(foods=[eggs()]),plan(foods=[{'name':'banana','quantity':1,'unit':'each'}]),plan('log')])
        first=app.chat('Protein in 2 eggs?',None)
        def fail(name): raise USDAError('USDA offline')
        app.usda.lookup=fail
        failed=app.chat('Protein in a banana?',first['conversation_id'])
        self.assertIsNone(app.memory.conversation(first['conversation_id'])['last_result_id'])
        self.assertEqual(failed['kind'],'clarification')
        self.assertEqual(app.chat('Add that to my macros',first['conversation_id'])['kind'],'clarification')
        self.assertEqual(app.memory.meals(),[])

    def test_umd_source_and_multifood_paths_are_preserved(self):
        fixture={'schema_version':1,'source':'UMD Dining Services','menu_date':DAY,
                 'retrieved_at':'2026-10-19T10:00:00+00:00','foods':[food('Grilled Chicken Breast')],'errors':[]}
        save_json(fixture,self.path/'umd.json')
        app=self.app([plan(foods=[item('grilled chicken breast',hall='yahentamitsi')])])
        result=app.chat('Protein in grilled chicken breast at The Y?',None)
        self.assertEqual(app.usda.calls,0)
        self.assertEqual(result['kind'],'result')
        self.assertIn('nutrition.umd.edu', result['cards'][0]['source_url'])
        self.assertNotEqual(result.get('source'),'USDA FoodData Central')
        app=self.app([plan('log',[eggs(),{'name':'banana','quantity':1,'unit':'each'}])])
        mixed=app.chat('Add 2 eggs and a banana',None)
        self.assertEqual(mixed['kind'],'clarification')
        self.assertEqual(app.usda.calls,0)
        self.assertEqual(app.memory.meals(),[])

    def test_approval_and_protein_celebration_use_existing_guards_once(self):
        app=self.app([plan(foods=[eggs()])])
        app.memory.set_goals({'protein_g':10})
        lookup=app.conversations.turn('Protein in 2 eggs?',None)
        cid=lookup['conversation_id'];mid=lookup['message_id']
        with self.assertRaises(ValueError): app.conversations.react(cid,mid,True)
        self.assertEqual(app.memory.meals(),[])
        with app.memory.capture_celebrations() as events:
            approved=app.conversations.react(cid,mid,True,True)
        self.assertEqual(len(events),1)
        self.assertEqual(app.memory.tracker()['totals']['protein_g'],12.7)
        with app.memory.capture_celebrations() as events:
            app.conversations.react(cid,mid,True,True)
        self.assertEqual(events,[])
        self.assertEqual(len(app.memory.meals()),1)

    def test_plain_today_query_reads_ledger_without_new_offer_or_model_interpretation(self):
        app=self.app([])
        app.memory.adjust('add',{'protein_g':12.7})
        result=app.conversations.turn('What are my macros today?',None)
        self.assertEqual(result['kind'],'tracker')
        self.assertEqual(result['total']['protein_g'],12.7)
        self.assertNotIn('approval',result)

    def test_unresolved_preparation_cannot_turn_a_weight_reply_into_logging(self):
        app=self.app([plan('log',[{'name':'chicken breast','quantity':150,'unit':'g'}]),
                      plan('log',updates=[{'item_index':0,'quantity':150,'unit':'g'}])])
        first=app.chat('Add 150 g raw chicken breast',None)
        self.assertEqual(first['kind'],'clarification')
        reply=app.chat('150 g',first['conversation_id'])
        self.assertEqual(reply['kind'],'clarification')
        self.assertEqual(app.memory.meals(),[])
        self.assertEqual(app.usda.calls,0)

    def test_usda_duplicate_guard_survives_aliases_units_labels_and_legacy_fingerprints(self):
        app=self.app([plan('log',[eggs()],label='Breakfast'),
                      plan('log',[{'name':'large eggs','quantity':2,'unit':'each'}],label='Lunch'),
                      plan('log',[eggs(100.6,'g')],label='Snack')])
        first=app.chat('Add 2 eggs for breakfast',None)
        # A pre-review record may have the old USDA fingerprint representation.
        with app.memory.db() as con:
            row=con.execute("SELECT id,payload_json FROM entries WHERE kind='meal'").fetchone()
            payload=json.loads(row['payload_json']);payload['fingerprint']='legacy-usda-fingerprint'
            con.execute('UPDATE entries SET payload_json=? WHERE id=?',(json.dumps(payload),row['id']))
        self.assertEqual(app.chat('Add 2 large eggs for lunch',first['conversation_id'])['kind'],'already_logged')
        self.assertEqual(app.chat('Add 100.6 g eggs for snack',first['conversation_id'])['kind'],'already_logged')
        self.assertEqual(len(app.memory.meals()),1)
        self.assertEqual(app.memory.tracker()['totals']['protein_g'],12.7)

    def test_umd_followup_with_unset_hall_never_switches_to_usda(self):
        fixture={'schema_version':1,'source':'UMD Dining Services','menu_date':DAY,
                 'retrieved_at':'2026-10-19T10:00:00+00:00','foods':[food('Chicken Breast')],'errors':[]}
        save_json(fixture,self.path/'umd.json')
        app=self.app([plan(foods=[{'name':'chicken breast','quantity':1,'unit':'each'}]),
                      plan(updates=[{'item_index':0,'quantity':2}])])
        first=app.chat('Protein in chicken breast at The Y?',None)
        self.assertEqual(first['kind'],'result')
        # Older persisted conversations have no explicit source-context field.
        session=app.memory.conversation(first['conversation_id']);session.pop('nutrition_source',None)
        app.memory.save_conversation(first['conversation_id'],session)
        later=app.chat('Actually make that 2',first['conversation_id'])
        self.assertEqual(later['kind'],'result')
        self.assertEqual(later['total']['protein_g'],64)
        self.assertEqual(app.usda.calls,0)

    def test_explicit_mixed_umd_and_home_food_cannot_log_only_one_source(self):
        app=self.app([plan('log',[item('chicken breast',hall='yahentamitsi'),
                                  {'name':'banana','quantity':1,'unit':'each'}])])
        result=app.chat('Add chicken breast from The Y and a banana from home',None)
        self.assertEqual(result['kind'],'clarification')
        self.assertEqual(app.memory.meals(),[])
        self.assertEqual(app.usda.calls,0)

    def test_legacy_umd_portion_question_remains_umd_after_restart(self):
        fixture={'schema_version':1,'source':'UMD Dining Services','menu_date':DAY,
                 'retrieved_at':'2026-10-19T10:00:00+00:00','foods':[food('Chicken Breast',serving='4 oz')],'errors':[]}
        save_json(fixture,self.path/'umd.json')
        app=self.app([plan(foods=[{'name':'chicken breast','quantity':2,'unit':'scoop'}])])
        first=app.chat('Protein in two scoops of chicken breast at The Y?',None)
        self.assertEqual(first['kind'],'clarification')
        session=app.memory.conversation(first['conversation_id']);session.pop('nutrition_source',None)
        app.memory.save_conversation(first['conversation_id'],session)
        later=app.chat('one scoop is one serving',first['conversation_id'])
        self.assertEqual(later['kind'],'result')
        self.assertEqual(later['total']['protein_g'],64)
        self.assertEqual(app.usda.calls,0)
