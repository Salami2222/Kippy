"""SQLite memory and a deterministic, single-profile nutrition ledger."""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .agent import NUTRIENTS, portion_factor


NY = ZoneInfo("America/New_York")
PROFILE = "local"  # Kept on every row so storage can later be scoped to accounts.
MEMORY_TYPES = {"preference", "favorite_restaurant", "favorite_meal", "favorite_food",
                "common_meal", "frequent_food"}
ZERO = {key: 0 for key in NUTRIENTS}


def numbers(values: dict, *, complete: bool = False, nonnegative: bool = False) -> dict:
    if not isinstance(values, dict):
        raise ValueError("Enter macro values.")
    keys = NUTRIENTS if complete else [key for key in NUTRIENTS if key in values]
    if not keys:
        raise ValueError("Enter at least one macro value.")
    result = {}
    for key in keys:
        value = values.get(key)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or abs(value) > 1_000_000 or (nonnegative and value < 0)):
            raise ValueError("Macro values must be finite numbers within the supported range.")
        result[key] = round(value, 2)
    return result


def usda_portions(cards):
    """Canonical portions survive aliases, model labels, and older USDA fingerprints."""
    if not cards or any(c.get("source") != "USDA FoodData Central"
                        or not isinstance(c.get("record_key"), str) or not c["record_key"].startswith("usda:")
                        or type(c.get("quantity")) not in (int, float) for c in cards):
        return None
    return sorted((c["record_key"], c["quantity"]) for c in cards)


class MemoryStore:
    def __init__(self, path: Path, now=None) -> None:
        self._celebrations = ContextVar("protein_celebrations", default=None)
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.now = now or (lambda: datetime.now(timezone.utc))
        with self.db() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
                    state_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS results (
                    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entries (
                    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, log_date TEXT NOT NULL,
                    kind TEXT NOT NULL, status TEXT NOT NULL,
                    payload_json TEXT NOT NULL, source_result_id TEXT,
                    conversation_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(profile_id, source_result_id)
                );
                CREATE INDEX IF NOT EXISTS entries_day ON entries(profile_id, log_date, created_at);
                CREATE TABLE IF NOT EXISTS protein_celebrations (
                    profile_id TEXT NOT NULL, log_date TEXT NOT NULL,
                    PRIMARY KEY(profile_id, log_date)
                );
                CREATE TABLE IF NOT EXISTS goals (
                    profile_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, category TEXT NOT NULL,
                    key TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(profile_id, category, key)
                );
                CREATE TABLE IF NOT EXISTS migrations (
                    profile_id TEXT NOT NULL, migration_key TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(profile_id, migration_key)
                );
            """)

    @contextmanager
    def db(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    @contextmanager
    def capture_celebrations(self):
        """Request-local events: never stored in conversation responses or replayed by GET."""
        events = []
        token = self._celebrations.set(events)
        try:
            yield events
        finally:
            self._celebrations.reset(token)

    def _protein_crossing(self, con, day, previous):
        # Called within the ledger's write transaction, before committing either change.
        if day != self.today():
            return None
        row = con.execute("SELECT payload_json FROM goals WHERE profile_id=?", (PROFILE,)).fetchone()
        goal = json.loads(row["payload_json"]).get("protein_g") if row else None
        if (not isinstance(goal, (int, float)) or isinstance(goal, bool)
                or not math.isfinite(goal) or goal <= 0
                or not previous < goal <= self._totals(con, day)["protein_g"]):
            return None
        inserted = con.execute("INSERT OR IGNORE INTO protein_celebrations VALUES (?,?)", (PROFILE, day))
        return {"date": day, "kind": "protein_goal"} if inserted.rowcount else None

    def _emit_celebration(self, event):
        events = self._celebrations.get()
        if event and events is not None:
            events.append(event)

    def timestamp(self) -> str:
        return self.now().astimezone(timezone.utc).isoformat()

    def today(self) -> str:
        return self.now().astimezone(NY).date().isoformat()

    def conversation(self, conversation_id: str | None) -> dict:
        if not conversation_id:
            return {"foods": [], "question": "", "recent": [], "last_result_id": None,
                    "last_logged_id": None, "last_action": None, "pending": None, "selections": {}, "working_logged_id": None}
        with self.db() as con:
            row = con.execute("SELECT state_json FROM conversations WHERE id=? AND profile_id=?",
                              (conversation_id, PROFILE)).fetchone()
        return json.loads(row["state_json"]) if row else self.conversation(None)

    def save_conversation(self, conversation_id: str, state: dict) -> None:
        state["recent"] = state.get("recent", [])[-6:]
        with self.db() as con:
            con.execute("INSERT INTO conversations VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                        "state_json=excluded.state_json, updated_at=excluded.updated_at",
                        (conversation_id, PROFILE, json.dumps(state), self.timestamp()))

    def save_result(self, conversation_id: str, payload: dict) -> str:
        result_id = uuid.uuid4().hex
        with self.db() as con:
            con.execute("INSERT INTO results VALUES (?,?,?,?,?)",
                        (result_id, PROFILE, conversation_id, json.dumps(payload), self.timestamp()))
        return result_id

    def result(self, result_id: str) -> dict | None:
        with self.db() as con:
            row = con.execute("SELECT payload_json FROM results WHERE id=? AND profile_id=?",
                              (result_id, PROFILE)).fetchone()
        return json.loads(row["payload_json"]) if row else None

    @staticmethod
    def _totals(con, day: str) -> dict:
        totals = ZERO.copy()
        rows = con.execute("SELECT payload_json FROM entries WHERE profile_id=? AND log_date=? AND status='active'",
                           (PROFILE, day)).fetchall()
        for row in rows:
            for key, value in json.loads(row["payload_json"])["delta"].items():
                totals[key] = round(totals[key] + value, 2)
        return totals

    def tracker(self, day: str | None = None) -> dict:
        day = day or self.today()
        with self.db() as con:
            totals = self._totals(con, day)
            goals = con.execute("SELECT payload_json FROM goals WHERE profile_id=?", (PROFILE,)).fetchone()
            rows = con.execute("SELECT * FROM entries WHERE profile_id=? AND log_date=? ORDER BY created_at DESC",
                               (PROFILE, day)).fetchall()
        return {"date": day, "totals": totals,
                "goals": json.loads(goals["payload_json"]) if goals else dict.fromkeys(NUTRIENTS),
                "entries": [self._entry(row) for row in rows],
                "addedResultIds": [row["source_result_id"] for row in rows if row["source_result_id"]]}

    @staticmethod
    def _entry(row) -> dict:
        return {"id": row["id"], "date": row["log_date"], "kind": row["kind"],
                "status": row["status"], "created_at": row["created_at"],
                **json.loads(row["payload_json"])}

    def meals(self, limit: int = 30) -> list[dict]:
        with self.db() as con:
            rows = con.execute("SELECT * FROM entries WHERE profile_id=? AND kind='meal' "
                               "ORDER BY created_at DESC LIMIT ?", (PROFILE, min(limit, 100))).fetchall()
        return [self._entry(row) for row in rows]

    def entry(self, entry_id: str) -> dict | None:
        with self.db() as con:
            row = con.execute("SELECT * FROM entries WHERE id=? AND profile_id=?",
                              (entry_id, PROFILE)).fetchone()
        return self._entry(row) if row else None

    def log_result(self, result_id: str, day: str, conversation_id: str | None = None,
                   allow_repeat: bool = False, label_override: str | None = None) -> tuple[dict, bool]:
        result = self.result(result_id)
        if not result or result.get("kind") != "result" or not result.get("cards"):
            raise ValueError("This nutrition result is unavailable.")
        for card in result["cards"]:
            if card.get("portion_confirmed") is False:
                raise ValueError("Please confirm the reported portion before logging this result.")
            if "portion_confirmed" not in card:
                # Old saved results may have assumed a scoop was a serving. Revalidate them.
                factor, _ = portion_factor({"quantity": card.get("quantity", 1), "unit": card.get("unit", "unspecified")}, card)
                if factor is None or factor != card.get("quantity"):
                    raise ValueError("This older result needs a portion check. Ask Mellow to verify it again.")
        values = numbers(result.get("total"), complete=True, nonnegative=True)
        if any(card.get("menu_date") != day for card in result["cards"]):
            raise ValueError("The menu date differs from the log date. Specify a matching saved menu first.")
        fingerprint = result["fingerprint"]
        canonical_usda = usda_portions(result["cards"])
        label = (label_override or result.get("label") or "Meal").strip()[:80]
        stamp = self.timestamp()
        with self.db() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute("SELECT * FROM entries WHERE profile_id=? AND source_result_id=?",
                                   (PROFILE, result_id)).fetchone()
            if existing:
                return self._entry(existing), False
            if not allow_repeat:
                rows = con.execute("SELECT * FROM entries WHERE profile_id=? AND log_date=? AND kind='meal' "
                                   "AND status='active'", (PROFILE, day)).fetchall()
                for row in rows:
                    old_payload = json.loads(row["payload_json"])
                    if ((old_payload.get("fingerprint") == fingerprint and old_payload.get("label") == label)
                            or (canonical_usda and usda_portions(old_payload.get("cards")) == canonical_usda)):
                        return self._entry(row), False
            previous = self._totals(con, day)["protein_g"]
            entry_id = uuid.uuid4().hex
            payload = {"label": label, "cards": result["cards"],
                       "delta": values, "fingerprint": fingerprint, "replaces_id": None}
            con.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (entry_id, PROFILE, day, "meal", "active", json.dumps(payload),
                         result_id, conversation_id, stamp, stamp))
            event = self._protein_crossing(con, day, previous)
        self._emit_celebration(event)
        return {"id": entry_id, "date": day, "kind": "meal", "status": "active",
                "created_at": stamp, **payload}, True

    def adjust(self, mode: str, values: dict, day: str | None = None) -> dict:
        if mode not in {"add", "subtract", "set"}:
            raise ValueError("Choose add, subtract, or set.")
        given = numbers(values)
        day = day or self.today()
        stamp = self.timestamp()
        with self.db() as con:
            con.execute("BEGIN IMMEDIATE")
            current = self._totals(con, day)
            delta = {key: round((value - current[key] if mode == "set" else
                                 -value if mode == "subtract" else value), 2)
                     for key, value in given.items()}
            if any(abs(current[key] + value) > 1_000_000 for key, value in delta.items()):
                raise ValueError("The resulting total is too large.")
            con.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex, PROFILE, day, "adjustment", "active",
                         json.dumps({"label": "Manual adjustment", "delta": delta, "mode": mode,
                                     "values": given}), None, None, stamp, stamp))
            event = self._protein_crossing(con, day, current["protein_g"])
        self._emit_celebration(event)
        return self.tracker(day)

    def reset(self) -> dict:
        return self.adjust("set", ZERO)

    def set_goals(self, goals: dict) -> dict:
        if not isinstance(goals, dict):
            raise ValueError("Enter goals to save.")
        current = self.tracker()["goals"]
        for key in NUTRIENTS:
            if key not in goals:
                continue
            if goals[key] is None:
                current[key] = None
            else:
                current[key] = numbers({key: goals[key]}, nonnegative=True)[key]
        with self.db() as con:
            con.execute("INSERT INTO goals VALUES (?,?) ON CONFLICT(profile_id) DO UPDATE SET "
                        "payload_json=excluded.payload_json", (PROFILE, json.dumps(current)))
        return self.tracker()

    def remove_meal(self, entry_id: str) -> dict:
        stamp = self.timestamp()
        with self.db() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM entries WHERE id=? AND profile_id=? AND kind='meal' AND status='active'",
                              (entry_id, PROFILE)).fetchone()
            if not row:
                raise ValueError("That active meal was not found.")
            con.execute("UPDATE entries SET status='removed', updated_at=? WHERE id=?", (stamp, entry_id))
        return self._entry(row)

    def restore_meal(self, entry_id: str) -> dict:
        with self.db() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM entries WHERE id=? AND profile_id=? AND kind='meal' AND status='removed'",
                              (entry_id, PROFILE)).fetchone()
            if not row:
                raise ValueError("That removed meal was not found.")
            for other in con.execute("SELECT payload_json FROM entries WHERE profile_id=? AND status='active' AND kind='meal'",
                                     (PROFILE,)):
                if json.loads(other["payload_json"]).get("replaces_id") == entry_id:
                    raise ValueError("Undo the replacement before restoring this meal.")
            con.execute("UPDATE entries SET status='active', updated_at=? WHERE id=?",
                        (self.timestamp(), entry_id))
        return self._entry(row)

    def replace_meal(self, entry_id: str, result_id: str) -> dict:
        result = self.result(result_id)
        if not result or result.get("kind") != "result" or not result.get("cards"):
            raise ValueError("The replacement nutrition result is unavailable.")
        for card in result["cards"]:
            if card.get("portion_confirmed") is False:
                raise ValueError("Please confirm the reported portion before logging this result.")
            if "portion_confirmed" not in card:
                # Old saved results may have assumed a scoop was a serving. Revalidate them.
                factor, _ = portion_factor({"quantity": card.get("quantity", 1), "unit": card.get("unit", "unspecified")}, card)
                if factor is None or factor != card.get("quantity"):
                    raise ValueError("This older result needs a portion check. Ask Mellow to verify it again.")
        values = numbers(result.get("total"), complete=True, nonnegative=True)
        stamp = self.timestamp()
        with self.db() as con:
            con.execute("BEGIN IMMEDIATE")
            old = con.execute("SELECT * FROM entries WHERE id=? AND profile_id=? AND kind='meal' AND status='active'",
                              (entry_id, PROFILE)).fetchone()
            if not old:
                raise ValueError("That active meal was not found.")
            if any(card.get("menu_date") != old["log_date"] for card in result["cards"]):
                raise ValueError("The replacement menu date differs from the logged meal date.")
            previous = self._totals(con, old["log_date"])["protein_g"]
            con.execute("UPDATE entries SET status='removed', updated_at=? WHERE id=?", (stamp, entry_id))
            payload = {"label": result.get("label") or json.loads(old["payload_json"]).get("label", "Meal"),
                       "cards": result["cards"], "delta": values,
                       "fingerprint": result["fingerprint"], "replaces_id": entry_id}
            new_id = uuid.uuid4().hex
            con.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (new_id, PROFILE, old["log_date"], "meal", "active", json.dumps(payload),
                         result_id, old["conversation_id"], stamp, stamp))
            event = self._protein_crossing(con, old["log_date"], previous)
        self._emit_celebration(event)
        return {"id": new_id, "date": old["log_date"], "kind": "meal", "status": "active",
                "created_at": stamp, **payload}

    def import_legacy(self, migration_key: str, state: dict, goals: dict) -> dict:
        if not isinstance(migration_key, str) or not 1 <= len(migration_key) <= 120:
            raise ValueError("Invalid migration key.")
        if not isinstance(state, dict) or not isinstance(goals, dict):
            raise ValueError("Invalid browser tracker data.")
        day = state.get("date")
        totals = numbers(state.get("totals"), complete=True)
        if day != self.today():
            totals = ZERO.copy()  # Browser's previous day never becomes today's total.
        cleaned_goals = {key: None if goals.get(key) is None else
                         numbers({key: goals[key]}, nonnegative=True)[key] for key in NUTRIENTS}
        imported_totals = False
        conflict = False
        stamp = self.timestamp()
        with self.db() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM migrations WHERE profile_id=? AND migration_key=?",
                           (PROFILE, migration_key)).fetchone():
                return {"already_imported": True, "tracker": self.tracker()}
            has_entries = con.execute("SELECT 1 FROM entries WHERE profile_id=? AND log_date=? LIMIT 1",
                                      (PROFILE, self.today())).fetchone()
            if not has_entries and any(totals.values()):
                con.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (uuid.uuid4().hex, PROFILE, self.today(), "adjustment", "active",
                             json.dumps({"label": "Imported browser totals", "delta": totals,
                                         "mode": "import"}), None, None, stamp, stamp))
                imported_totals = True
            elif has_entries and totals != self._totals(con, self.today()):
                conflict = True
            if not con.execute("SELECT 1 FROM goals WHERE profile_id=?", (PROFILE,)).fetchone():
                con.execute("INSERT INTO goals VALUES (?,?)", (PROFILE, json.dumps(cleaned_goals)))
            con.execute("INSERT INTO migrations VALUES (?,?,?)", (PROFILE, migration_key, stamp))
        return {"already_imported": False, "imported_totals": imported_totals,
                "conflict": conflict, "tracker": self.tracker()}

    def memories(self) -> list[dict]:
        with self.db() as con:
            rows = con.execute("SELECT * FROM memories WHERE profile_id=? ORDER BY category, key",
                               (PROFILE,)).fetchall()
        return [dict(row) for row in rows]

    def save_memory(self, category: str, key: str, value: str) -> dict:
        key, value = key.strip(), value.strip()
        if category not in MEMORY_TYPES or not 1 <= len(key) <= 100 or not 1 <= len(value) <= 500:
            raise ValueError("Choose a memory type, name, and value.")
        memory_id = uuid.uuid4().hex
        with self.db() as con:
            old = con.execute("SELECT id FROM memories WHERE profile_id=? AND category=? AND key=?",
                              (PROFILE, category, key)).fetchone()
            if old:
                memory_id = old["id"]
            con.execute("INSERT INTO memories VALUES (?,?,?,?,?,?) ON CONFLICT(profile_id, category, key) "
                        "DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                        (memory_id, PROFILE, category, key, value, self.timestamp()))
        return {"id": memory_id, "category": category, "key": key, "value": value}

    def delete_memory(self, memory_id: str) -> bool:
        with self.db() as con:
            result = con.execute("DELETE FROM memories WHERE id=? AND profile_id=?", (memory_id, PROFILE))
        return result.rowcount > 0

    def update_memory(self, memory_id: str, category: str, key: str, value: str) -> dict:
        key, value = key.strip(), value.strip()
        if category not in MEMORY_TYPES or not 1 <= len(key) <= 100 or not 1 <= len(value) <= 500:
            raise ValueError("Choose a memory type, name, and value.")
        with self.db() as con:
            result = con.execute("UPDATE memories SET category=?, key=?, value=?, updated_at=? "
                                 "WHERE id=? AND profile_id=?",
                                 (category, key, value, self.timestamp(), memory_id, PROFILE))
        if not result.rowcount:
            raise ValueError("That saved memory was not found.")
        return {"id": memory_id, "category": category, "key": key, "value": value}
