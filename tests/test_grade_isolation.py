import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


TEST_DB_PATH = Path(tempfile.gettempdir()) / "untis_pwa_grade_isolation_tests.db"
try:
    TEST_DB_PATH.unlink()
except FileNotFoundError:
    pass

os.environ.update({
    "SECRET_KEY": "grade-isolation-test-secret",
    "ADMIN_TOKEN": "grade-isolation-test-admin",
    "DB_PATH": str(TEST_DB_PATH),
    "BACKUP_WEBHOOK_URL": "",
    "AUTO_RESTORE_URL": "",
    "UNTIS_BASE": "https://example.invalid/WebUntis/jsonrpc.do",
    "UNTIS_SCHOOL": "test-school",
    "UNTIS_USER": "ef-user",
    "UNTIS_PASS": "ef-pass",
    "UNTIS_ELEMENT_ID": "1",
    "UNTIS_USER_Q1": "q1-user",
    "UNTIS_PASS_Q1": "q1-pass",
    "UNTIS_ELEMENT_ID_Q1": "2",
    "UNTIS_USER_Q2": "q2-user",
    "UNTIS_PASS_Q2": "q2-pass",
    "UNTIS_ELEMENT_ID_Q2": "3",
})

import app as app_module
import untis_client


class GradeIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.paths = {
            grade: str(root / f"course_mapping_{grade.lower()}.txt")
            for grade in app_module.SUPPORTED_GRADES
        }
        labels = {"EF": "EF Name", "Q1": "Q1 Name", "Q2": "Q2 Name"}
        for grade, path in self.paths.items():
            Path(path).write_text(f"same raw key={labels[grade]}\n", encoding="utf-8")

        self.old_course_paths = app_module.COURSE_MAP_PATHS
        self.old_legacy_path = app_module.LEGACY_COURSE_MAP_PATH
        self.old_room_path = app_module.ROOM_MAP_PATH
        self.old_available_grades = app_module.available_grades
        self.old_seen_by_grade = app_module.SEEN_SUBJECTS_RAW_BY_GRADE

        app_module.COURSE_MAP_PATHS = self.paths
        app_module.LEGACY_COURSE_MAP_PATH = str(root / "course_mapping.txt")
        app_module.ROOM_MAP_PATH = str(root / "rooms_mapping.txt")
        Path(app_module.ROOM_MAP_PATH).write_text("", encoding="utf-8")
        app_module.available_grades = lambda: list(app_module.SUPPORTED_GRADES)
        app_module.SEEN_SUBJECTS_RAW_BY_GRADE = {
            grade: ["SAME RAW KEY"]
            for grade in app_module.SUPPORTED_GRADES
        }
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.COURSE_MAP_PATHS = self.old_course_paths
        app_module.LEGACY_COURSE_MAP_PATH = self.old_legacy_path
        app_module.ROOM_MAP_PATH = self.old_room_path
        app_module.available_grades = self.old_available_grades
        app_module.SEEN_SUBJECTS_RAW_BY_GRADE = self.old_seen_by_grade
        self.temp_dir.cleanup()

    def _admin_login(self):
        with self.client.session_transaction() as session:
            session["admin_ok"] = True

    def test_mapping_api_keeps_identical_keys_separate(self):
        payload = self.client.get("/api/mappings").get_json()
        self.assertNotIn("courses", payload)
        self.assertEqual(payload["coursesByGrade"]["EF"]["same raw key"], "EF Name")
        self.assertEqual(payload["coursesByGrade"]["Q1"]["same raw key"], "Q1 Name")
        self.assertEqual(payload["coursesByGrade"]["Q2"]["same raw key"], "Q2 Name")

    def test_user_and_admin_views_expose_q2_controls(self):
        index_html = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="grade-switcher"', index_html)
        self._admin_login()
        admin_html = self.client.get("/admin/mappings").get_data(as_text=True)
        self.assertIn('id="subjects-q2"', admin_html)
        self.assertIn('id="save-sub-q2"', admin_html)

    def test_course_options_include_each_grade_with_its_own_label(self):
        payload = self.client.get("/api/courses").get_json()
        options = {item["key"]: item["label"] for item in payload["courses"]}
        self.assertEqual(options["EF:same raw key"], "EF Name")
        self.assertEqual(options["Q1:same raw key"], "Q1 Name")
        self.assertEqual(options["Q2:same raw key"], "Q2 Name")

    def test_course_options_do_not_leak_mapping_keys_between_grades(self):
        for grade, path in self.paths.items():
            Path(path).write_text(
                "ef only=EF course\nq1 only=Q1 course\nq2 only=Q2 course\n",
                encoding="utf-8",
            )
        app_module.SEEN_SUBJECTS_RAW_BY_GRADE = {
            "EF": ["EF ONLY"],
            "Q1": ["Q1 ONLY"],
            "Q2": ["Q2 ONLY"],
        }

        with patch.object(app_module, "_load_raw_subjects_for_grade", return_value=[]):
            payload = self.client.get("/api/courses").get_json()

        options_by_grade = {
            grade: {
                item["key"]
                for item in payload["courses"]
                if item["grade"] == grade
            }
            for grade in app_module.SUPPORTED_GRADES
        }
        self.assertEqual(options_by_grade["EF"], {"EF:ef only"})
        self.assertEqual(options_by_grade["Q1"], {"Q1:q1 only"})
        self.assertEqual(options_by_grade["Q2"], {"Q2:q2 only"})

    def test_saved_mapping_only_course_does_not_redefine_grade_catalog(self):
        Path(self.paths["Q2"]).write_text(
            "same raw key=Q2 Name\nsaved only=Saved Q2\n",
            encoding="utf-8",
        )
        app_module.SEEN_SUBJECTS_RAW_BY_GRADE = {
            grade: [] for grade in app_module.SUPPORTED_GRADES
        }
        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute("DELETE FROM users WHERE username = ?", ("saved-q2-test",))
            db.execute(
                "INSERT INTO users (username, password_hash, profile_json) VALUES (?, ?, ?)",
                (
                    "saved-q2-test",
                    "hash",
                    '{"grade":"Q2","courses":["Q2:saved only"]}',
                ),
            )
            db.commit()
        try:
            with patch.object(app_module, "_load_raw_subjects_for_grade", return_value=[]):
                payload = self.client.get("/api/courses").get_json()
        finally:
            with app_module.app.app_context():
                db = app_module.get_db()
                db.execute("DELETE FROM users WHERE username = ?", ("saved-q2-test",))
                db.commit()

        keys = {item["key"] for item in payload["courses"]}
        self.assertNotIn("Q2:saved only", keys)
        self.assertNotIn("EF:saved only", keys)
        self.assertNotIn("Q1:saved only", keys)

    def test_admin_rename_changes_only_the_requested_grade(self):
        self._admin_login()
        response = self.client.post(
            "/api/admin/save",
            json={"courses_q1": {"same raw key": "Renamed Q1"}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            app_module._course_map_normalized_for_grade("EF")["same raw key"],
            "EF Name",
        )
        self.assertEqual(
            app_module._course_map_normalized_for_grade("Q1")["same raw key"],
            "Renamed Q1",
        )
        self.assertEqual(
            app_module._course_map_normalized_for_grade("Q2")["same raw key"],
            "Q2 Name",
        )

    def test_admin_rejects_an_unscoped_subject_mapping(self):
        self._admin_login()
        response = self.client.post(
            "/api/admin/save",
            json={"courses": {"same raw key": "Unsafe Rename"}},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"],
            "grade_required_for_course_mappings",
        )

    def test_every_existing_mapping_key_is_renameable_per_grade(self):
        self._admin_login()
        payload = self.client.get("/api/admin/state").get_json()
        for grade in ("ef", "q1", "q2"):
            self.assertIn("same raw key", payload[f"subjects_grouped_{grade}"])

    def test_backup_contains_only_grade_scoped_subject_maps(self):
        with app_module.app.app_context():
            mappings = app_module._build_backup_payload()["mappings"]
        self.assertNotIn("courses", mappings)
        self.assertEqual(mappings["courses_ef"]["same raw key"], "EF Name")
        self.assertEqual(mappings["courses_q1"]["same raw key"], "Q1 Name")
        self.assertEqual(mappings["courses_q2"]["same raw key"], "Q2 Name")

    def test_legacy_unscoped_backup_restores_users_without_mixing_mappings(self):
        payload = {
            "meta": {"version": 3},
            "database": {
                "users": [{
                    "id": 1,
                    "username": "restored-user",
                    "password_hash": "hash",
                    "password_plain": None,
                    "profile": {"grade": "Q1", "courses": ["Q1:SAME RAW KEY"]},
                    "created_at": "2026-01-01T00:00:00",
                }],
                "vacations": [],
                "exams_manual": [],
                "settings": {},
            },
            "mappings": {
                "courses": {"same raw key": "Ambiguous Legacy Name"},
                "rooms": {},
            },
            "seen": {
                "subjects_raw": [],
                "rooms_raw": [],
            },
        }

        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute("DELETE FROM users")
            db.commit()
            with (
                patch.object(app_module, "_save_last_backup"),
                patch.object(app_module, "_course_map_write_for_grade") as write_grade,
                patch.object(app_module, "_write_mapping_txt"),
                patch.object(app_module, "_save_seen_raw"),
            ):
                app_module._apply_backup_payload(payload)
            restored = db.execute(
                "SELECT username FROM users ORDER BY id"
            ).fetchall()
            db.execute("DELETE FROM users")
            db.commit()

        self.assertEqual([row["username"] for row in restored], ["restored-user"])
        write_grade.assert_not_called()

    def test_automatic_backup_cannot_replace_more_remote_users(self):
        remote_response = Mock()
        remote_response.raise_for_status.return_value = None
        remote_response.json.return_value = {
            "database": {"users": [{} for _ in range(60)]},
        }
        payload = {"database": {"users": [{}]}}

        with (
            patch.object(app_module, "BACKUP_WEBHOOK_URL", "https://backup.invalid"),
            patch.object(app_module, "AUTO_RESTORE_URL", "https://restore.invalid"),
            patch.object(app_module.requests, "get", return_value=remote_response),
            patch.object(app_module.requests, "post") as backup_post,
        ):
            sent = app_module._maybe_send_backup("profile_update", payload)

        self.assertFalse(sent)
        backup_post.assert_not_called()

    def test_profile_grade_is_explicit_and_controls_rollover(self):
        profile = app_module._normalise_profile({
            "grade": "Q2",
            "courses": ["Q1:SAME RAW KEY"],
        })
        self.assertEqual(profile["grade"], "Q2")
        self.assertEqual(profile["courses"], ["Q2:same raw key"])

    def test_enabled_upper_grade_requires_its_own_element_id(self):
        with self.assertRaises(RuntimeError):
            untis_client._validate_optional_credentials("Q2", "user", "pass", 0)

    def test_ambiguous_legacy_profile_is_not_assigned_to_ef(self):
        profile = app_module._normalise_profile({
            "courses": ["SAME RAW KEY"],
        })
        self.assertEqual(profile["grade"], "")
        self.assertEqual(profile["courses"], ["same raw key"])


def tearDownModule():
    try:
        TEST_DB_PATH.unlink()
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    unittest.main()
