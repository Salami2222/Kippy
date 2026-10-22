"""Persistent message events and narrowly scoped, idempotent reaction approvals."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid

from .memory import PROFILE


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Invalid conversation or message identifier.")
    return value


class Conversations:
    def __init__(self, app):
        self.app = app
        self.store = app.memory
        self.lock = threading.RLock()
        with self.store.db() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chat_history ON chat_messages(profile_id, conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS chat_turns (
                    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
                    response_json TEXT
                );
                CREATE TABLE IF NOT EXISTS message_reactions (
                    message_id TEXT NOT NULL, profile_id TEXT NOT NULL, actor TEXT NOT NULL,
                    active INTEGER NOT NULL, processed INTEGER NOT NULL DEFAULT 0,
                    response_json TEXT,
                    PRIMARY KEY(message_id, profile_id, actor)
                );
            """)

    def history(self, conversation_id):
        identifier(conversation_id)
        with self.store.db() as con:
            rows = con.execute("SELECT * FROM chat_messages WHERE profile_id=? AND conversation_id=? ORDER BY rowid",
                               (PROFILE, conversation_id)).fetchall()
            reactions = con.execute("SELECT r.* FROM message_reactions r JOIN chat_messages m ON m.id=r.message_id "
                                    "WHERE m.profile_id=? AND m.conversation_id=?", (PROFILE, conversation_id)).fetchall()
        return {"conversation_id": conversation_id, "messages": [
            {"id": row["id"], "role": row["role"], "payload": json.loads(row["payload_json"]),
             "reactions": {r["actor"]: bool(r["active"]) for r in reactions if r["message_id"] == row["id"]},
             "reaction_processed": {r["actor"]: bool(r["processed"]) for r in reactions if r["message_id"] == row["id"]}}
            for row in rows]}

    def _save_message(self, con, message_id, conversation_id, role, payload):
        con.execute("INSERT INTO chat_messages VALUES (?,?,?,?,?,?)",
                    (message_id, PROFILE, conversation_id, role, json.dumps(payload), self.store.timestamp()))

    def _context(self, conversation_id, event):
        session = self.store.conversation(conversation_id)
        session["recent"] = (session.get("recent", []) + [event])[-6:]
        self.store.save_conversation(conversation_id, session)

    def turn(self, message, conversation_id=None, request_id=None, reply_to=None):
        from .web import ChatError
        conversation_id = identifier(conversation_id) if conversation_id else uuid.uuid4().hex
        request_id = identifier(request_id) if request_id else uuid.uuid4().hex
        with self.lock:
            with self.store.db() as con:
                con.execute("BEGIN IMMEDIATE")
                old = con.execute("SELECT * FROM chat_turns WHERE id=? AND profile_id=?", (request_id, PROFILE)).fetchone()
                if old:
                    if old["conversation_id"] != conversation_id:
                        raise ValueError("This request belongs to a different conversation.")
                    if old["response_json"]:
                        return json.loads(old["response_json"])
                    raise ValueError("This request is processing or was interrupted. Check your tracker before sending a new request.")
                reference = None
                if reply_to is not None:
                    identifier(reply_to)
                    target = con.execute("SELECT * FROM chat_messages WHERE id=? AND conversation_id=? AND profile_id=?",
                                         (reply_to, conversation_id, PROFILE)).fetchone()
                    if not target:
                        raise ValueError("The reply target was not found in this conversation.")
                    latest = con.execute("SELECT id FROM chat_messages WHERE conversation_id=? AND profile_id=? ORDER BY rowid DESC LIMIT 1",
                                         (conversation_id, PROFILE)).fetchone()
                    payload = json.loads(target["payload_json"])
                    reference = {"message_id": reply_to, "role": target["role"], "text": payload.get("message", "")[:600],
                                 "is_current": latest["id"] == reply_to}
                    if payload.get("cards"):
                        reference["foods"] = [{key: card.get(key) for key in ("name", "quantity", "serving_size", "dining_hall", "menu_date")}
                                              for card in payload["cards"][:12]]
                con.execute("INSERT INTO chat_turns VALUES (?,?,?,NULL)", (request_id, PROFILE, conversation_id))
                self._save_message(con, request_id, conversation_id, "user",
                                   {"message": message, **({"reply_reference": reference} if reference else {})})
            try:
                if reference:
                    self._context(conversation_id, {"role": "user", "event": "reply_reference", "reply_reference": reference})
                    response = self.app.chat(message, conversation_id, reply_context=reference)
                else:
                    response = self.app.chat(message, conversation_id)
            except ChatError as exc:
                response = {"kind": "error", "message": str(exc), "debug": exc.debug, "cards": [], "total": None}
                self._context(conversation_id, {"role": "user", "text": message[:250]})
                self._context(conversation_id, {"role": "assistant", "text": str(exc)[:250]})
            response["conversation_id"] = conversation_id
            response["message_id"] = uuid.uuid4().hex
            response["reply_to"] = request_id
            response.setdefault("response_type", "clarification" if response["kind"] in {"clarification", "needs_data"}
                                else "action" if response["kind"] in {"adjusted", "meal_logged", "already_logged", "meal_edited", "meal_removed", "undone", "memory_saved", "memory_deleted"} else "message")
            response["acknowledged"] = response["kind"] not in {"error", "clarification", "needs_data"}
            response.setdefault("debug", {})["response_type"] = response["response_type"]
            # Only deterministic, complete, same-day nutrition results can become reaction offers.
            if response["kind"] == "result":
                session = self.store.conversation(conversation_id)
                day = self.store.today()
                if self.app._safe_to_log(response, session["foods"], day) is None:
                    response["approval"] = {"operation": "log", "result_id": response["result_id"], "date": day}
                    response["message"] += f" You can add this meal for {day}; a thumbs-up will ask you to confirm first."
            with self.store.db() as con:
                self._save_message(con, response["message_id"], conversation_id, "assistant", response)
                if response["acknowledged"]:
                    con.execute("INSERT INTO message_reactions VALUES (?,?, 'mellow',1,1,NULL)", (request_id, PROFILE))
                con.execute("UPDATE chat_turns SET response_json=? WHERE id=? AND profile_id=?",
                            (json.dumps(response), request_id, PROFILE))
            # Preserve the exact offer and message IDs in the bounded model context.
            self._context(conversation_id, {"role": "assistant", "message_id": response["message_id"],
                                           "text": response["message"][:500], "approval": response.get("approval")})
            return response

    def feedback(self, conversation_id, message_id, active):
        """User-authored message feedback cannot authorize nutrition operations."""
        identifier(conversation_id)
        identifier(message_id)
        if not isinstance(active, bool):
            raise ValueError("Feedback active must be true or false.")
        with self.store.db() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT id FROM chat_messages WHERE id=? AND conversation_id=? AND profile_id=? AND role='user'",
                              (message_id, conversation_id, PROFILE)).fetchone()
            if not row:
                raise ValueError("That user message was not found in this conversation.")
            con.execute("INSERT INTO message_reactions VALUES (?,?,'user_feedback',?,1,NULL) "
                        "ON CONFLICT(message_id,profile_id,actor) DO UPDATE SET active=excluded.active",
                        (message_id, PROFILE, int(active)))
        return {"active": active}

    def react(self, conversation_id, message_id, active, confirm_approval=False):
        identifier(conversation_id)
        identifier(message_id)
        if not isinstance(active, bool):
            raise ValueError("Reaction active must be true or false.")
        if not isinstance(confirm_approval, bool):
            raise ValueError("Approval confirmation must be true or false.")
        started = time.perf_counter()
        with self.lock:
            with self.store.db() as con:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT * FROM chat_messages WHERE id=? AND conversation_id=? AND profile_id=? AND role='assistant'",
                                  (message_id, conversation_id, PROFILE)).fetchone()
                if not row:
                    raise ValueError("That Mellow message was not found in this conversation.")
                target = json.loads(row["payload_json"])
                existing = con.execute("SELECT * FROM message_reactions WHERE message_id=? AND profile_id=? AND actor='user'",
                                       (message_id, PROFILE)).fetchone()
                if active and target.get("approval") and not (existing and existing["processed"]) and not confirm_approval:
                    raise ValueError("Confirm logging this specific meal and date before approving it. No meal or reaction was changed.")
                con.execute("INSERT INTO message_reactions VALUES (?,?,'user',?,0,NULL) "
                            "ON CONFLICT(message_id,profile_id,actor) DO UPDATE SET active=excluded.active",
                            (message_id, PROFILE, int(active)))
                process = active and not (existing and existing["processed"])
                if process:
                    # Claim before executing: crashes/retries must never repeat an approval.
                    con.execute("UPDATE message_reactions SET processed=1 WHERE message_id=? AND profile_id=? AND actor='user'",
                                (message_id, PROFILE))
                latest = con.execute("SELECT id FROM chat_messages WHERE conversation_id=? AND profile_id=? AND role='assistant' ORDER BY rowid DESC LIMIT 1",
                                     (conversation_id, PROFILE)).fetchone()
            if not existing or bool(existing["active"]) != active:
                self._context(conversation_id, {"role": "user", "event": "reaction", "message_id": message_id,
                                               "emoji": "👍", "active": active, "text": target["message"][:250]})
            if not process:
                if active and existing and existing["processed"] and not existing["response_json"]:
                    raise ValueError("The earlier approval is processing or was interrupted. Check your tracker; it will not be executed again automatically.")
                return {"active": active, "processed": bool(existing and existing["processed"]),
                        "tracker": self.store.tracker(), "message": "Reaction saved. Removing a reaction does not undo a logged meal."}
            response = {"kind": "acknowledgment", "response_type": "acknowledgment", "message": "",
                        "acknowledged": True, "cards": [], "total": None}
            approval = target.get("approval")
            if approval:
                try:
                    if latest["id"] != message_id or approval["date"] != self.store.today():
                        raise ValueError("That meal offer is no longer current. Ask me to verify the meal and date again before logging it.")
                    if approval["operation"] != "log":
                        raise ValueError("Please confirm this action in words; a reaction cannot authorize it.")
                    meal, added = self.store.log_result(approval["result_id"], approval["date"], conversation_id)
                    response.update(kind="meal_logged" if added else "already_logged", response_type="action",
                                    message=f"Logged {meal['label']} for {approval['date']}." if added else "That meal was already logged; I did not add it twice.",
                                    tracker=self.store.tracker())
                    session = self.store.conversation(conversation_id)
                    if added:
                        session["working_logged_id"] = meal["id"]
                        session["last_logged_id"] = meal["id"]
                        session["last_action"] = {"type": "log", "entry_id": meal["id"]}
                        self.store.save_conversation(conversation_id, session)
                    response["debug"] = {"tool_action": {"operation": "log", "entry_id": meal["id"], "added": added}}
                except ValueError as exc:
                    response.update(kind="error", response_type="message", acknowledged=False, message=str(exc))
            response.update(conversation_id=conversation_id, message_id=uuid.uuid4().hex, reply_to=message_id)
            response.setdefault("debug", {}).update(user_request="👍", reaction_target=message_id,
                                                    response_type=response["response_type"], ai_response_time_ms=None,
                                                    error={"stage": "reaction approval", "message": response["message"]} if response["kind"] == "error" else None,
                                                    response_time_ms=round((time.perf_counter() - started) * 1000))
            with self.store.db() as con:
                self._save_message(con, response["message_id"], conversation_id, "assistant", response)
                con.execute("UPDATE message_reactions SET response_json=? WHERE message_id=? AND profile_id=? AND actor='user'",
                            (json.dumps(response), message_id, PROFILE))
            self._context(conversation_id, {"role": "assistant", "text": response["message"] or "Acknowledged the referenced reaction; no action requested."})
            return {"active": active, "processed": True, "response": response, "tracker": self.store.tracker()}
