import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from ops import render_log_monitor as monitor


class RenderLogMonitorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cfg = {
            "api_key": "private-render-key", "service_id": "", "owner_id": "",
            "state_path": Path(directory.name) / "state.json", "secrets": ["private-render-key"],
        }
        self.now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
        self.service = {"id": "srv-test", "ownerId": "tea-test", "url": monitor.SERVICE_URL}
        with monitor.state_store(self.cfg["state_path"]) as state:
            state["service"] = self.service

    def row(self, identity, message="RuntimeError: test failure", level="error", when=None, **labels):
        return {"id": identity, "timestamp": monitor.iso(when or self.now - timedelta(seconds=5)),
                "message": message, "labels": [{"name": k, "value": v}
                for k, v in {"level": level, "instance": "instance-one", **labels}.items()]}

    def state(self):
        return json.loads(self.cfg["state_path"].read_text())

    def poll(self, rows, when=None, page=None):
        with patch.object(monitor, "fetch_logs", return_value=(rows, page)):
            return monitor.collect(self.cfg, when or self.now)

    def actionable(self):
        return self.poll([self.row("one"), self.row("two")])["incidents"][0]["fingerprint"]

    def test_fingerprint_preserves_cause_but_ignores_metadata(self):
        first = '[abc123] 2026-09-07 19:45:01 ERROR File "app.py", line 42, in worker: database is locked'
        second = '[def456] 2026-09-07 20:12:03 ERROR File "app.py", line 99, in worker: database is locked'
        self.assertEqual(monitor.fingerprint(first), monitor.fingerprint(second))
        self.assertNotEqual(monitor.fingerprint(first), monitor.fingerprint(first.replace("locked", "malformed")))

    def test_missing_key_never_calls_render(self):
        self.cfg["api_key"] = ""
        with patch.object(monitor.requests, "get") as get:
            self.assertEqual(monitor.collect(self.cfg)["status"], "configuration_required")
        get.assert_not_called()

    def test_http_error_fingerprint_ignores_access_metadata_and_cache_busters(self):
        first = '127.0.0.1 - [08/Sep/2026:10:00:00 +0000] "GET /api/exams?ts=111 HTTP/1.1" 500 256'
        second = '127.0.0.2 - [08/Sep/2026:10:01:00 +0000] "GET /api/exams?ts=222 HTTP/1.1" 500 512'
        self.assertEqual(monitor.fingerprint(first), monitor.fingerprint(second))
        self.assertNotEqual(monitor.fingerprint(first), monitor.fingerprint(second.replace('/api/exams', '/admin')))

    def test_redacts_known_secrets_urls_and_json_credentials(self):
        message = 'private-render-key Bearer some-token https://a.example/?token=abc user@example.org {"password": "hello world", "token": "not-known"}'
        result = monitor.redact(message, self.cfg)
        for secret in ("private-render-key", "some-token", "a.example", "user@example.org", "hello world", "not-known"):
            self.assertNotIn(secret, result)

    def test_overlap_does_not_count_same_row_twice(self):
        rows = [self.row("one")]
        self.assertEqual(self.poll(rows)["status"], "no_action")
        self.assertEqual(self.poll(rows)["status"], "no_action")
        result = self.poll(rows + [self.row("two")])
        self.assertEqual(result["incidents"][0]["count"], 2)
        self.assertEqual(self.state()["attempts"], [])

    def test_repetition_accumulates_across_polls(self):
        self.poll([self.row("one")])
        result = self.poll([self.row("two")])
        self.assertEqual(result["status"], "action_required")
        self.assertEqual(result["incidents"][0]["count"], 2)

    def test_warnings_need_five_distinct_events(self):
        rows = [self.row(str(i), "fetch_exams failed", "warning") for i in range(5)]
        self.assertEqual(self.poll(rows[:4])["status"], "no_action")
        self.assertEqual(self.poll(rows)["incidents"][0]["kind"], "warning")

    def test_critical_and_corruption_are_immediately_actionable(self):
        result = self.poll([self.row("crash", "Exited with status 1", "info"),
                            self.row("data", "database disk image is malformed", "warning")])
        self.assertTrue(result["action_required"])
        self.assertEqual(len(monitor.candidates(self.state(), self.now)), 2)

    def test_http_500_and_python_exception_without_error_level(self):
        self.assertEqual(monitor.classify(self.row("a", level="info", statusCode="500"), "GET /"), "error")
        self.assertEqual(monitor.classify(self.row("b", level="info"), "sqlite3.OperationalError: database is locked"), "error")

    def test_old_or_future_events_do_not_trigger_repairs(self):
        rows = [self.row(str(i), when=self.now + timedelta(hours=3 * direction))
                for i, direction in enumerate((-1, -1, 1, 1))]
        self.assertEqual(self.poll(rows)["status"], "no_action")

    def test_traceback_context_is_bounded_and_instance_scoped(self):
        rows = [self.row("other", 'File "unrelated.py", line 3', "info", instance="another"),
                self.row("trace", 'File "app.py", line 3, in worker', "info"),
                self.row("one"), self.row("two")]
        context = self.poll(rows)["incidents"][0]["context"]
        self.assertTrue(any("app.py" in text for text in context))
        self.assertFalse(any("unrelated" in text for text in context))

    def test_claim_is_exclusive_and_consumes_one_attempt(self):
        key = self.actionable()
        result = monitor.claim(key, self.cfg, self.now)
        self.assertEqual(result["status"], "claimed")
        self.assertEqual(monitor.claim(key, self.cfg, self.now)["status"], "blocked")
        self.assertEqual(len(self.state()["attempts"]), 1)
        self.assertEqual(self.poll([])["status"], "repair_in_progress")

    def test_claim_requires_actionable_fingerprint(self):
        self.assertEqual(monitor.claim("invented", self.cfg, self.now)["status"], "no_action")
        self.assertEqual(self.state()["attempts"], [])

    def test_budget_enforces_rolling_day_and_global_cooldown(self):
        key = self.actionable()
        for ages in ((1800,), (4000, 8000)):
            with self.subTest(ages=ages):
                with monitor.state_store(self.cfg["state_path"]) as state:
                    state["attempts"] = [(self.now - timedelta(seconds=age)).timestamp() for age in ages]
                self.assertEqual(monitor.claim(key, self.cfg, self.now)["status"], "budget_reached")
                self.assertEqual(self.poll([])["status"], "budget_reached")
        with monitor.state_store(self.cfg["state_path"]) as state:
            state["attempts"] = [(self.now - timedelta(hours=25)).timestamp()]
        self.assertEqual(monitor.claim(key, self.cfg, self.now)["status"], "claimed")

    def test_finish_rejects_wrong_reservation(self):
        key = self.actionable()
        monitor.claim(key, self.cfg, self.now)
        with self.assertRaises(monitor.MonitorError):
            monitor.finish("wrong", "transient", cfg=self.cfg, now=self.now)
        self.assertIsNotNone(self.state()["active"])

    def test_failed_repair_pauses_future_repairs(self):
        claim = monitor.claim(self.actionable(), self.cfg, self.now)
        monitor.finish(claim["id"], "failed", cfg=self.cfg, now=self.now)
        self.assertEqual(self.poll([])["status"], "paused")
        self.assertIsNone(self.state()["active"])

    def test_finish_fixed_requires_verified_deployed_sha(self):
        claim = monitor.claim(self.actionable(), self.cfg, self.now)
        with self.assertRaises(monitor.MonitorError):
            monitor.finish(claim["id"], "fixed", cfg=self.cfg)
        with patch.object(monitor, "deployment", return_value={"status": "pending"}):
            with self.assertRaises(monitor.MonitorError):
                monitor.finish(claim["id"], "fixed", "a" * 40, self.cfg, self.now)
        self.assertIsNotNone(self.state()["active"])
        with patch.object(monitor, "deployment", return_value={"status": "healthy"}):
            monitor.finish(claim["id"], "fixed", "a" * 40, self.cfg, self.now)
        self.assertIsNone(self.state()["active"])
        self.assertEqual(next(iter(self.state()["incidents"].values()))["occurrences"], [])

    def test_corrupt_state_fails_closed(self):
        for value in ("{", "{}", "[]"):
            self.cfg["state_path"].write_text(value)
            with self.assertRaises(monitor.MonitorError):
                monitor.claim("anything", self.cfg)

    def test_state_changes_rolled_back_on_error(self):
        with self.assertRaises(RuntimeError):
            with monitor.state_store(self.cfg["state_path"]) as state:
                state["paused"] = True
                raise RuntimeError("test")
        self.assertNotIn("paused", self.state())

    def test_process_lock_prevents_concurrent_state_access(self):
        with monitor.state_store(self.cfg["state_path"]):
            with self.assertRaises(monitor.MonitorBusy):
                with monitor.state_store(self.cfg["state_path"]):
                    self.fail("concurrent access allowed")

    def test_render_log_pagination_uses_exact_cursors(self):
        start, end = monitor.iso(self.now - timedelta(hours=2)), monitor.iso(self.now)
        middle = monitor.iso(self.now - timedelta(hours=1))
        pages = [{"logs": [self.row("a")], "hasMore": True, "nextStartTime": middle, "nextEndTime": end},
                 {"logs": [self.row("b")], "hasMore": False}]
        with patch.object(monitor, "api_get", side_effect=pages) as get:
            rows, cursor = monitor.fetch_logs(self.cfg, self.service, start, end)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(cursor)
        self.assertEqual(get.call_args_list[1].args[2]["startTime"], middle)

    def test_pagination_limit_preserves_backlog(self):
        start, end = monitor.iso(self.now - timedelta(hours=2)), monitor.iso(self.now)
        middle = monitor.iso(self.now - timedelta(hours=1))
        page = {"logs": [self.row("a")], "hasMore": True, "nextStartTime": middle, "nextEndTime": end}
        with patch.object(monitor, "MAX_PAGES", 1), patch.object(monitor, "api_get", return_value=page):
            _, cursor = monitor.fetch_logs(self.cfg, self.service, start, end)
        self.poll([], page=cursor)
        self.assertEqual(self.state()["page"], {"start": middle, "end": end})
        self.assertNotIn("last_poll", self.state())
        with patch.object(monitor, "fetch_logs", return_value=([], None)) as fetch:
            monitor.collect(self.cfg, self.now + timedelta(hours=1))
        self.assertEqual(fetch.call_args.args[2:], (middle, end))
        self.assertEqual(self.state()["last_poll"], end)

    def test_invalid_api_response_does_not_advance_cursor(self):
        self.poll([])
        previous = self.state()
        for page in ({}, {"logs": [{}], "hasMore": False}, {"logs": [], "hasMore": True}):
            with self.subTest(page=page), patch.object(monitor, "api_get", return_value=page):
                with self.assertRaises(monitor.MonitorError):
                    monitor.collect(self.cfg, self.now + timedelta(hours=1))
            self.assertEqual(previous, self.state())

    def test_service_discovery_matches_url_not_just_name(self):
        correct = {"id": "srv-target", "ownerId": "tea-target", "serviceDetails": {"url": monitor.SERVICE_URL}}
        wrong = {"id": "srv-wrong", "ownerId": "tea-wrong", "serviceDetails": {"url": "https://another.onrender.com"}}
        with patch.object(monitor, "api_get", return_value=[{"service": wrong}, {"service": correct}]):
            result = monitor.resolve_service(self.cfg, {})
        self.assertEqual(result["id"], "srv-target")
        self.assertEqual(result["ownerId"], "tea-target")

    def test_service_identity_ambiguity_and_wrong_owner_fail_closed(self):
        with patch.object(monitor, "api_get", return_value=[]):
            with self.assertRaises(monitor.MonitorError):
                monitor.resolve_service(self.cfg, {})
        self.cfg["owner_id"] = "tea-someone-else"
        with self.assertRaises(monitor.MonitorError):
            monitor.resolve_service(self.cfg, self.state())

    def test_api_rejects_redirect_and_does_not_expose_body(self):
        response = Mock(status_code=302)
        with patch.object(monitor.requests, "get", return_value=response) as get:
            with self.assertRaisesRegex(monitor.MonitorError, "HTTP 302"):
                monitor.api_get(self.cfg, "/services")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertEqual(get.call_args.kwargs["timeout"], (5, 20))
        response.json.assert_not_called()

    def test_deployment_requires_exact_sha_and_live_status(self):
        for status in ("build_in_progress", "build_failed", "live"):
            rows = [{"deploy": {"id": "dep-test", "commit": {"id": "a" * 40}, "status": status}}]
            response = Mock(status_code=200)
            response.json.return_value = {"ok": True, "vacations": []}
            with patch.object(monitor, "api_get", return_value=rows), patch.object(monitor.requests, "get", return_value=response) as get:
                self.assertEqual(monitor.deployment("b" * 40, self.cfg)["status"], "pending")
                result = monitor.deployment("a" * 40, self.cfg)
            expected = {"build_in_progress": "pending", "build_failed": "failed", "live": "healthy"}[status]
            self.assertEqual(result["status"], expected)
            if status != "live":
                get.assert_not_called()

    def test_deployment_health_validates_database_api(self):
        rows = [{"deploy": {"id": "dep-test", "commit": {"id": "a" * 40}, "status": "live"}}]
        response = Mock(status_code=200)
        response.json.return_value = {"error": "database unavailable"}
        with patch.object(monitor, "api_get", return_value=rows), patch.object(monitor.requests, "get", return_value=response):
            result = monitor.deployment("a" * 40, self.cfg)
        self.assertEqual(result["status"], "unhealthy")

    def test_deployment_does_not_accept_superseded_live_commit(self):
        rows = [{"deploy": {"id": "dep-new", "commit": {"id": "b" * 40}, "status": "live"}},
                {"deploy": {"id": "dep-old", "commit": {"id": "a" * 40}, "status": "live"}}]
        with patch.object(monitor, "api_get", return_value=rows), patch.object(monitor.requests, "get") as get:
            self.assertEqual(monitor.deployment("a" * 40, self.cfg)["status"], "superseded")
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
