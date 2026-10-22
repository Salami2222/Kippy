import copy
import json
import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from macromellow.agent import choose_food, resolve
from macromellow.ollama import OllamaClient
from macromellow.web import DataApp, AppHandler
from test_agent import DAY, food, item
from test_planning import EXAMPLE, FOUR, wire

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/umd-2026-10-19-excerpt.json').read_text())


class NaturalMealTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.fixture = copy.deepcopy(FIXTURE)
        self.now = datetime(2026, 10, 19, 18, tzinfo=timezone.utc)

    def app(self, plans):
        client = OllamaClient('http://localhost:11434', 'qwen3.5:9b')
        client.status = lambda: {'model_available': True}
        self.responses = iter([wire(p) for p in plans])
        client._request = lambda *args, **kwargs: next(self.responses)
        app = DataApp(self.path, client)
        app.memory.now = lambda: self.now
        app.datasets = lambda: {'today': app.memory.today(), 'datasets': [self.fixture], 'load_errors': []}
        return app

    def test_exact_four_item_request_all_items_retained_and_no_scoop_guess(self):
        app = self.app([{'operation': 'lookup', 'foods': FOUR}])
        reply = app.conversations.turn(EXAMPLE, None)
        state = app.memory.conversation(reply['conversation_id'])
        self.assertEqual(len(state['foods']), 4)
        self.assertEqual([(f['quantity'], f['unit']) for f in state['foods']], [(1,'slice'),(2,'scoop'),(1,'each'),(1,'scoop')])
        self.assertEqual(reply['kind'], 'clarification')
        self.assertNotIn('could not interpret', reply['message'])
        self.assertIsNone(reply['total'])
        self.assertIn('macaroni pizza', reply['message'])
        self.assertTrue(any(c['name'] == 'Chocolate Chip Cookie' and c['nutrition']['calories'] == 188 for c in reply['cards']))
        rice = next(c for c in reply['cards'] if c['name'] == 'Cilantro Lime Rice')
        self.assertFalse(rice['portion_confirmed'])
        self.assertIsNone(rice['nutrition']['calories'])
        self.assertEqual(reply['confirmed_subtotal']['calories'], 188)
        self.assertEqual(len(reply['debug']['matches']), 4)
        self.assertEqual(app.memory.meals(), [])

    def test_four_food_followups_patch_hall_name_quantity_and_choices_without_loss(self):
        app = self.app([{'operation':'lookup','foods':FOUR},
                        {'operation':'lookup','updates':[{'item_index':0,'name':'cheese pizza'}]},
                        {'operation':'lookup','updates':[{'item_index':1,'quantity':3,'unit':'scoop'}]}])
        first = app.conversations.turn(EXAMPLE, None)
        cid = first['conversation_id']
        app.conversations.turn('The pizza was from The Y', cid)
        third = app.conversations.turn('I meant cheese pizza', cid)
        self.assertEqual(app.memory.conversation(cid)['foods'][0]['quantity'], 1)
        self.assertEqual(len(third['cards']), 3)
        app.conversations.turn('Actually it was 3 scoops of rice, not 2', cid)
        fourth = app.conversations.turn('each scoop was 3 oz', cid)
        self.assertEqual(len(app.memory.conversation(cid)['foods']), 4)
        self.assertEqual(app.memory.conversation(cid)['foods'][1]['quantity'], 3)
        self.assertTrue(fourth['options'])
        chosen = app.conversations.turn('the first one', cid)
        self.assertEqual(chosen['kind'], 'clarification')
        listed = next(c for c in chosen['cards'] if c['item_index'] == 3)['serving_size']
        final = app.conversations.turn('each scoop was ' + listed, cid)
        self.assertEqual(final['kind'], 'result')
        self.assertEqual(len(final['cards']), 4)
        self.assertTrue(final['complete'])
        self.assertEqual(final['total']['calories'], sum(c['nutrition']['calories'] for c in final['cards']))
        self.assertEqual(app.memory.meals(), [])

    def test_pending_log_survives_restart_and_logs_only_once_after_portion_answer(self):
        foods=[{'name':'cilantro lime rice','quantity':2,'unit':'scoop','hall':'south','date':DAY},
               {'name':'chocolate chip cookie','quantity':1,'unit':'each','hall':'south','date':DAY}]
        app=self.app([{'operation':'log','label':'lunch','foods':foods}])
        first=app.conversations.turn('Add 2 scoops of cilantro lime rice and 1 chocolate chip cookie for lunch',None)
        cid=first['conversation_id']
        self.assertEqual(app.memory.meals(),[])
        restarted=self.app([{'operation':'log'}])
        logged=restarted.conversations.turn('each scoop was 3 oz',cid)
        self.assertEqual(logged['kind'],'meal_logged')
        self.assertEqual(logged['tracker']['totals']['calories'],382)
        again=restarted.conversations.turn("Can you add that to today's macros?",cid)
        self.assertEqual(again['kind'],'already_logged')
        self.assertEqual(len(restarted.memory.meals()),1)
        self.assertEqual(again['tracker']['totals']['calories'],382)

    def test_aliases_typos_and_unrelated_foods(self):
        regular=food('Macaroni and Cheese')
        self.assertEqual(choose_food('mac n cheese',[regular])[0],regular)
        chicken=food('Grilled Chicken Breast')
        self.assertEqual(choose_food('grilled chiken breast',[chicken])[0],chicken)
        self.assertIsNone(choose_food('chicken breast',[food('Grilled Chipotle Lime Chicken')])[0])
        self.assertIsNone(choose_food('macaroni pizza',[food('Cheese Pizza')])[0])
        self.assertIsNone(choose_food('cilantro lime rice',[food('Cilantro Lime Grilled Chicken')])[0])

    def test_ambiguous_mac_and_cheese_has_specific_question_and_options(self):
        data={'menu_date':DAY,'foods':[food('Macaroni and Cheese'),food('Baked Macaroni and Cheese')]}
        result=resolve([item('mac n cheese')],[data],DAY)
        self.assertEqual(result['kind'],'clarification')
        self.assertIn('did you mean',result['message'])
        self.assertEqual(len(result['options']),2)
        self.assertIsNone(result['total'])

    def test_natural_candidate_selection_is_grounded_not_model_guess(self):
        self.fixture={'menu_date':DAY,'foods':[food('Macaroni and Cheese'),food('Baked Macaroni and Cheese')]}
        app=self.app([{'operation':'lookup','foods':[item('mac n cheese')]},
                      {'operation':'lookup','selection':1}, {'operation':'lookup','selection':2}])
        first=app.conversations.turn('A serving of mac n cheese',None)
        cid=first['conversation_id']
        undecided=app.conversations.turn("I don't know",cid)
        self.assertEqual(undecided['kind'],'clarification')
        self.assertIsNone(undecided['total'])
        # Ranking sorts regular first, baked second; the adjective clearly selects the baked label.
        baked=app.conversations.turn('the baked one',cid)
        self.assertEqual(baked['kind'],'result')
        self.assertEqual(baked['cards'][0]['name'],'Baked Macaroni and Cheese')

    def test_remove_cookie_from_working_meal_preserves_other_items(self):
        app=self.app([{'operation':'lookup','foods':[item('Brown Rice'),item('Chocolate Chip Cookie',unit='each',hall='south')]},
                      {'operation':'remove','action':'drop','updates':[{'item_index':1}]}])
        first=app.conversations.turn('I had brown rice and a chocolate chip cookie',None)
        changed=app.conversations.turn('Wait, remove the cookie',first['conversation_id'])
        state=app.memory.conversation(first['conversation_id'])
        self.assertEqual([i['name'] for i in state['foods']],['Brown Rice'])
        self.assertEqual(app.memory.meals(),[])

    def test_logged_correction_replaces_ledger_and_undo_still_works(self):
        app=self.app([{'operation':'log','foods':[item('Chocolate Chip Cookie',unit='each',hall='south')]},
                      {'operation':'edit','updates':[{'item_index':0,'quantity':2}]}, {'operation':'undo'}])
        first=app.conversations.turn('Log 1 chocolate chip cookie',None)
        self.assertEqual(first['kind'],'meal_logged')
        corrected=app.conversations.turn('Actually it was 2 cookies',first['conversation_id'])
        self.assertEqual(corrected['kind'],'meal_edited')
        self.assertEqual(corrected['tracker']['totals']['calories'],376)
        undone=app.conversations.turn('Undo that correction',first['conversation_id'])
        self.assertEqual(undone['tracker']['totals']['calories'],188)

    def test_report_is_not_permission_even_if_model_returns_log(self):
        app=self.app([{'operation':'log','foods':[item('Chocolate Chip Cookie',unit='each',hall='south')]}])
        first=app.conversations.turn('I had a chocolate chip cookie',None)
        self.assertEqual(first['kind'],'clarification')
        self.assertEqual(app.memory.meals(),[])

    def test_referring_to_this_reuses_all_foods(self):
        app=self.app([{'operation':'lookup','foods':[item('Chocolate Chip Cookie',unit='each',hall='south')]}, {'operation':'lookup'}])
        first=app.conversations.turn('I had a chocolate chip cookie',None)
        next_reply=app.conversations.turn('How many calories are in this?',first['conversation_id'])
        self.assertEqual(first['total'],next_reply['total'])
        self.assertEqual(first['result_id'],next_reply['result_id'])

    def test_same_as_yesterday_requires_actual_unique_verified_meal(self):
        app=self.app([{'operation':'log','foods':[item('Chocolate Chip Cookie',unit='each',hall='south')]},
                      {'operation':'lookup'}])
        first=app.conversations.turn('Log a chocolate chip cookie',None)
        self.now=datetime(2026,10,20,18,tzinfo=timezone.utc)
        repeated=app.conversations.turn('I had the same thing as yesterday',first['conversation_id'])
        self.assertEqual(repeated['kind'],'result')
        self.assertEqual(repeated['cards'][0]['menu_date'],DAY)
        self.assertEqual(app.memory.tracker()['totals']['calories'],0)
        self.assertEqual(len(app.memory.meals()),1)

    def test_same_as_yesterday_without_record_asks(self):
        app=self.app([{'operation':'lookup'}])
        result=app.conversations.turn('I had the same thing as yesterday',None)
        self.assertEqual(result['kind'],'clarification')
        self.assertEqual(app.memory.meals(),[])

    def test_changed_working_meal_cannot_log_stale_complete_result(self):
        app=self.app([{'operation':'lookup','foods':[item('Chocolate Chip Cookie',unit='each',hall='south')]},
                      {'operation':'lookup','action':'add','foods':[{'name':'not a real pizza','unit':'slice'}]}, {'operation':'log'}])
        first=app.conversations.turn('A chocolate chip cookie',None)
        second=app.conversations.turn('And not a real pizza',first['conversation_id'])
        self.assertIsNone(second['total'])
        last=app.conversations.turn('Add that to my macros',first['conversation_id'])
        self.assertEqual(last['kind'],'clarification')
        self.assertEqual(app.memory.meals(),[])

    def test_scoops_are_not_servings_and_known_weight_is_deterministic(self):
        data={'menu_date':DAY,'foods':[food('Brown Rice',serving='3 oz')]}
        unknown=resolve([item('brown rice',2,'scoop')],[data],DAY)
        self.assertIsNone(unknown['total'])
        measured=item('brown rice',2,'scoop'); measured.update(portion_amount=3,portion_unit='oz')
        known=resolve([measured],[data],DAY)
        self.assertEqual(known['cards'][0]['quantity'],2)
        self.assertEqual(known['total']['calories'],360)
        cup=resolve([item('brown rice',2,'cup')],[data],DAY)
        self.assertIsNone(cup['total'])


    def test_identical_official_label_across_halls_does_not_need_hall(self):
        data={'menu_date':DAY,'foods':[food('Brown Rice'),food('Brown Rice',hall='251 North')]}
        result=resolve([item('Brown Rice',hall='')],[data],DAY)
        self.assertEqual(result['kind'],'result')
        self.assertTrue(any('same official label' in note for note in result['notes']))

    def test_cancel_pending_log_before_portion_confirmation(self):
        app=self.app([{'operation':'log','foods':[{'name':'Cilantro Lime Rice','quantity':2,'unit':'scoop','hall':'south'}]},
                      {'operation':'lookup'}])
        first=app.conversations.turn('Log 2 scoops of cilantro lime rice',None)
        app.conversations.turn("Just look it up, don't log it",first['conversation_id'])
        final=app.conversations.turn('each scoop was 3 oz',first['conversation_id'])
        self.assertEqual(final['kind'],'result')
        self.assertEqual(app.memory.meals(),[])

    def test_quantity_correction_with_two_scooped_foods_needs_target(self):
        app=self.app([{'operation':'lookup','foods':FOUR},
                      {'operation':'lookup','updates':[{'item_index':1,'quantity':3,'unit':'scoop'}]}])
        first=app.conversations.turn(EXAMPLE,None)
        result=app.conversations.turn('Actually it was 3 scoops',first['conversation_id'])
        self.assertEqual(result['kind'],'clarification')
        self.assertIn('Which food',result['message'])
        self.assertEqual(app.memory.conversation(first['conversation_id'])['foods'][1]['quantity'],2)

    def test_legacy_result_with_assumed_scoop_cannot_bypass_portion_guard(self):
        app=self.app([{'operation':'lookup','foods':[item('Chocolate Chip Cookie',unit='each',hall='south')]}])
        first=app.conversations.turn('A chocolate chip cookie',None)
        stored=app.memory.result(first['result_id'])
        card=stored['cards'][0]
        card.pop('portion_confirmed');card['unit']='scoop'
        legacy=app.memory.save_result(first['conversation_id'],stored)
        with self.assertRaises(ValueError):
            app.memory.log_result(legacy,DAY,first['conversation_id'])

    def test_preference_is_never_permission_for_a_meal(self):
        app=self.app([{'operation':'log','foods':[item('Chocolate Chip Cookie',unit='each',hall='south')]}])
        first=app.conversations.turn('I prefer to add a chocolate chip cookie',None)
        self.assertEqual(first['kind'],'clarification')
        self.assertEqual(app.memory.meals(),[])

    def test_portion_correction_does_not_erase_other_logged_foods(self):
        app=self.app([{'operation':'log','foods':[item('Chocolate Chip Cookie',unit='each',hall='south'),item('Cheese Pizza',unit='slice')]},
                      {'operation':'edit','updates':[{'item_index':0,'quantity':2}]},
                      {'operation':'undo'}, {'operation':'lookup'}])
        first=app.conversations.turn('Log 1 chocolate chip cookie and a slice of cheese pizza',None)
        app.conversations.turn('Actually make the cookie 2 cookies',first['conversation_id'])
        undone=app.conversations.turn('Undo that correction',first['conversation_id'])
        after=app.conversations.turn('How many calories are in this?',first['conversation_id'])
        self.assertEqual(after['total']['calories'],undone['tracker']['totals']['calories'])
        self.assertEqual(len(after['cards']),2)


    def test_latest_add_result_button_keeps_followup_correction_context(self):
        app=self.app([{'operation':'lookup','foods':[item('Chocolate Chip Cookie',unit='each',hall='south')]},
                      {'operation':'edit','updates':[{'item_index':0,'quantity':2}]}])
        first=app.conversations.turn('A chocolate chip cookie',None)
        payload=json.dumps({'result_id':first['result_id'],'conversation_id':first['conversation_id']}).encode()
        handler=object.__new__(AppHandler); handler.app=app; handler.path='/api/tracker/add-result'
        handler.headers={'Host':'localhost:8000','Content-Type':'application/json','Content-Length':str(len(payload)),
                         'X-MacroMellow-Request':'state'}
        handler.rfile=io.BytesIO(payload); responses=[]
        handler._json=lambda status,data:responses.append((status,data))
        handler.do_POST()
        self.assertEqual(responses[-1][0],200)
        corrected=app.conversations.turn('Actually make that 2 cookies',first['conversation_id'])
        self.assertEqual(corrected['kind'],'meal_edited')
        self.assertEqual(corrected['tracker']['totals']['calories'],376)

    def test_ambiguous_cookie_removal_does_not_choose_a_variant(self):
        self.fixture={'menu_date':DAY,'foods':[food('Chocolate Chip Cookie'),food('Sugar Cookie')]}
        app=self.app([{'operation':'lookup','foods':[item('Chocolate Chip Cookie'),item('Sugar Cookie')]},
                      {'operation':'lookup','action':'drop','updates':[{'item_index':0}]}])
        first=app.conversations.turn('I had chocolate chip cookie and sugar cookie',None)
        reply=app.conversations.turn('Remove the cookie',first['conversation_id'])
        self.assertEqual(reply['kind'],'clarification')
        self.assertEqual(len(app.memory.conversation(first['conversation_id'])['foods']),2)


if __name__ == '__main__':
    unittest.main()
