#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

PLUGIN = pathlib.Path(__file__).resolve().parent.parent
COLLECTOR = PLUGIN / "helpers" / "omarchy-agent-usage-opencode-go"


def load_collector():
    loader = importlib.machinery.SourceFileLoader("omarchy_go", str(COLLECTOR))
    spec = importlib.util.spec_from_loader("omarchy_go", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class KeyResolutionTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_collector()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._saved = {k: os.environ.get(k) for k in
                       ("OPENCODE_GO_API_KEY", "OPENCODE_API_KEY", "XDG_DATA_HOME")}
        for k in self._saved:
            os.environ.pop(k, None)
        os.environ["XDG_DATA_HOME"] = self.tmp.name

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def write_auth(self, obj_text):
        d = pathlib.Path(self.tmp.name) / "opencode"
        d.mkdir(parents=True, exist_ok=True)
        (d / "auth.json").write_text(obj_text)

    def test_go_env_wins_and_is_stripped(self):
        os.environ["OPENCODE_GO_API_KEY"] = "  sk-go  "
        os.environ["OPENCODE_API_KEY"] = "sk-other"
        self.assertEqual(self.mod.resolve_key(), "sk-go")

    def test_plain_env_falls_through_when_go_empty(self):
        os.environ["OPENCODE_GO_API_KEY"] = "   "
        os.environ["OPENCODE_API_KEY"] = "sk-other"
        self.assertEqual(self.mod.resolve_key(), "sk-other")

    def test_auth_json_used_when_no_env(self):
        self.write_auth(json.dumps({"opencode-go": {"key": "sk-auth"}}))
        self.assertEqual(self.mod.resolve_key(), "sk-auth")

    def test_auth_json_empty_key_is_no_key(self):
        self.write_auth(json.dumps({"opencode-go": {"key": "  "}}))
        self.assertEqual(self.mod.resolve_key(), "")

    def test_auth_json_bad_shape_is_no_key(self):
        self.write_auth("{ not json")
        self.assertEqual(self.mod.resolve_key(), "")
        self.write_auth(json.dumps({"opencode-go": {"key": 5}}))
        self.assertEqual(self.mod.resolve_key(), "")


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_collector()

    def test_record_key_set_and_no_key_state(self):
        rec = self.mod.build_record(
            {"limits": [], "state": "Waiting for auth", "retry": False}, None)
        self.assertEqual(rec["id"], "opencode-go")
        self.assertEqual(rec["name"], "OpenCode Go")
        self.assertEqual(rec["tierLabel"], "Go")
        self.assertEqual(rec["schemaVersion"], 1)
        self.assertEqual(rec["usageStatusText"], "Waiting for auth")
        self.assertFalse(rec["ready"])
        self.assertFalse(rec["hasLocalStats"])
        for key in ("todayPrompts", "todaySessions", "todayTotalTokens",
                    "totalPrompts", "totalSessions", "activeDays"):
            self.assertEqual(rec[key], 0)
        for key in ("todayTokensByModel", "modelUsage"):
            self.assertEqual(rec[key], {})
        for key in ("recentDays", "activeDates", "limits"):
            self.assertEqual(rec[key], [])
        self.assertNotIn("retryAdvised", rec)

    def test_ready_when_limits_present(self):
        rec = self.mod.build_record(
            {"limits": [{"label": "Session (5-hour)", "percent": 0.5, "resetsAt": ""}],
             "state": "", "retry": False}, None)
        self.assertTrue(rec["ready"])

    def test_retry_advised_only_on_retry(self):
        rec = self.mod.build_record({"limits": [], "state": "", "retry": True}, None)
        self.assertTrue(rec["retryAdvised"])


if __name__ == "__main__":
    unittest.main()
