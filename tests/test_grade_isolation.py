import base64
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
    "NOTIFICATION_MONITOR_ENABLED": "false",
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
        self.old_fetch_schoolyear_subjects = app_module.fetch_schoolyear_subjects
        self.old_subject_catalog_cache = app_module._subject_catalog_cache
        self.old_subject_catalog_cached_at = app_module._subject_catalog_cached_at
        self.old_subject_catalog_live = app_module._subject_catalog_live

        app_module.COURSE_MAP_PATHS = self.paths
        app_module.LEGACY_COURSE_MAP_PATH = str(root / "course_mapping.txt")
        app_module.ROOM_MAP_PATH = str(root / "rooms_mapping.txt")
        Path(app_module.ROOM_MAP_PATH).write_text("", encoding="utf-8")
        app_module.available_grades = lambda: list(app_module.SUPPORTED_GRADES)
        app_module.SEEN_SUBJECTS_RAW_BY_GRADE = {
            grade: ["SAME RAW KEY"]
            for grade in app_module.SUPPORTED_GRADES
        }
        app_module.fetch_schoolyear_subjects = lambda grade: ["SAME RAW KEY"]
        app_module._subject_catalog_cache = {}
        app_module._subject_catalog_cached_at = {}
        app_module._subject_catalog_live = {}
        with app_module.app.app_context():
            app_module._set_settings({
                "imageBannerData": "",
                "imageBannerEnabled": "0",
                "imageBannerAlt": "Schulbanner",
                "imageBannerUpdatedAt": "0",
            })
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.COURSE_MAP_PATHS = self.old_course_paths
        app_module.LEGACY_COURSE_MAP_PATH = self.old_legacy_path
        app_module.ROOM_MAP_PATH = self.old_room_path
        app_module.available_grades = self.old_available_grades
        app_module.SEEN_SUBJECTS_RAW_BY_GRADE = self.old_seen_by_grade
        app_module.fetch_schoolyear_subjects = self.old_fetch_schoolyear_subjects
        app_module._subject_catalog_cache = self.old_subject_catalog_cache
        app_module._subject_catalog_cached_at = self.old_subject_catalog_cached_at
        app_module._subject_catalog_live = self.old_subject_catalog_live
        self.temp_dir.cleanup()

    def _admin_login(self):
        with self.client.session_transaction() as session:
            session["admin_ok"] = True

    @staticmethod
    def _banner_data_url(width=1600, height=400):
        sof_payload = (
            b"\x08"
            + height.to_bytes(2, "big")
            + width.to_bytes(2, "big")
            + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        )
        image = b"\xff\xd8\xff\xc0" + (17).to_bytes(2, "big") + sof_payload + b"\xff\xd9"
        encoded = base64.b64encode(image).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}", image

    def test_banner_admin_api_requires_login_and_exact_dimensions(self):
        data_url, _ = self._banner_data_url()
        unauthorized = self.client.post(
            "/api/admin/banner-image",
            json={"imageData": data_url, "enabled": True},
        )
        self.assertEqual(unauthorized.status_code, 401)

        self._admin_login()
        wrong_size, _ = self._banner_data_url(width=1599)
        invalid = self.client.post(
            "/api/admin/banner-image",
            json={"imageData": wrong_size, "enabled": True},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.get_json()["error"], "banner_image_wrong_dimensions")

    def test_banner_save_display_backup_and_delete(self):
        self._admin_login()
        data_url, image_bytes = self._banner_data_url()
        with patch.object(app_module, "_maybe_send_backup"):
            saved = self.client.post(
                "/api/admin/banner-image",
                json={
                    "imageData": data_url,
                    "enabled": True,
                    "alt": "Schulfest",
                },
            )
        self.assertEqual(saved.status_code, 200)
        metadata = saved.get_json()["image_banner"]
        self.assertTrue(metadata["enabled"])
        self.assertEqual(metadata["alt"], "Schulfest")
        self.assertEqual((metadata["width"], metadata["height"]), (1600, 400))

        admin_state = self.client.get("/api/admin/state").get_json()
        self.assertNotIn("imageBannerData", admin_state["settings"])
        self.assertEqual(admin_state["image_banner"]["alt"], "Schulfest")

        page = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="page-image-banner"', page)
        self.assertIn('alt="Schulfest"', page)

        image_response = self.client.get("/api/banner-image")
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response.data, image_bytes)
        self.assertEqual(image_response.mimetype, "image/jpeg")
        self.assertIn("immutable", image_response.headers["Cache-Control"])

        with app_module.app.app_context():
            settings = app_module._build_backup_payload()["database"]["settings"]
        self.assertEqual(settings["imageBannerData"], data_url)
        self.assertEqual(settings["imageBannerEnabled"], "1")

        with patch.object(app_module, "_maybe_send_backup"):
            deleted = self.client.delete("/api/admin/banner-image")
        self.assertEqual(deleted.status_code, 200)
        self.assertNotIn('id="page-image-banner"', self.client.get("/").get_data(as_text=True))
        self.assertEqual(self.client.get("/api/banner-image").status_code, 404)

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
        self.assertIn(
            '<details class="card collapsible-card" id="users">',
            admin_html,
        )
        for grade in ("ef", "q1", "q2"):
            self.assertIn(
                f'id="only-unmapped-sub-{grade}" checked',
                admin_html,
            )

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
        app_module.fetch_schoolyear_subjects = lambda grade: [f"{grade} ONLY"]
        app_module._subject_catalog_cache.clear()
        app_module._subject_catalog_cached_at.clear()
        app_module._subject_catalog_live.clear()

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

    def test_live_catalog_removes_stale_mapping_keys_from_api_and_storage(self):
        Path(self.paths["EF"]).write_text(
            "same raw key=Current EF name\nold ef course=Old EF name\n",
            encoding="utf-8",
        )
        app_module.fetch_schoolyear_subjects = lambda grade: ["SAME RAW KEY"]
        app_module._subject_catalog_cache.clear()
        app_module._subject_catalog_cached_at.clear()
        app_module._subject_catalog_live.clear()

        payload = self.client.get("/api/mappings").get_json()

        self.assertEqual(payload["schoolyear"], app_module._current_schoolyear_label())
        self.assertEqual(
            payload["coursesByGrade"]["EF"],
            {"same raw key": "Current EF name"},
        )
        self.assertEqual(
            app_module._course_map_normalized_for_grade("EF"),
            {"same raw key": "Current EF name"},
        )

    def test_admin_cannot_restore_a_mapping_for_an_inactive_course(self):
        self._admin_login()
        response = self.client.post(
            "/api/admin/save",
            json={
                "courses_ef": {
                    "same raw key": "Renamed current EF",
                    "old ef course": "Must not return",
                }
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            app_module._course_map_normalized_for_grade("EF"),
            {"same raw key": "Renamed current EF"},
        )

    def test_admin_does_not_prune_mappings_when_catalog_is_unavailable(self):
        self._admin_login()
        original = app_module._course_map_normalized_for_grade("EF")
        app_module.fetch_schoolyear_subjects = Mock(side_effect=RuntimeError("offline"))
        app_module._subject_catalog_cache.clear()
        app_module._subject_catalog_cached_at.clear()
        app_module._subject_catalog_live.clear()

        with patch.object(app_module, "_load_raw_subjects_for_grade", return_value=[]):
            response = self.client.post(
                "/api/admin/save",
                json={"courses_ef": {"same raw key": "Should not be written"}},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(app_module._course_map_normalized_for_grade("EF"), original)

    def test_course_options_ignore_stale_seen_subjects(self):
        app_module.SEEN_SUBJECTS_RAW_BY_GRADE = {
            grade: [f"STALE {grade}"]
            for grade in app_module.SUPPORTED_GRADES
        }
        app_module.fetch_schoolyear_subjects = lambda grade: [f"CURRENT {grade}"]
        app_module._subject_catalog_cache.clear()
        app_module._subject_catalog_cached_at.clear()
        app_module._subject_catalog_live.clear()

        payload = self.client.get("/api/courses").get_json()
        keys = {item["key"] for item in payload["courses"]}

        for grade in app_module.SUPPORTED_GRADES:
            self.assertIn(f"{grade}:current {grade.lower()}", keys)
            self.assertNotIn(f"{grade}:stale {grade.lower()}", keys)

    def test_course_catalog_falls_back_per_grade_without_seen_history(self):
        app_module.SEEN_SUBJECTS_RAW_BY_GRADE = {
            grade: [f"STALE {grade}"]
            for grade in app_module.SUPPORTED_GRADES
        }
        app_module.fetch_schoolyear_subjects = Mock(side_effect=RuntimeError("offline"))
        app_module._subject_catalog_cache.clear()
        app_module._subject_catalog_cached_at.clear()
        app_module._subject_catalog_live.clear()

        with patch.object(
            app_module,
            "_load_raw_subjects_for_grade",
            side_effect=lambda grade: [f"FALLBACK {grade}"],
        ):
            self.assertEqual(
                app_module._current_subjects_for_grade("Q2"),
                ["FALLBACK Q2"],
            )

    def test_static_course_catalog_must_match_current_schoolyear(self):
        root = Path(self.temp_dir.name)
        valid_year = app_module._current_schoolyear_label()
        stale_year = "2025/2026" if valid_year != "2025/2026" else "2024/2025"
        catalog = root / "subjects_raw_q2.txt"

        with patch.object(app_module, "DATA_DIR", str(root)):
            catalog.write_text(
                f"# schoolyear: {stale_year}\nOld course\n",
                encoding="utf-8",
            )
            self.assertEqual(app_module._load_raw_subjects_for_grade("Q2"), [])

            catalog.write_text(
                f"# schoolyear: {valid_year}\nCurrent course\n",
                encoding="utf-8",
            )
            self.assertEqual(
                app_module._load_raw_subjects_for_grade("Q2"),
                ["Current course"],
            )

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

    def test_schoolyear_catalog_includes_every_subject_reference(self):
        client = untis_client.UntisClient(
            "https://example.invalid/WebUntis/jsonrpc.do",
            "school",
            "user",
            "pass",
            12,
            1,
        )

        def rpc(method, _params):
            if method == "getCurrentSchoolyear":
                return {"startDate": 20260831, "endDate": 20270718}
            if method == "getSubjects":
                return [
                    {"id": 1, "longName": "Course A"},
                    {"id": 2, "longName": "Course B"},
                ]
            if method == "getTimetable":
                return [
                    {"su": [{"id": 2}, {"id": 1}]},
                    {"su": [{"id": 2}]},
                ]
            raise AssertionError(method)

        with patch.object(client, "_rpc_auth", side_effect=rpc) as rpc_mock:
            self.assertEqual(
                client.fetch_schoolyear_subjects(element_id=99, element_type=1),
                ["Course A", "Course B"],
            )
        timetable_call = next(
            call for call in rpc_mock.call_args_list
            if call.args[0] == "getTimetable"
        )
        self.assertEqual(
            timetable_call.args[1]["options"]["element"],
            {"id": 99, "type": 1},
        )

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

    def test_backup_uses_configured_token_and_excludes_plaintext_passwords(self):
        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute("DELETE FROM users")
            db.execute(
                "INSERT INTO users (username, password_hash, password_plain, profile_json) VALUES (?, ?, ?, ?)",
                ("secure-backup-user", "password-hash", "must-not-leak", "{}"),
            )
            db.commit()
            payload = app_module._build_backup_payload()
            db.execute("DELETE FROM users")
            db.commit()

        backed_up_user = payload["database"]["users"][0]
        self.assertNotIn("password_plain", backed_up_user)
        self.assertEqual(backed_up_user["password_hash"], "password-hash")

        remote_response = Mock()
        remote_response.content = b'{"database":{"users":[]}}'
        remote_response.raise_for_status.return_value = None
        remote_response.json.return_value = {"database": {"users": []}}
        post_response = Mock()
        post_response.content = b'{"ok":true}'
        post_response.raise_for_status.return_value = None
        post_response.json.return_value = {"ok": True}
        with (
            patch.object(app_module, "BACKUP_WEBHOOK_URL", "https://backup.invalid"),
            patch.object(app_module, "BACKUP_WEBHOOK_TOKEN", "shared-test-token"),
            patch.object(app_module, "AUTO_RESTORE_URL", "https://restore.invalid"),
            patch.object(app_module.requests, "get", return_value=remote_response),
            patch.object(app_module.requests, "post", return_value=post_response) as backup_post,
        ):
            sent = app_module._maybe_send_backup("profile_update", payload)

        self.assertTrue(sent)
        request_kwargs = backup_post.call_args.kwargs
        self.assertEqual(request_kwargs["params"], {"token": "shared-test-token"})
        self.assertEqual(request_kwargs["headers"]["X-Backup-Token"], "shared-test-token")

    def test_empty_remote_response_blocks_restore_and_backup(self):
        empty_response = Mock()
        empty_response.content = b""
        empty_response.raise_for_status.return_value = None
        payload = {"database": {"users": [{}]}}
        with (
            patch.object(app_module, "BACKUP_WEBHOOK_URL", "https://backup.invalid"),
            patch.object(app_module, "AUTO_RESTORE_URL", "https://restore.invalid"),
            patch.object(app_module.requests, "get", return_value=empty_response),
            patch.object(app_module.requests, "post") as backup_post,
        ):
            sent = app_module._maybe_send_backup("profile_update", payload)

        self.assertFalse(sent)
        backup_post.assert_not_called()

    def test_profile_keeps_mixed_explicit_grades_for_client_cleanup(self):
        profile = app_module._normalise_profile({
            "grade": "Q2",
            "courses": [
                "EF:SAME RAW KEY",
                "Q1:SAME RAW KEY",
                "Q2:SAME RAW KEY",
            ],
        })
        self.assertEqual(profile["grade"], "Q2")
        self.assertEqual(profile["courses"], [
            "EF:same raw key",
            "Q1:same raw key",
            "Q2:same raw key",
        ])

    def test_profile_grade_scopes_only_unprefixed_legacy_courses(self):
        profile = app_module._normalise_profile({
            "grade": "Q2",
            "courses": ["SAME RAW KEY"],
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
