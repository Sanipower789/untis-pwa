import os
import tempfile
import unittest
from pathlib import Path


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
