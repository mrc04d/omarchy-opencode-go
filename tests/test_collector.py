#!/usr/bin/env python3
import datetime as dt
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
import time


class StubServer:
    """One-shot loopback HTTP server with a configurable response."""

    def __init__(self, status=200, body=b"{}", headers=None, chunks=None, delay=0.0):
        self.status = status
        self.body = body
        self.headers = headers or {"Content-Type": "application/json"}
        self.chunks = chunks  # list[bytes] streamed instead of body
        self.delay = delay  # seconds to stall after headers before the body
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append(self.path)
                self.send_response(outer.status)
                for k, v in outer.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                if outer.delay:
                    time.sleep(outer.delay)
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
            "monthly": {"status": "ok", "percent": 34, "resetsAt": ""}}}).encode()
        with StubServer(200, body) as srv:
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            result = self.mod.probe_limits("sk-x")
        self.assertTrue(result["ok"])
        self.assertFalse(result["transport"])
        self.assertEqual([w["label"] for w in result["limits"]], ["Monthly (30-day)"])
        self.assertEqual(round(result["limits"][0]["percent"], 4), 0.34)

    def test_all_invalid_percents_is_transport_failure(self):
        body = json.dumps({"usage": {
            "rolling": {"status": "ok", "percent": -1, "resetsAt": ""},
            "weekly": {"status": "ok", "percent": "34", "resetsAt": ""},
            "monthly": {"status": "ok", "percent": 101, "resetsAt": ""}}}).encode()
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
        body = json.dumps({"usage": {
            "rolling": {"status": "ok", "percent": 6, "resetsAt": ""},
            "weekly": {"status": "ok", "percent": 23, "resetsAt": ""},
            "monthly": {"status": "ok", "percent": 34, "resetsAt": ""},
            "_pad": "x" * (65536 + 100)}}).encode()
        self.assertGreater(len(body), 65536)
        with StubServer(200, body) as srv:
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            result = self.mod.probe_limits("sk-x")
        self.assertTrue(result["transport"])

    def test_truncated_body_is_transport_failure(self):
        body = ok_body()
        truncated = body[:len(body) // 2]  # valid usage JSON cut short
        headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        with StubServer(200, truncated, headers=headers) as srv:
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            result = self.mod.probe_limits("sk-x")
        self.assertTrue(result["transport"])

    def test_malformed_chunked_body_is_transport_failure(self):
        headers = {"Content-Type": "application/json", "Transfer-Encoding": "chunked"}
        with StubServer(200, b"zz\r\n", headers=headers) as srv:
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            result = self.mod.probe_limits("sk-x")
        self.assertTrue(result["transport"])

    def test_body_stall_timeout_is_transport_failure(self):
        self.mod.TIMEOUT_SECONDS = 0.05
        with StubServer(200, b"{}", headers={"Content-Length": "10"}, delay=0.5) as srv:
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            result = self.mod.probe_limits("sk-x")
        self.assertTrue(result["transport"])

    def test_redirect_not_followed(self):
        with StubServer(302, b"") as srv:
            srv.headers["Location"] = srv.url
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            result = self.mod.probe_limits("sk-x")
        self.assertTrue(result["transport"])
        self.assertEqual(len(srv.requests), 1)

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


class LimitsCacheTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_collector()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._saved_cache = os.environ.get("XDG_CACHE_HOME")
        self._saved_url = os.environ.get("OPENCODE_GO_USAGE_URL")
        os.environ["XDG_CACHE_HOME"] = self.tmp.name
        self.cache = pathlib.Path(self.tmp.name) / "omarchy" / "agent-usage" / "opencode-go-limits.json"

    def tearDown(self):
        for name, saved in (("XDG_CACHE_HOME", self._saved_cache),
                            ("OPENCODE_GO_USAGE_URL", self._saved_url)):
            if saved is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved

    def seed(self, fetched_at_ms, limits):
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text(json.dumps({"fetchedAtMs": fetched_at_ms, "limits": limits}))

    def future_body(self):
        resets = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()
        return json.dumps({"usage": {
            "rolling": {"status": "ok", "percent": 6, "resetsAt": resets},
            "weekly": {"status": "ok", "percent": 23, "resetsAt": resets},
            "monthly": {"status": "ok", "percent": 34, "resetsAt": resets}}}).encode()

    def test_success_writes_cache_and_reuses_within_15s(self):
        with StubServer(200, self.future_body()) as srv:
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            first = self.mod.collect_limits("sk-x", False)
            self.assertTrue(first["limits"])
            self.assertTrue(self.cache.exists())
            before = len(srv.requests)
            second = self.mod.collect_limits("sk-x", False)  # cached; no new request
        self.assertEqual(len(srv.requests), before)
        self.assertTrue(second["limits"])

    def test_force_bypasses_reuse_and_failed_force_keeps_cache(self):
        self.seed(self.mod.now_ms(), [{"label": "Session (5-hour)", "percent": 0.1, "resetsAt": ""}])
        with StubServer(500, b"{}") as srv:
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            result = self.mod.collect_limits("sk-x", True)
        self.assertTrue(result["retry"])
        self.assertTrue(result["limits"])  # fallback retained
        cached = json.loads(self.cache.read_text())
        self.assertNotEqual(cached["limits"], [])

    def test_stale_fallback_over_24h_discarded(self):
        self.seed(self.mod.now_ms() - (25 * 3600 * 1000),
                  [{"label": "Session (5-hour)", "percent": 0.1, "resetsAt": ""}])
        with StubServer(500, b"{}") as srv:
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            result = self.mod.collect_limits("sk-x", True)
        self.assertEqual(result["limits"], [])
        self.assertTrue(result["retry"])

    def test_expired_resets_are_dropped(self):
        self.seed(self.mod.now_ms(),
                  [{"label": "Session (5-hour)", "percent": 0.1, "resetsAt": "2000-01-01T00:00:00Z"},
                   {"label": "Weekly (7-day)", "percent": 0.2, "resetsAt": "2999-01-01T00:00:00Z"}])
        with StubServer(500, b"{}") as srv:
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            result = self.mod.collect_limits("sk-x", True)
        self.assertEqual([w["label"] for w in result["limits"]], ["Weekly (7-day)"])

    def test_malformed_cache_entry_rejected_wholesale(self):
        for bad in ({"label": "Bad", "percent": 5.0, "resetsAt": ""},
                    {"label": "Bad", "percent": "half", "resetsAt": ""},
                    "not-a-dict"):
            with self.subTest(bad=bad):
                self.seed(self.mod.now_ms(), [
                    {"label": "Session (5-hour)", "percent": 0.1, "resetsAt": ""}, bad])
                with StubServer(500, b"{}") as srv:
                    os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
                    result = self.mod.collect_limits("sk-x", True)
                self.assertEqual(result["limits"], [])
                self.assertTrue(result["retry"])

    def test_401_does_not_use_fallback(self):
        self.seed(self.mod.now_ms(), [{"label": "Session (5-hour)", "percent": 0.1, "resetsAt": ""}])
        with StubServer(401, b"{}") as srv:
            os.environ["OPENCODE_GO_USAGE_URL"] = srv.url
            result = self.mod.collect_limits("sk-x", True)
        self.assertEqual(result["limits"], [])
        self.assertFalse(result["retry"])


import datetime as _dt
import sqlite3 as _sqlite3
import time


class LocalStatsTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_collector()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._saved_data = os.environ.get("XDG_DATA_HOME")
        self._saved_cache = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp.name
        os.environ["XDG_CACHE_HOME"] = self.tmp.name
        self.db = pathlib.Path(self.tmp.name) / "opencode" / "opencode.db"
        self.db.parent.mkdir(parents=True, exist_ok=True)
        conn = _sqlite3.connect(self.db)
        conn.execute("CREATE TABLE message (session_id TEXT, data TEXT)")
        now_ms = round(_dt.datetime.now().timestamp() * 1000)
        rows = [
            ("s1", {"role": "assistant", "providerID": "opencode-go", "modelID": "deepseek-flash",
                    "tokens": {"input": 100, "output": 10, "reasoning": 5,
                               "cache": {"read": 50, "write": 1}},
                    "time": {"created": now_ms}}),
            ("s1", {"role": "user", "providerID": "opencode-go", "modelID": "deepseek-flash",
                    "tokens": {}, "time": {"created": now_ms}}),
            ("s2", {"role": "assistant", "providerID": "anthropic", "modelID": "claude",
                    "tokens": {"input": 999, "output": 999}, "time": {"created": now_ms}}),
        ]
        for sid, obj in rows:
            conn.execute("INSERT INTO message VALUES (?, ?)", (sid, json.dumps(obj)))
        conn.commit()
        conn.close()

    def tearDown(self):
        for name, val in (("XDG_DATA_HOME", self._saved_data), ("XDG_CACHE_HOME", self._saved_cache)):
            if val is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = val

    def test_scan_filters_provider_and_totals_tokens(self):
        stats = self.mod.scan_local_stats(0)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["totalPrompts"], 1)
        self.assertEqual(stats["todayPrompts"], 1)
        self.assertEqual(stats["totalSessions"], 1)
        self.assertEqual(stats["todayTotalTokens"], 100 + 10 + 5 + 50 + 1)
        self.assertEqual(stats["modelUsage"]["deepseek-flash"]["inputTokens"], 100)

    def test_missing_db_is_none(self):
        self.db.unlink()
        self.assertIsNone(self.mod.scan_local_stats(0))

    def test_cache_reused_within_age(self):
        self.mod.scan_local_stats(0)
        cache = next(pathlib.Path(self.tmp.name).rglob("opencode-go-opencode-*.json"))
        first = cache.read_text()
        time.sleep(0.01)
        self.mod.scan_local_stats(900)
        self.assertEqual(cache.read_text(), first)  # reused, not rewritten

    def test_malformed_cache_is_ignored(self):
        self.mod.scan_local_stats(0)
        cache = next(pathlib.Path(self.tmp.name).rglob("opencode-go-opencode-*.json"))
        cache.write_text("{ not json")
        self.assertIsNotNone(self.mod.scan_local_stats(0))  # re-scans instead of trusting

    def test_non_dict_cache_is_ignored(self):
        self.mod.scan_local_stats(0)
        cache = next(pathlib.Path(self.tmp.name).rglob("opencode-go-opencode-*.json"))
        cache.write_text("[]")
        stats = self.mod.scan_local_stats(0)  # re-scans instead of crashing
        self.assertIsNotNone(stats)
        self.assertEqual(stats["totalPrompts"], 1)

    def _poisoned_cache(self, mutate):
        self.mod.scan_local_stats(0)
        cache = next(pathlib.Path(self.tmp.name).rglob("opencode-go-opencode-*.json"))
        payload = json.loads(cache.read_text())
        payload["stats"]["totalPrompts"] = 999  # trusted cache would surface this
        mutate(payload["stats"])
        cache.write_text(json.dumps(payload))
        return cache

    def test_malformed_recent_days_entry_is_ignored(self):
        self._poisoned_cache(lambda stats: stats.update(
            {"recentDays": [{"date": 1, "messageCount": 0}]}))
        stats = self.mod.scan_local_stats(900)
        self.assertEqual(stats["totalPrompts"], 1)  # re-scanned, not trusted

    def test_malformed_active_dates_entry_is_ignored(self):
        self._poisoned_cache(lambda stats: stats.update({"activeDates": ["2026-09-10", 5]}))
        stats = self.mod.scan_local_stats(900)
        self.assertEqual(stats["totalPrompts"], 1)

    def test_unwritable_cache_still_returns_stats(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root bypasses directory permissions")
        ro = pathlib.Path(self.tmp.name) / "readonly"
        ro.mkdir()
        os.chmod(ro, 0o500)
        os.environ["XDG_CACHE_HOME"] = str(ro / "cache")
        stats = self.mod.scan_local_stats(0)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["totalPrompts"], 1)


if __name__ == "__main__":
    unittest.main()
