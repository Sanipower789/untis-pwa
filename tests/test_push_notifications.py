import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


TEST_DB_PATH = Path(tempfile.gettempdir()) / "untis_pwa_push_notification_tests.db"
try:
    TEST_DB_PATH.unlink()
except FileNotFoundError:
    pass

os.environ.update({
    "SECRET_KEY": "push-notification-test-secret",
    "ADMIN_TOKEN": "push-notification-test-admin",
    "DB_PATH": str(TEST_DB_PATH),
    "BACKUP_WEBHOOK_URL": "",
    "AUTO_RESTORE_URL": "",
    "UNTIS_BASE": "https://example.invalid/WebUntis/jsonrpc.do",
    "UNTIS_SCHOOL": "test-school",
    "UNTIS_USER": "ef-user",
    "UNTIS_PASS": "ef-pass",
    "UNTIS_ELEMENT_ID": "1",
    "NOTIFICATION_MONITOR_ENABLED": "false",
})

import app as app_module


class PushNotificationTests(unittest.TestCase):
    def setUp(self):
        app_module.init_db()
        self.client = app_module.app.test_client()
        self.old_vapid = (
            app_module.VAPID_PUBLIC_KEY,
            app_module.VAPID_PRIVATE_KEY,
            app_module.VAPID_SUBJECT,
            app_module.webpush,
        )
        app_module.VAPID_PUBLIC_KEY = "test-public-key"
        app_module.VAPID_PRIVATE_KEY = "test-private-key"
        app_module.VAPID_SUBJECT = "mailto:test@example.com"
        app_module.webpush = Mock()

        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute("DELETE FROM notification_deliveries")
            db.execute("DELETE FROM notification_snapshots")
            db.execute("DELETE FROM notification_runtime")
            db.execute("DELETE FROM push_subscriptions")
            db.execute("DELETE FROM users")
            cursor = db.execute(
                "INSERT INTO users (username, password_hash, profile_json) VALUES (?, ?, ?)",
                ("push-user", "test-hash", '{}'),
            )
            self.user_id = int(cursor.lastrowid)
            db.commit()

    def tearDown(self):
        (
            app_module.VAPID_PUBLIC_KEY,
            app_module.VAPID_PRIVATE_KEY,
            app_module.VAPID_SUBJECT,
            app_module.webpush,
        ) = self.old_vapid
        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute("DELETE FROM notification_deliveries")
            db.execute("DELETE FROM notification_snapshots")
            db.execute("DELETE FROM notification_runtime")
            db.execute("DELETE FROM push_subscriptions")
            db.execute("DELETE FROM users")
            db.commit()

    def _login_user(self):
        with self.client.session_transaction() as session:
            session["user_id"] = self.user_id

    def _login_admin(self):
        with self.client.session_transaction() as session:
            session["admin_ok"] = True

    @staticmethod
    def _subscription(suffix="one"):
        return {
            "endpoint": f"https://push.example.test/subscriptions/{suffix}",
            "keys": {"p256dh": f"p256dh-{suffix}", "auth": f"auth-{suffix}"},
        }

    def _save_subscription(self, suffix="one"):
        self._login_user()
        response = self.client.post(
            "/api/push/subscription",
            json={"subscription": self._subscription(suffix)},
        )
        self.assertEqual(response.status_code, 200)
        return response

    def test_subscription_api_requires_a_logged_in_user(self):
        response = self.client.get("/api/push/subscription")
        self.assertEqual(response.status_code, 401)

    def test_subscription_can_be_saved_queried_and_deleted(self):
        self._save_subscription()

        status = self.client.get("/api/push/subscription").get_json()
        self.assertTrue(status["configured"])
        self.assertTrue(status["subscribed"])
        self.assertEqual(status["subscriptionCount"], 1)
        self.assertEqual(status["publicKey"], "test-public-key")
        endpoint = self._subscription()["endpoint"]
        expected_hash = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
        self.assertEqual(status["endpointHashes"], [expected_hash])
        self.assertNotIn(endpoint, json.dumps(status))

        deleted = self.client.delete(
            "/api/push/subscription",
            json={"subscription": self._subscription()},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.get_json()["deleted"])
        self.assertFalse(self.client.get("/api/push/subscription").get_json()["subscribed"])

    def test_same_endpoint_moves_to_the_current_account_without_duplicates(self):
        self._save_subscription()
        with app_module.app.app_context():
            db = app_module.get_db()
            second = db.execute(
                "INSERT INTO users (username, password_hash, profile_json) VALUES (?, ?, ?)",
                ("push-user-two", "test-hash", '{}'),
            )
            second_user_id = int(second.lastrowid)
            db.commit()

        with self.client.session_transaction() as session:
            session["user_id"] = second_user_id
        moved = self.client.post(
            "/api/push/subscription",
            json={"subscription": self._subscription()},
        )
        self.assertEqual(moved.status_code, 200)

        with app_module.app.app_context():
            rows = app_module.get_db().execute(
                "SELECT user_id FROM push_subscriptions"
            ).fetchall()
        self.assertEqual([int(row["user_id"]) for row in rows], [second_user_id])

    def test_admin_test_send_uses_saved_subscription(self):
        self._save_subscription()
        self._login_admin()

        response = self.client.post(
            "/api/admin/push/test",
            json={"user_id": self.user_id, "title": "Test", "body": "Hallo", "url": "/"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["sent"], 1)
        app_module.webpush.assert_called_once()
        call = app_module.webpush.call_args.kwargs
        self.assertEqual(call["subscription_info"], self._subscription())
        self.assertEqual(json.loads(call["data"])["body"], "Hallo")
        self.assertEqual(call["vapid_claims"], {"sub": "mailto:test@example.com"})

    def test_stale_subscription_is_removed_after_gone_response(self):
        self._save_subscription()
        gone = RuntimeError("gone")
        gone.response = Mock(status_code=410)
        app_module.webpush.side_effect = gone
        self._login_admin()

        response = self.client.post(
            "/api/admin/push/test",
            json={"title": "Test", "body": "Hallo"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["removed"], 1)
        with app_module.app.app_context():
            count = app_module.get_db().execute(
                "SELECT COUNT(*) FROM push_subscriptions"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_admin_state_and_backup_include_subscription_counts(self):
        self._save_subscription()
        self._login_admin()
        with patch.object(app_module, "_current_subjects_for_grade", return_value=[]):
            state = self.client.get("/api/admin/state").get_json()

        self.assertTrue(state["push"]["configured"])
        self.assertEqual(state["push"]["subscription_count"], 1)
        self.assertIn("monitor_enabled", state["push"])
        self.assertIn("last_result", state["push"])
        user = next(item for item in state["users"] if item["id"] == self.user_id)
        self.assertEqual(user["push_subscription_count"], 1)

        with app_module.app.app_context():
            payload = app_module._build_backup_payload()
        backed_up = payload["database"]["push_subscriptions"]
        self.assertEqual(len(backed_up), 1)
        self.assertEqual(backed_up[0]["user_id"], self.user_id)
        self.assertEqual(backed_up[0]["subscription"], self._subscription())

    def test_pages_and_service_worker_expose_notification_controls(self):
        user_html = self.client.get("/").get_data(as_text=True)
        sidebar_html = user_html.split('<aside id="sidebar"', 1)[1].split("</aside>", 1)[0]
        account_html = user_html.split('<div id="account-view"', 1)[1].split("</div>", 1)[0]
        self.assertIn('id="sidebar-notifications"', sidebar_html)
        self.assertIn('id="push-enable"', sidebar_html)
        self.assertIn('id="push-disable"', sidebar_html)
        self.assertNotIn('id="push-enable"', account_html)
        self.assertIn('id="push-prompt"', user_html)
        self.assertIn('id="push-prompt-enable"', user_html)
        self.assertIn('id="push-prompt-later"', user_html)
        self.assertIn('id="notification-enabled"', sidebar_html)
        self.assertIn('id="notification-timetable"', sidebar_html)
        self.assertIn('id="notification-exams"', sidebar_html)
        self.assertIn('id="notification-summary"', sidebar_html)

        self._login_admin()
        admin_html = self.client.get("/admin/mappings").get_data(as_text=True)
        self.assertIn('id="push-test-form"', admin_html)
        service_worker_response = self.client.get("/sw.js")
        service_worker = service_worker_response.get_data(as_text=True)
        service_worker_response.close()
        self.assertIn('addEventListener("push"', service_worker)
        self.assertIn('addEventListener("notificationclick"', service_worker)
        self.assertIn('url.pathname === "/api/banner-image"', service_worker)
        self.assertIn("await fresh.blob()", service_worker)

    def test_preferences_are_saved_per_account_and_normalised(self):
        self._login_user()
        defaults = self.client.get("/api/notifications/preferences")
        self.assertEqual(defaults.status_code, 200)
        self.assertTrue(defaults.get_json()["preferences"]["timetableChanges"])

        saved = self.client.put(
            "/api/notifications/preferences",
            json={
                "preferences": {
                    "enabled": True,
                    "timetableChanges": False,
                    "examReminders": True,
                    "examReminderDays": 99,
                    "dailySummary": True,
                    "dailySummaryTime": "07:15",
                }
            },
        )
        self.assertEqual(saved.status_code, 200)
        preferences = saved.get_json()["preferences"]
        self.assertFalse(preferences["timetableChanges"])
        self.assertEqual(preferences["examReminderDays"], 14)
        self.assertEqual(preferences["dailySummaryTime"], "07:15")

        with app_module.app.app_context():
            profile = app_module._load_profile_for_user(app_module._load_user(self.user_id))
        self.assertEqual(profile["notificationPreferences"], preferences)

    def test_timetable_snapshot_is_silent_then_reports_same_grade_change(self):
        original = {
            "id": "77-20260903-910",
            "date": "2026-09-03",
            "start": "09:10",
            "end": "10:10",
            "subject": "MATHE",
            "subject_original": "MATHE",
            "room": "B1",
            "teacher": "AB",
            "status": "normal",
        }
        with app_module.app.app_context():
            baseline = app_module._compare_and_store_notification_snapshot(
                "Q2", app_module.date(2026, 8, 31), [original], app_module.date(2026, 9, 2)
            )
            events = app_module._compare_and_store_notification_snapshot(
                "Q2",
                app_module.date(2026, 8, 31),
                [{**original, "room": "B2"}],
                app_module.date(2026, 9, 2),
            )
            repeated = app_module._compare_and_store_notification_snapshot(
                "Q2",
                app_module.date(2026, 8, 31),
                [{**original, "room": "B2"}],
                app_module.date(2026, 9, 2),
            )

        self.assertEqual(baseline, [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["grade"], "Q2")
        self.assertEqual(events[0]["changes"], ["room"])
        self.assertEqual(repeated, [])

    def test_automatic_timetable_push_is_filtered_and_deduplicated(self):
        self._save_subscription()
        profile = app_module._normalise_profile({
            "grade": "Q2",
            "courses": ["Q2:MATHE", "EF:MATHE"],
            "notificationPreferences": {"enabled": True, "timetableChanges": True},
        })
        event = {
            "key": "timetable:Q2:test:77:new",
            "grade": "Q2",
            "weekStart": "2026-08-31",
            "changes": ["room"],
            "lesson": {
                "id": "77-20260903-910",
                "date": "2026-09-03",
                "start": "09:10",
                "end": "10:10",
                "subject": "MATHE",
                "subject_original": "MATHE",
                "room": "B2",
                "teacher": "AB",
                "status": "normal",
            },
            "previous": {
                "date": "2026-09-03",
                "start": "09:10",
                "end": "10:10",
                "subject": "MATHE",
                "subject_original": "MATHE",
                "room": "B1",
                "teacher": "AB",
                "status": "normal",
            },
        }
        with app_module.app.app_context():
            app_module._save_profile(self.user_id, profile)
            now = app_module.datetime(2026, 9, 2, 12, 0, tzinfo=app_module.APP_TZ)
            first = app_module._send_timetable_event_notifications([event], now)
            second = app_module._send_timetable_event_notifications([event], now)

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(app_module.webpush.call_count, 1)
        payload = json.loads(app_module.webpush.call_args.kwargs["data"])
        self.assertIn("Q2", payload["title"])
        self.assertIn("Raum B1", payload["body"])


def tearDownModule():
    try:
        TEST_DB_PATH.unlink()
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    unittest.main()
