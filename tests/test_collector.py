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


import http.server
import threading


class StubServer:
    """One-shot loopback HTTP server with a configurable response."""

    def __init__(self, status=200, body=b"{}", headers=None, chunks=None):
        self.status = status
        self.body = body
        self.headers = headers or {"Content-Type": "application/json"}
        self.chunks = chunks  # list[bytes] streamed instead of body
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append(self.path)
                self.send_response(outer.status)
                for k, v in outer.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                try:
                    if outer.chunks is not None:
                        for chunk in outer.chunks:
                            self.wfile.write(chunk)
                    else:
                        self.wfile.write(outer.body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/usage"
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def ok_body(rolling=6, weekly=23, monthly=34, status="ok"):
    return json.dumps({"usage": {
        "rolling": {"status": status, "percent": rolling, "resetsAt": "2026-09-10T14:14:24.749Z"},
        "weekly": {"status": status, "percent": weekly, "resetsAt": "2026-09-14T00:00:00.749Z"},
        "monthly": {"status": status, "percent": monthly, "resetsAt": "2026-09-21T11:07:36.749Z"}},
    }).encode()


class LimitsHttpTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_collector()
        self._saved = os.environ.get("OPENCODE_GO_USAGE_URL")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            os.environ.pop("OPENCODE_GO_USAGE_URL", None)
        else:
            os.environ["OPENCODE_GO_USAGE_URL"] = self._saved

    def test_maps_windows_to_fraction_limits(self):
        with StubServer(200, ok_body()) as srv:
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            result = self.mod.probe_limits("sk-x")
        self.assertTrue(result["ok"])
        self.assertEqual([w["label"] for w in result["limits"]],
                         ["Session (5-hour)", "Weekly (7-day)", "Monthly (30-day)"])
        self.assertEqual([round(w["percent"], 4) for w in result["limits"]],
                         [0.06, 0.23, 0.34])
        self.assertEqual(result["limits"][0]["resetsAt"], "2026-09-10T14:14:24.749Z")

    def test_percent_validation_omits_bad_windows(self):
        body = json.dumps({"usage": {
            "rolling": {"status": "ok", "percent": -1, "resetsAt": ""},
            "weekly": {"status": "ok", "percent": 150, "resetsAt": ""},
            "monthly": {"status": "ok", "percent": "34", "resetsAt": ""}}}).encode()
        with StubServer(200, body) as srv:
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            result = self.mod.probe_limits("sk-x")
        self.assertFalse(result["ok"])
        self.assertTrue(result["transport"])

    def test_zero_window_payload_is_transport_failure(self):
        with StubServer(200, json.dumps({"usage": {}}).encode()) as srv:
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            result = self.mod.probe_limits("sk-x")
        self.assertTrue(result["transport"])
        self.assertEqual(result["limits"], [])

    def test_401_and_403_states(self):
        for status, state_fragment in ((401, "expired"), (403, "subscription")):
            with StubServer(status, b"{}") as srv:
                os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
                result = self.mod.probe_limits("sk-x")
            self.assertFalse(result["transport"])
            self.assertIn(state_fragment, result["state"].lower())

    def test_500_is_transport_failure(self):
        with StubServer(500, b"{}") as srv:
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            result = self.mod.probe_limits("sk-x")
        self.assertTrue(result["transport"])

    def test_oversized_body_rejected(self):
        big = b"x" * (65536 + 100)
        with StubServer(200, big) as srv:
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            result = self.mod.probe_limits("sk-x")
        self.assertTrue(result["transport"])

    def test_malformed_json_is_transport_failure(self):
        with StubServer(200, b"{ not json") as srv:
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            result = self.mod.probe_limits("sk-x")
        self.assertTrue(result["transport"])

    def test_connection_refused_is_transport_failure(self):
        os.environ["OPENCODE_GO_USAGE_URL"] = "http://127.0.0.1:9/usage"
        result = self.mod.probe_limits("sk-x")
        self.assertTrue(result["transport"])

    def test_non_loopback_override_ignored(self):
        with StubServer(200, ok_body()) as srv:
            self.mod.REAL_ENDPOINT = srv.url  # the "real" endpoint is our stub
            os.environ["OPENCODE_GO_USAGE_URL"] = "http://example.com/usage"
            result = self.mod.probe_limits("sk-x")
        self.assertTrue(result["ok"])
        self.assertEqual(srv.requests, ["/usage"])

    def test_override_rejects_query_fragment_credentials(self):
        for bad in ("http://127.0.0.1:1/usage?x=1", "http://127.0.0.1:1/usage#f",
                    "http://u:p@127.0.0.1:1/usage", "http://[::ffff:127.0.0.1]:1/usage"):
            os.environ["OPENCODE_GO_USAGE_URL"] = bad
            self.assertEqual(self.mod.endpoint_url(), self.mod.REAL_ENDPOINT)


if __name__ == "__main__":
    unittest.main()
