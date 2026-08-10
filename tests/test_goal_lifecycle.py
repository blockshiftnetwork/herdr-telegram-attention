#!/usr/bin/env python3
"""Regression checks for managed-goal correlation and closure fallbacks."""
import importlib.util
import os
import pathlib
import tempfile
import types
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "bin" / "herdr_telegram_attention.py"
SPEC = importlib.util.spec_from_file_location("telegram_attention", MODULE_PATH)
plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plugin)


class GoalLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        plugin.STATE_DIR = pathlib.Path(self.tmp.name)
        plugin.STATE = plugin.STATE_DIR / "incidents.json"
        plugin.CONFIG = pathlib.Path(self.tmp.name) / "missing.env"
        self.calls = []
        self.original_api = plugin.api
        self.original_run = plugin.subprocess.run
        plugin.api = self.fake_api
        plugin.subprocess.run = lambda *args, **kwargs: types.SimpleNamespace(returncode=0)
        self.config = {"TELEGRAM_CHAT_ID": "42", "TELEGRAM_LANGUAGE": "es", "TELEGRAM_AUTO_REGISTER_GOALS": "true", "TELEGRAM_GOAL_REPORT_TIMEOUT_SECONDS": "30"}
        self.old_pane = os.environ.get("HERDR_PANE_ID")

    def tearDown(self):
        plugin.api = self.original_api
        plugin.subprocess.run = self.original_run
        if self.old_pane is None:
            os.environ.pop("HERDR_PANE_ID", None)
        else:
            os.environ["HERDR_PANE_ID"] = self.old_pane
        self.tmp.cleanup()

    def fake_api(self, _config, method, data):
        self.calls.append((method, data))
        return {"message_id": 88} if method == "sendMessage" else True

    def event(self, state):
        return {"state": state, "agent": "codex", "workspace": "w1", "tab": "w1:t1", "pane": "w1:p2", "reason": "", "seq": "1", "project": "api", "cwd": "", "task": "Fix correlation"}

    def test_goal_has_stable_identity_and_late_report_updates_same_message(self):
        plugin.handle_event(self.config, self.event("working"))
        plugin.handle_event(self.config, self.event("done"))
        goal = plugin.load_state()["goals"]["w1:p2"]
        self.assertEqual(goal["phase"], "awaiting_report")
        self.assertTrue(goal["id"])
        pending = self.calls[0][1]["text"]
        self.assertIn(f"Goal: {goal['id']}", pending)
        self.assertIn("Workspace: w1", pending)
        self.assertIn("Pestaña: w1:t1", pending)
        self.assertIn("Panel: w1:p2", pending)

        goal["phase"] = "evidence_pending"
        plugin.save_state({"offset": 0, "incidents": {}, "goals": {"w1:p2": goal}})
        os.environ["HERDR_PANE_ID"] = "w1:p2"
        plugin.goal_report(self.config, ["--goal-id", goal["id"], "--outcome", "completed", "--summary", "done", "--evidence", "tests"])
        self.assertEqual(plugin.load_state()["goals"]["w1:p2"]["phase"], "reported")
        self.assertEqual(self.calls[-1][0], "editMessageText")

    def test_timeout_marks_evidence_pending_without_losing_goal_identity(self):
        goal = {"id": "goal-123", "pane": "w1:p2", "agent": "codex", "workspace": "w1", "tab": "w1:t1", "project": "api", "task": "Fix", "phase": "awaiting_report", "requested": 0, "message_id": 88, "timeout_eligible": True}
        state = {"offset": 0, "incidents": {}, "goals": {"w1:p2": goal}}
        self.assertTrue(plugin.reconcile_goal_timeouts(self.config, state, 30))
        self.assertEqual(goal["phase"], "evidence_pending")
        self.assertEqual(self.calls[-1][0], "editMessageText")
        self.assertIn("Goal: goal-123", self.calls[-1][1]["text"])
        self.assertIn("evidencia pendiente", self.calls[-1][1]["text"])

    def test_legacy_goal_does_not_emit_a_new_timeout_alert_on_upgrade(self):
        goal = {"id": "legacy-123", "pane": "w1:p2", "agent": "codex", "project": "api", "phase": "awaiting_report", "requested": 0, "message_id": 88, "timeout_eligible": False}
        state = {"offset": 0, "incidents": {}, "goals": {"w1:p2": goal}}
        self.assertFalse(plugin.reconcile_goal_timeouts(self.config, state, 30))
        self.assertEqual(goal["phase"], "awaiting_report")
        self.assertEqual(self.calls, [])

    def test_wrong_pane_cannot_report_another_goal(self):
        state = {"offset": 0, "incidents": {}, "goals": {"w1:p2": {"id": "goal-123", "pane": "w1:p2", "agent": "codex", "project": "api", "phase": "awaiting_report"}}}
        plugin.save_state(state)
        os.environ["HERDR_PANE_ID"] = "w1:p9"
        with self.assertRaisesRegex(RuntimeError, "awaiting a report"):
            plugin.goal_report(self.config, ["--goal-id", "goal-123", "--outcome", "completed", "--summary", "done", "--evidence", "tests"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
