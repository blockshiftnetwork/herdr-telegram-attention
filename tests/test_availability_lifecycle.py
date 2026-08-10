#!/usr/bin/env python3
"""Regression checks for the non-invasive availability queue."""
import importlib.util
import os
import pathlib
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "bin" / "herdr_telegram_attention.py"
SPEC = importlib.util.spec_from_file_location("telegram_attention", MODULE_PATH)
plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plugin)


class AvailabilityLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        plugin.STATE_DIR = pathlib.Path(self.tmp.name)
        plugin.STATE = plugin.STATE_DIR / "incidents.json"
        plugin.CONFIG = pathlib.Path(self.tmp.name) / "missing.env"
        self.calls = []
        self.original_api = plugin.api
        plugin.api = self.fake_api
        self.config = {"TELEGRAM_CHAT_ID": "42", "TELEGRAM_LANGUAGE": "es", "TELEGRAM_NOTIFY_AVAILABLE": "true"}

    def tearDown(self):
        plugin.api = self.original_api
        self.tmp.cleanup()

    def fake_api(self, _config, method, data):
        self.calls.append((method, data))
        return {"message_id": 88} if method == "sendMessage" else True

    def event(self, state, pane="w1:p2", seq="1"):
        return {"state": state, "agent": "codex", "workspace": "w1", "tab": "w1:t1", "pane": pane, "reason": "", "seq": seq, "project": "api", "cwd": "", "task": "Review API"}

    def test_done_creates_a_reassignable_queue_without_prompting_agent(self):
        plugin.handle_event(self.config, self.event("done"))
        state = plugin.load_state()
        available = next(iter(state["availability"].values()))
        self.assertEqual(available["status"], "available")
        self.assertIn("w1:p2", available["agents"])
        self.assertEqual(self.calls[0][0], "sendMessage")
        self.assertIn("Agentes disponibles", self.calls[0][1]["text"])
        self.assertIn("Panel: w1:p2", self.calls[0][1]["text"])
        self.assertNotIn("agent\",\"prompt", MODULE_PATH.read_text())

    def test_duplicate_done_sequence_does_not_send_another_notification(self):
        plugin.handle_event(self.config, self.event("done", seq="7"))
        plugin.handle_event(self.config, self.event("done", seq="7"))
        self.assertEqual([method for method, _ in self.calls].count("sendMessage"), 1)

    def test_working_removes_agent_from_available_queue(self):
        plugin.handle_event(self.config, self.event("done"))
        plugin.handle_event(self.config, self.event("working", seq="2"))
        available = next(iter(plugin.load_state()["availability"].values()))
        self.assertEqual(available["status"], "reviewed")
        self.assertEqual(available["agents"], {})
        self.assertEqual(self.calls[-1][0], "editMessageText")
        self.assertIn("Disponibles revisados", self.calls[-1][1]["text"])

    def test_blocked_agent_leaves_queue_and_creates_attention_incident(self):
        plugin.handle_event(self.config, self.event("done"))
        blocked = self.event("blocked", seq="2")
        blocked["reason"] = "Need approval"
        plugin.handle_event(self.config, blocked)
        state = plugin.load_state()
        self.assertEqual(next(iter(state["availability"].values()))["agents"], {})
        self.assertEqual(len(state["incidents"]), 1)
        self.assertIn("requiere tu atención", self.calls[-1][1]["text"])

    def test_review_button_archives_queue_without_touching_agent(self):
        plugin.handle_event(self.config, self.event("done"))
        available = next(iter(plugin.load_state()["availability"].values()))
        callback = {"id": "cb1", "data": f"hta:v:{available['id']}:clear", "message": {"chat": {"id": "42"}}}
        plugin.callback(self.config, callback)
        archived = plugin.load_state()["availability"][available["id"]]
        self.assertEqual(archived["status"], "reviewed")
        self.assertEqual(archived["agents"], {})
        self.assertEqual(self.calls[-1][0], "answerCallbackQuery")

    def test_other_telegram_user_cannot_archive_queue(self):
        plugin.handle_event(self.config, self.event("done"))
        available = next(iter(plugin.load_state()["availability"].values()))
        secured = {**self.config, "TELEGRAM_ALLOWED_USER_ID": "99"}
        callback = {"id": "cb2", "data": f"hta:v:{available['id']}:clear", "from": {"id": "100"}, "message": {"chat": {"id": "42"}}}
        plugin.callback(secured, callback)
        self.assertEqual(plugin.load_state()["availability"][available["id"]]["status"], "available")
        self.assertEqual(self.calls[-1][0], "answerCallbackQuery")
        self.assertEqual(self.calls[-1][1]["text"], "Unauthorized")

    def test_direct_process_uses_standard_state_fallback(self):
        previous_state_dir = os.environ.pop("HERDR_PLUGIN_STATE_DIR", None)
        previous_xdg_state_home = os.environ.get("XDG_STATE_HOME")
        try:
            os.environ["XDG_STATE_HOME"] = self.tmp.name
            spec = importlib.util.spec_from_file_location("telegram_attention_fallback", MODULE_PATH)
            fallback = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(fallback)
            self.assertEqual(fallback.STATE, pathlib.Path(self.tmp.name) / "herdr" / "plugins" / fallback.PLUGIN_ID / "incidents.json")
        finally:
            if previous_state_dir is not None:
                os.environ["HERDR_PLUGIN_STATE_DIR"] = previous_state_dir
            if previous_xdg_state_home is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = previous_xdg_state_home


if __name__ == "__main__":
    unittest.main(verbosity=2)
