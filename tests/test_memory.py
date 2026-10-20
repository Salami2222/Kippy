import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from macromellow.memory import MemoryStore
from macromellow.umd import save_json
from macromellow.web import DataApp

from test_agent import DAY, food, item


class PlannedOllama:
    model = "test-planner"

    def __init__(self, plans):
        self.plans = iter(plans)
        self.contexts = []

    def interpret(self, message, previous, today, dates, last_question="", recent=None,
                  memories=None, recent_meals=None):
        self.contexts.append({"previous": previous, "recent": recent, "memories": memories,
                              "meals": recent_meals, "today": today})
        return next(self.plans)


def plan(operation, foods=None, **extra):
    return {"action": "set", "operation": operation, "foods": foods or [], "question": "", **extra}


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.instant = datetime(2026, 10, 19, 16, tzinfo=timezone.utc)
        self.store = MemoryStore(self.path / "macromellow.sqlite3", now=lambda: self.instant)

    def test_daily_rollover_retains_history_and_goals(self):
        self.store.set_goals({"calories": 2000, "protein_g": 120})
        self.store.adjust("add", {"calories": 450, "protein_g": 30})
        self.assertEqual(self.store.tracker()["totals"]["calories"], 450)
        self.instant = datetime(2026, 10, 20, 4, tzinfo=timezone.utc)
        self.assertEqual(self.store.today(), "2026-10-20")
        self.assertEqual(self.store.tracker()["totals"]["calories"], 0)
        self.assertEqual(self.store.tracker()["goals"]["calories"], 2000)
        self.assertEqual(self.store.tracker(DAY)["totals"]["protein_g"], 30)
        restarted = MemoryStore(self.store.path, now=lambda: self.instant)
        self.assertEqual(restarted.tracker()["goals"]["protein_g"], 120)

    def test_browser_import_once_and_keeps_local_day(self):
        state = {"date": DAY, "totals": {"calories": 500, "protein_g": 20,
                                           "carbohydrates_g": 40, "fat_g": 9}}
        goals = {"calories": 2200, "protein_g": 100, "carbohydrates_g": None, "fat_g": None}
        first = self.store.import_legacy("browser-1", state, goals)
        self.assertTrue(first["imported_totals"])
        self.assertEqual(first["tracker"]["totals"]["calories"], 500)
        self.assertTrue(self.store.import_legacy("browser-1", state, goals)["already_imported"])
        self.assertEqual(self.store.tracker()["totals"]["calories"], 500)
        self.instant = datetime(2026, 10, 20, 4, tzinfo=timezone.utc)
        self.assertEqual(self.store.tracker()["totals"]["calories"], 0)
        self.assertEqual(self.store.tracker()["goals"]["calories"], 2200)

    def test_browser_import_does_not_merge_over_existing_backend_totals(self):
        self.store.adjust("add", {"calories": 100})
        state = {"date": DAY, "totals": {"calories": 500, "protein_g": 20,
                                           "carbohydrates_g": 40, "fat_g": 9}}
        imported = self.store.import_legacy("other-browser", state, {"calories": 2100})
        self.assertTrue(imported["conflict"])
        self.assertFalse(imported["imported_totals"])
        self.assertEqual(imported["tracker"]["totals"]["calories"], 100)
        self.assertEqual(imported["tracker"]["goals"]["calories"], 2100)

    def test_preferences_are_separate_from_log_and_editable(self):
        saved = self.store.save_memory("favorite_meal", "usual lunch", "Chicken and rice")
        self.assertEqual(self.store.tracker()["totals"]["calories"], 0)
        self.store.update_memory(saved["id"], "preference", "usual lunch", "Extra rice")
        self.assertEqual(self.store.memories()[0]["value"], "Extra rice")
        self.assertTrue(self.store.delete_memory(saved["id"]))
        self.assertEqual(self.store.memories(), [])

    def test_agent_remembers_and_recalls_without_logging(self):
        planner = PlannedOllama([
            plan("remember", memory_category="preference", memory_key="lunch rice",
                 memory_value="I prefer rice at lunch"),
            plan("recall"),
        ])
        app = DataApp(self.path, planner)
        app.memory.now = lambda: self.instant
        saved = app.chat("Remember that I prefer rice at lunch", None)
        self.assertEqual(saved["kind"], "memory_saved")
        restarted = DataApp(self.path, planner)
        restarted.memory.now = lambda: self.instant
        recalled = restarted.chat("What do you remember about my preferences?", saved["conversation_id"])
        self.assertEqual(recalled["kind"], "memories")
        self.assertEqual(recalled["memories"][0]["value"], "I prefer rice at lunch")
        self.assertEqual(planner.contexts[1]["memories"][0]["key"], "lunch rice")
        self.assertEqual(restarted.memory.tracker()["totals"]["calories"], 0)

    def test_log_duplicate_edit_and_undo_survive_restart(self):
        menu = {"schema_version": 1, "source": "UMD Dining Services", "menu_date": DAY,
                "retrieved_at": "2026-10-19T19:00:00-04:00", "errors": [],
                "foods": [food("Grilled Chicken Breast")]}
        save_json(menu, self.path / "menu.json")
        planner = PlannedOllama([
            plan("log", [item("grilled chicken breast", 2)], label="lunch", log_date=DAY),
            plan("log", [], label="lunch", log_date=DAY),
            plan("edit", [item("grilled chicken breast", 3)], label="lunch", log_date=DAY),
            plan("undo"),
        ])
        app = DataApp(self.path, planner)
        app.memory.now = lambda: self.instant
        with_today = lambda: {"today": DAY, "datasets": [menu], "load_errors": []}
        app.datasets = with_today
        first = app.chat("Log 2 servings of grilled chicken breast for lunch", None)
        self.assertEqual(first["kind"], "meal_logged")
        self.assertEqual(first["tracker"]["totals"]["calories"], 360)
        second = app.chat("Log lunch", first["conversation_id"])
        self.assertEqual(second["kind"], "already_logged")
        self.assertEqual(second["tracker"]["totals"]["calories"], 360)
        corrected = app.chat("Actually make that 3 servings", first["conversation_id"])
        self.assertEqual(corrected["kind"], "meal_edited")
        self.assertEqual(corrected["tracker"]["totals"]["calories"], 540)
        self.assertEqual(planner.contexts[2]["previous"][0]["quantity"], 2)
        restarted = DataApp(self.path, planner)
        restarted.memory.now = lambda: self.instant
        restarted.datasets = with_today
        undone = restarted.chat("Undo that correction", first["conversation_id"])
        self.assertEqual(undone["kind"], "undone")
        self.assertEqual(undone["tracker"]["totals"]["calories"], 360)
        self.assertTrue(any("Actually make that 3" in turn["text"] for turn in planner.contexts[3]["recent"]))
        self.assertEqual(len(restarted.memory.meals()), 2)
        active = next(meal for meal in restarted.memory.meals() if meal["status"] == "active")
        restarted.memory.remove_meal(active["id"])
        self.assertEqual(restarted.memory.tracker()["totals"]["calories"], 0)
        restarted.memory.restore_meal(active["id"])
        self.assertEqual(restarted.memory.tracker()["totals"]["calories"], 360)

    def test_ambiguous_portion_does_not_log(self):
        menu = {"schema_version": 1, "source": "UMD Dining Services", "menu_date": DAY,
                "retrieved_at": "2026-10-19T19:00:00-04:00", "errors": [],
                "foods": [food("Grilled Chicken Breast", serving="4 oz")]}
        save_json(menu, self.path / "menu.json")
        app = DataApp(self.path, PlannedOllama([plan("log", [item("grilled chicken breast", 2, "each")],
                                                    label="lunch", log_date=DAY)]))
        app.memory.now = lambda: self.instant
        app.datasets = lambda: {"today": DAY, "datasets": [menu], "load_errors": []}
        result = app.chat("Log two grilled chicken breasts for lunch", None)
        self.assertEqual(result["kind"], "clarification")
        self.assertEqual(app.memory.tracker()["totals"]["calories"], 0)

    def test_lookup_then_explicit_add_uses_previous_verified_result(self):
        menu = {"schema_version": 1, "source": "UMD Dining Services", "menu_date": DAY,
                "retrieved_at": "2026-10-19T19:00:00-04:00", "errors": [],
                "foods": [food("Grilled Chicken Breast")]}
        save_json(menu, self.path / "menu.json")
        planner = PlannedOllama([
            plan("lookup", [item("grilled chicken breast", 2)]),
            plan("log", [], label="lunch", log_date=DAY),
        ])
        app = DataApp(self.path, planner)
        app.memory.now = lambda: self.instant
        app.datasets = lambda: {"today": DAY, "datasets": [menu], "load_errors": []}
        lookup = app.chat("How many calories in two servings of grilled chicken breast?", None)
        self.assertEqual(lookup["kind"], "result")
        self.assertEqual(app.memory.tracker()["totals"]["calories"], 0)
        logged = app.chat("Add my lunch to today's macros", lookup["conversation_id"])
        self.assertEqual(logged["kind"], "meal_logged")
        self.assertEqual(logged["tracker"]["totals"]["calories"], 360)
        self.assertEqual(app.memory.meals()[0]["label"], "lunch")

    def test_yesterday_log_stays_out_of_today(self):
        self.instant = datetime(2026, 10, 20, 16, tzinfo=timezone.utc)
        menu = {"schema_version": 1, "source": "UMD Dining Services", "menu_date": DAY,
                "retrieved_at": "2026-10-19T19:00:00-04:00", "errors": [],
                "foods": [food("Grilled Chicken Breast")]}
        save_json(menu, self.path / "menu.json")
        app = DataApp(self.path, PlannedOllama([
            plan("log", [item("grilled chicken breast", 1, day=DAY)], label="dinner", log_date=DAY)]))
        app.memory.now = lambda: self.instant
        app.datasets = lambda: {"today": "2026-10-20", "datasets": [menu], "load_errors": []}
        result = app.chat("Log yesterday's dinner: one serving of grilled chicken breast", None)
        self.assertEqual(result["kind"], "meal_logged")
        self.assertEqual(app.memory.tracker()["totals"]["calories"], 0)
        self.assertEqual(app.memory.tracker(DAY)["totals"]["calories"], 180)
        self.assertEqual(app.memory.meals()[0]["date"], DAY)

    def test_mentioning_eating_is_not_permission_to_log(self):
        menu = {"schema_version": 1, "source": "UMD Dining Services", "menu_date": DAY,
                "retrieved_at": "2026-10-19T19:00:00-04:00", "errors": [],
                "foods": [food("Grilled Chicken Breast")]}
        save_json(menu, self.path / "menu.json")
        app = DataApp(self.path, PlannedOllama([
            plan("log", [item("grilled chicken breast")], log_date=DAY)]))
        app.memory.now = lambda: self.instant
        app.datasets = lambda: {"today": DAY, "datasets": [menu], "load_errors": []}
        result = app.chat("I ate grilled chicken breast. What are its macros?", None)
        self.assertEqual(result["kind"], "clarification")
        self.assertEqual(app.memory.tracker()["totals"]["calories"], 0)

    def test_log_without_food_or_prior_result_asks_for_details(self):
        app = DataApp(self.path, PlannedOllama([plan("log", [])]))
        app.memory.now = lambda: self.instant
        result = app.chat("Log my lunch today", None)
        self.assertEqual(result["kind"], "clarification")
        self.assertEqual(app.memory.tracker()["totals"]["calories"], 0)


if __name__ == "__main__":
    unittest.main()
