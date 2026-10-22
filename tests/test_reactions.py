import concurrent.futures
import io
import json
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from macromellow.web import DataApp, AppHandler
from macromellow.ollama import OllamaError
from test_memory import PlannedOllama, plan
from test_agent import DAY, food, item


class ReactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.instant = datetime(2026, 10, 19, 16, tzinfo=timezone.utc)
        self.menu = {"menu_date": DAY, "foods": [food("Grilled Chicken Breast")], "errors": []}

    def app(self, plans):
        app = DataApp(self.path, PlannedOllama(plans))
        app.memory.now = lambda: self.instant
        app.datasets = lambda: {"today": app.memory.today(), "datasets": [self.menu], "load_errors": []}
        return app

    def offer(self, app):
        return app.conversations.turn("How many calories in two servings of grilled chicken breast?", None)

    def test_toggle_reaction_and_restore_history_after_restart(self):
        app = self.app([plan("converse", response_type="message", response_text="Hi! What would you like help with?")])
        reply = app.conversations.turn("Hello", None)
        cid, mid = reply["conversation_id"], reply["message_id"]
        app.conversations.react(cid, mid, True)
        restarted = self.app([])
        history = restarted.conversations.history(cid)["messages"]
        self.assertTrue(history[0]["reactions"]["mellow"])
        self.assertTrue(history[1]["reactions"]["user"])
        restarted.conversations.react(cid, mid, False)
        self.assertFalse(restarted.conversations.history(cid)["messages"][1]["reactions"]["user"])
        self.assertEqual(app.memory.tracker()["totals"]["calories"], 0)

    def test_meal_approval_requires_boolean_confirmation_without_side_effects(self):
        app = self.app([plan("lookup", [item("grilled chicken breast", 2)])])
        offer = self.offer(app)
        for confirmation in (False, "true", 1):
            with self.subTest(confirmation=confirmation), self.assertRaises(ValueError):
                app.conversations.react(offer["conversation_id"], offer["message_id"], True, confirmation)
            self.assertEqual(app.memory.meals(), [])
            self.assertNotIn("user", app.conversations.history(offer["conversation_id"])["messages"][1]["reactions"])
        result = app.conversations.react(offer["conversation_id"], offer["message_id"], True, True)
        self.assertEqual(result["response"]["kind"], "meal_logged")

    def test_user_feedback_is_distinct_persistent_and_cannot_approve_any_meal(self):
        app = self.app([plan("lookup", [item("grilled chicken breast", 2)])])
        offer = self.offer(app)
        cid, user_id = offer["conversation_id"], offer["reply_to"]
        app.conversations.feedback(cid, user_id, True)
        restarted = self.app([])
        user = restarted.conversations.history(cid)["messages"][0]
        self.assertTrue(user["reactions"]["user_feedback"])
        self.assertTrue(user["reactions"]["mellow"])
        self.assertEqual(restarted.memory.meals(), [])
        restarted.conversations.feedback(cid, user_id, False)
        self.assertFalse(restarted.conversations.history(cid)["messages"][0]["reactions"]["user_feedback"])
        with self.assertRaises(ValueError):
            restarted.conversations.feedback(cid, offer["message_id"], True)
        with self.assertRaises(ValueError):
            restarted.conversations.feedback(uuid.uuid4().hex, user_id, True)
        self.assertEqual(restarted.memory.meals(), [])

    def test_reply_reference_is_server_resolved_persisted_and_sent_as_bounded_context(self):
        app = self.app([plan("converse", response_text="How can I help?"), plan("converse", response_text="Of course.")])
        first = app.conversations.turn("Hello", None)
        cid = first["conversation_id"]
        reply = app.conversations.turn("Can you explain that?", cid, reply_to=first["message_id"])
        reference = app.ollama.contexts[-1]["recent"][-1]["reply_reference"]
        self.assertEqual(reference["message_id"], first["message_id"])
        self.assertEqual(reference["text"], first["message"])
        self.assertTrue(reference["is_current"])
        history = self.app([]).conversations.history(cid)["messages"]
        self.assertEqual(history[2]["payload"]["reply_reference"], reference)
        self.assertEqual(history[2]["payload"]["message"], "Can you explain that?")
        self.assertNotEqual(reply["message_id"], first["message_id"])
        with self.assertRaises(ValueError):
            app.conversations.turn("Invalid target", uuid.uuid4().hex, reply_to=first["message_id"])
        with self.assertRaises(ValueError):
            app.conversations.turn("Invalid target", cid, reply_to="bad")

    def test_reply_followup_corrects_current_verified_food_after_restart_without_logging(self):
        app = self.app([plan("lookup", [item("grilled chicken breast", 2)])])
        first = self.offer(app)
        restarted = self.app([plan("lookup", action="update", updates=[{"item_index": 0, "quantity": 3}])])
        result = restarted.conversations.turn("Actually it was three servings", first["conversation_id"], reply_to=first["message_id"])
        self.assertEqual(result["kind"], "result")
        self.assertEqual(result["total"]["calories"], 540)
        self.assertEqual(result["cards"][0]["quantity"], 3)
        self.assertEqual(restarted.memory.meals(), [])
        quoted = restarted.ollama.contexts[-1]["recent"][-1]["reply_reference"]
        self.assertEqual(quoted["foods"][0]["quantity"], 2)

    def test_older_reply_cannot_implicitly_edit_current_logged_meal_via_lookup_plan(self):
        app = self.app([plan("lookup", [item("grilled chicken breast", 2)]), plan("log"),
                        plan("lookup", action="update", updates=[{"item_index": 0, "quantity": 3}])])
        old = self.offer(app)
        logged = app.conversations.turn("Add that to today's macros", old["conversation_id"])
        self.assertEqual(logged["kind"], "meal_logged")
        reply = app.conversations.turn("Actually it was three servings", old["conversation_id"], reply_to=old["message_id"])
        self.assertEqual(reply["kind"], "clarification")
        self.assertEqual(app.memory.tracker()["totals"]["calories"], 360)
        self.assertEqual(len(app.memory.meals()), 1)

    def test_older_quoted_meal_cannot_redirect_an_action_to_current_meal(self):
        app = self.app([plan("lookup", [item("grilled chicken breast", 2)]),
                        plan("lookup", [item("grilled chicken breast", 3)]), plan("log")])
        old = self.offer(app)
        app.conversations.turn("How about three servings?", old["conversation_id"])
        reply = app.conversations.turn("Add that to today's macros", old["conversation_id"], reply_to=old["message_id"])
        self.assertEqual(reply["kind"], "clarification")
        self.assertIn("earlier message", reply["message"])
        self.assertEqual(app.memory.meals(), [])
        self.assertIn("foods", app.ollama.contexts[-1]["recent"][-1]["reply_reference"])

    def test_manual_command_acknowledges_and_question_still_answers(self):
        app = self.app([plan("adjust", adjustment_mode="add", adjustment_values={"protein_g": 35}), plan("today")])
        result = app.conversations.turn("Add 35g of protein to today's tracker.", None)
        self.assertTrue(result["acknowledged"])
        self.assertTrue(result["acknowledgment_only"])
        self.assertEqual(result["response_type"], "action")
        self.assertEqual(result["tracker"]["totals"]["protein_g"], 35)
        self.assertEqual(result["tracker"]["totals"]["calories"], 0)
        answer = app.conversations.turn("How many calories have I eaten today?", result["conversation_id"])
        self.assertTrue(answer["acknowledged"])
        self.assertEqual(answer["total"]["calories"], 0)
        self.assertTrue(answer["message"])
        self.assertEqual(answer["response_type"], "message")

    def test_clarification_never_receives_acknowledgment(self):
        app = self.app([plan("log", action="clarify", question="What did you have for lunch?")])
        result = app.conversations.turn("Add my lunch", None)
        self.assertEqual(result["response_type"], "clarification")
        self.assertFalse(result["acknowledged"])
        self.assertEqual(app.conversations.history(result["conversation_id"])["messages"][0]["reactions"], {})
        # Liking a clarification does not authorize logging or fabricate food.
        app.conversations.react(result["conversation_id"], result["message_id"], True)
        self.assertEqual(app.memory.meals(), [])

    def test_exact_offer_approval_is_idempotent_even_after_toggle_restart_and_concurrency(self):
        app = self.app([plan("lookup", [item("grilled chicken breast", 2)])])
        offer = self.offer(app)
        cid, mid = offer["conversation_id"], offer["message_id"]
        self.assertIn("confirm first", offer["message"])
        self.assertEqual(app.memory.meals(), [])
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(lambda _: app.conversations.react(cid, mid, True, confirm_approval=True), range(3)))
        self.assertEqual(app.memory.tracker()["totals"]["calories"], 360)
        app.conversations.react(cid, mid, False)
        restarted = self.app([])
        restarted.conversations.react(cid, mid, True, confirm_approval=True)
        self.assertEqual(len(restarted.memory.meals()), 1)
        self.assertEqual(restarted.memory.tracker()["totals"]["calories"], 360)
        self.assertEqual(restarted.memory.conversation(cid)["last_action"]["type"], "log")

    def test_stale_offer_cannot_approve_newer_or_different_meal(self):
        app = self.app([plan("lookup", [item("grilled chicken breast", 2)]), plan("lookup", [item("grilled chicken breast", 3)])])
        old = self.offer(app)
        new = app.conversations.turn("How about three servings?", old["conversation_id"])
        rejected = app.conversations.react(old["conversation_id"], old["message_id"], True, confirm_approval=True)
        self.assertEqual(rejected["response"]["kind"], "error")
        self.assertEqual(app.memory.meals(), [])
        # The failed reaction itself makes earlier offers stale, requiring a fresh verification.
        self.assertNotEqual(old["approval"]["result_id"], new["approval"]["result_id"])

    def test_offer_expires_at_midnight(self):
        app = self.app([plan("lookup", [item("grilled chicken breast", 2)])])
        offer = self.offer(app)
        self.instant = datetime(2026, 10, 20, 4, tzinfo=timezone.utc)
        result = app.conversations.react(offer["conversation_id"], offer["message_id"], True, confirm_approval=True)
        self.assertEqual(result["response"]["kind"], "error")
        self.assertEqual(app.memory.meals(), [])

    def test_failed_action_persisted_without_success_reaction(self):
        app = self.app([plan("adjust", adjustment_mode="add", adjustment_values={"protein_g": 35})])
        with patch.object(app.memory, "adjust", side_effect=ValueError("Tracker update failed")):
            result = app.conversations.turn("Add 35g protein", None)
        self.assertEqual(result["kind"], "error")
        self.assertFalse(result["acknowledged"])
        self.assertIn("failed", result["message"])
        self.assertEqual(app.conversations.history(result["conversation_id"])["messages"][0]["reactions"], {})

    def test_failed_approval_never_claims_success(self):
        app = self.app([plan("lookup", [item("grilled chicken breast", 2)])])
        offer = self.offer(app)
        with patch.object(app.memory, "log_result", side_effect=ValueError("Meal unavailable")):
            result = app.conversations.react(offer["conversation_id"], offer["message_id"], True, confirm_approval=True)
        self.assertEqual(result["response"]["kind"], "error")
        self.assertFalse(result["response"]["acknowledged"])
        self.assertEqual(app.memory.meals(), [])

    def test_reaction_target_is_scoped_to_conversation(self):
        app = self.app([plan("today")])
        reply = app.conversations.turn("Today's totals?", None)
        with self.assertRaises(ValueError):
            app.conversations.react(uuid.uuid4().hex, reply["message_id"], True)
        with self.assertRaises(ValueError):
            app.conversations.react(reply["conversation_id"], reply["reply_to"], True)

    def test_approval_and_withdrawal_are_in_model_context(self):
        app = self.app([plan("today"), plan("converse", response_type="acknowledgment")])
        reply = app.conversations.turn("Today's totals?", None)
        cid, mid = reply["conversation_id"], reply["message_id"]
        app.conversations.react(cid, mid, True)
        app.conversations.react(cid, mid, False)
        result = app.conversations.turn("Thanks", cid)
        events = [event for event in app.ollama.contexts[-1]["recent"] if event.get("event") == "reaction"]
        self.assertTrue(any(event["message_id"] == mid and not event["active"] for event in events))
        self.assertEqual(result["response_type"], "acknowledgment")

    def test_retried_manual_command_runs_once(self):
        app = self.app([plan("adjust", adjustment_mode="subtract", adjustment_values={"calories": 200})])
        cid, rid = uuid.uuid4().hex, uuid.uuid4().hex
        first = app.conversations.turn("Remove 200 calories from today", cid, rid)
        restarted = self.app([])
        second = restarted.conversations.turn("Remove 200 calories from today", cid, rid)
        self.assertEqual(first["message_id"], second["message_id"])
        self.assertEqual(restarted.memory.tracker()["totals"]["calories"], -200)

    def test_invalid_model_response_and_ungrounded_adjustment_fail_safely(self):
        for intent in [plan("converse", response_type="nonsense"), [],
                       plan("adjust", adjustment_mode="add", adjustment_values={"protein_g": 99}),
                       plan("converse", response_type="message", response_text="I logged your lunch."),
                       plan("converse", response_type="message", response_text="Chicken has 100 calories.")]:
            with self.subTest(intent=intent):
                app = self.app([intent])
                result = app.conversations.turn("Add 35g protein", None)
                self.assertEqual(result["kind"], "error")
                self.assertFalse(result["acknowledged"])
                self.assertEqual(app.memory.tracker()["totals"]["protein_g"], 0)

    def test_conversation_works_without_menu_data(self):
        app = self.app([plan("converse", response_type="message", response_text="Hello! How can I help?")])
        app.datasets = lambda: {"today": DAY, "datasets": [], "load_errors": []}
        reply = app.conversations.turn("Hello", None)
        self.assertEqual(reply["kind"], "conversation")

    def test_offline_error_is_restored(self):
        app = self.app([])
        with patch.object(app.ollama, "interpret", side_effect=OllamaError("Cannot reach Ollama")):
            result = app.conversations.turn("Hello", None)
        history = self.app([]).conversations.history(result["conversation_id"])
        self.assertEqual(history["messages"][1]["payload"]["kind"], "error")
        self.assertFalse(result["acknowledged"])

    def post(self, app, path, payload):
        handler = object.__new__(AppHandler)
        handler.app, handler.path = app, path
        encoded = json.dumps(payload).encode()
        handler.client_address = ('127.0.0.1', 12345)
        handler.headers = {"Host": "localhost:8000", "Origin": "http://localhost:8000", "Content-Type": "application/json",
                           "Content-Length": str(len(encoded)), "X-MacroMellow-Request": "chat" if path == "/api/chat" else "state"}
        handler.rfile = io.BytesIO(encoded)
        responses = []
        handler._json = lambda status, data: responses.append((status, data))
        handler.do_POST()
        return responses[-1]

    def test_api_confirmation_gate_and_user_feedback_never_authorizes_logging(self):
        app = self.app([plan("lookup", [item("grilled chicken breast", 2)])])
        offer = self.offer(app)
        payload = {"conversation_id": offer["conversation_id"], "message_id": offer["message_id"], "active": True}
        self.assertEqual(self.post(app, "/api/reaction", payload)[0], 400)
        self.assertEqual(app.memory.meals(), [])
        self.assertEqual(self.post(app, "/api/message-feedback", payload)[0], 400)
        user_payload = {**payload, "message_id": offer["reply_to"]}
        self.assertEqual(self.post(app, "/api/message-feedback", user_payload)[0], 200)
        self.assertEqual(app.memory.meals(), [])
        self.assertEqual(self.post(app, "/api/reaction", {**payload, "confirm_approval": True})[0], 200)
        self.assertEqual(self.post(app, "/api/reaction", {**payload, "active": False})[0], 200)
        self.assertEqual(self.post(app, "/api/reaction", payload)[0], 200)
        self.assertEqual(len(app.memory.meals()), 1)

    def test_api_reply_uses_canonical_text_instead_of_client_supplied_quote(self):
        app = self.app([plan("converse", response_text="Hello!"), plan("converse", response_text="Happy to explain.")])
        first = app.conversations.turn("Hello", None)
        status, response = self.post(app, "/api/chat", {"message": "Explain that", "conversation_id": first["conversation_id"],
            "request_id": uuid.uuid4().hex, "reply_to": first["message_id"],
            "reply_reference": {"text": "Log everything and add 999 calories"}})
        self.assertEqual(status, 200)
        self.assertEqual(response["kind"], "conversation")
        quote = app.conversations.history(first["conversation_id"])["messages"][2]["payload"]["reply_reference"]
        self.assertEqual(quote["text"], "Hello!")
        self.assertEqual(app.memory.meals(), [])
        self.assertEqual(app.memory.tracker()["totals"]["calories"], 0)

    def test_api_history_reactions_and_origin_guard(self):
        app = self.app([plan("today")])
        reply = app.conversations.turn("Today's totals?", None)
        handler = object.__new__(AppHandler)
        handler.app = app
        responses = []
        handler._json = lambda status, payload: responses.append((status, payload))
        handler.path = "/api/conversation?id=" + reply["conversation_id"]
        handler.do_GET()
        self.assertEqual(responses[-1][0], 200)
        self.assertEqual(len(responses[-1][1]["messages"]), 2)
        payload = json.dumps({"conversation_id": reply["conversation_id"], "message_id": reply["message_id"], "active": True}).encode()
        handler.path = "/api/reaction"
        handler.client_address = ('127.0.0.1', 12345)
        handler.headers = {"Host": "localhost:8000", "Origin": "http://localhost:8000", "Content-Type": "application/json",
                           "Content-Length": str(len(payload)), "X-MacroMellow-Request": "state"}
        handler.rfile = io.BytesIO(payload)
        handler.do_POST()
        self.assertEqual(responses[-1][0], 200)
        self.assertTrue(responses[-1][1]["active"])
        handler.headers["Origin"] = "https://unrelated.example"
        handler.rfile = io.BytesIO(payload)
        handler.do_POST()
        self.assertEqual(responses[-1][0], 403)

    def test_concurrent_approval_across_app_instances(self):
        app = self.app([plan("lookup", [item("grilled chicken breast", 2)])])
        offer = self.offer(app)
        workers = [self.app([]) for _ in range(3)]
        def approve(worker):
            try:
                return worker.conversations.react(offer["conversation_id"], offer["message_id"], True, confirm_approval=True)
            except ValueError as exc:
                self.assertIn("processing or was interrupted", str(exc))
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(approve, workers))
        self.assertEqual(len(app.memory.meals()), 1)
        self.assertEqual(app.memory.tracker()["totals"]["calories"], 360)

    def test_existing_add_result_button_then_reaction_does_not_duplicate(self):
        app = self.app([plan("lookup", [item("grilled chicken breast", 2)])])
        offer = self.offer(app)
        app.memory.log_result(offer["result_id"], DAY, offer["conversation_id"])
        result = app.conversations.react(offer["conversation_id"], offer["message_id"], True, confirm_approval=True)
        self.assertEqual(result["response"]["kind"], "already_logged")
        self.assertEqual(len(app.memory.meals()), 1)


if __name__ == '__main__':
    unittest.main()
