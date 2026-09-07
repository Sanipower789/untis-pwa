import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_grade_isolation import app_module as app


class AppSafetyTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        root = Path(self.root.name)
        for name, value in {
            "DB_PATH": str(root / "test.db"),
            "LAST_BACKUP_PATH": str(root / "backup.json"),
            "LAST_GOOD_PATH": str(root / "timetable.json"),
            "COURSE_MAP_PATHS": {g: str(root / (g + ".txt")) for g in app.SUPPORTED_GRADES},
            "ROOM_MAP_PATH": str(root / "rooms.txt"),
            "SEEN_SUB_RAW_PATH": str(root / "subjects.json"),
            "SEEN_SUB_RAW_PATHS": {g: str(root / (g + ".json")) for g in app.SUPPORTED_GRADES},
            "SEEN_ROOM_RAW_PATH": str(root / "rooms.json"),
            "_last_weekkey_payload": {}, "_last_weekkey_ts": {},
            "_last_exam_payload": {}, "_last_exam_key_ts": {},
            "LAST_GOOD": None,
        }.items():
            self.enterContext(patch.object(app, name, value))
        app.init_db()
        self.client = app.app.test_client()
        response = self.client.post('/api/auth/register', json={"username": "audit-user", "password": "test-password"})
        self.assertEqual(response.status_code, 201)

    def login_admin(self):
        response = self.client.post('/admin/login', data={"token": app.ADMIN_TOKEN})
        self.assertEqual(response.status_code, 302)

    def backup(self):
        with app.app.app_context():
            return app._build_backup_payload()

    def test_auth_profile_roundtrip_and_cookie_security(self):
        profile = {"grade": "Q1", "courses": ["Q1:Biology"], "name": "Audit", "klausuren": [], "colors": {}}
        self.assertEqual(self.client.put('/api/profile', json=profile).status_code, 200)
        self.client.post('/api/auth/logout')
        self.assertEqual(self.client.get('/api/profile').status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login', json={"username": "audit-user", "password": "wrong"}).status_code, 401)
        result = self.client.post('/api/auth/login', json={"username": "AUDIT-USER", "password": "test-password"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()['profile']['courses'], ['Q1:biology'])
        cookie = result.headers.get('Set-Cookie', '')
        for flag in ('Secure', 'HttpOnly', 'SameSite=Lax'):
            self.assertIn(flag, cookie)

    def test_empty_course_selection_never_resurrects_backup_profile(self):
        payload = self.backup()
        payload['database']['users'][0]['profile'] = {'grade': 'EF', 'courses': ['EF:old course']}
        Path(app.LAST_BACKUP_PATH).write_text(json.dumps(payload), encoding='utf-8')
        self.client.put('/api/profile', json={'grade': 'Q2', 'courses': [], 'name': 'Current'})
        profile = self.client.get('/api/profile').get_json()['profile']
        self.assertEqual(profile['grade'], 'Q2')
        self.assertEqual(profile['courses'], [])
        self.assertEqual(profile['name'], 'Current')

    def test_non_object_json_is_rejected_without_server_errors(self):
        self.login_admin()
        for endpoint in ('/api/auth/register', '/api/auth/login', '/api/admin/save', '/api/admin/vacations', '/api/admin/banner-image'):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.post(endpoint, json=['invalid']).status_code, 400)
        for values in ({'username': 42, 'password': 'test'}, {'username': 'test', 'password': ['bad']}):
            self.assertEqual(self.client.post('/api/auth/register', json=values).status_code, 400)

    def test_invalid_profile_json_does_not_clear_saved_courses(self):
        self.client.put('/api/profile', json={'grade': 'Q1', 'courses': ['Q1:biology']})
        self.assertEqual(self.client.put('/api/profile', data='{', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.get('/api/profile').get_json()['profile']['courses'], ['Q1:biology'])

    def test_malformed_backup_cannot_delete_existing_accounts(self):
        good = self.backup()
        for users in ({}, [{}], [{'id': 1, 'username': 'broken'}]):
            bad = copy.deepcopy(good)
            bad['database']['users'] = users
            with self.subTest(users=users), app.app.app_context():
                with self.assertRaises(ValueError):
                    app._apply_backup_payload(bad)
                self.assertEqual(app._database_user_count(), 1)
                self.assertIsNotNone(app.get_db().execute("SELECT id FROM users WHERE username='audit-user'").fetchone())

    def test_backup_export_fails_closed_on_database_error(self):
        db = Mock()
        db.execute.side_effect = sqlite3.OperationalError('disk read failed')
        with app.app.app_context(), patch.object(app, 'get_db', return_value=db):
            with self.assertRaises(sqlite3.Error):
                app._build_backup_payload()

    def test_unrecognized_backup_acknowledgement_is_failure(self):
        response = Mock(content=b'unauthorized')
        response.json.side_effect = ValueError('not JSON')
        with patch.object(app, 'BACKUP_WEBHOOK_URL', 'https://backup.invalid'), patch.object(app, 'AUTO_RESTORE_URL', ''), patch.object(app.requests, 'post', return_value=response):
            self.assertFalse(app._maybe_send_backup('manual', self.backup()))

    def test_admin_exam_rejects_invalid_dates_times_and_grades(self):
        self.login_admin()
        good = {'subject': 'Math', 'date': '2026-09-09', 'start': '09:10', 'end': '10:10', 'grade': 'Q1'}
        for changes in ({'date': '2026-02-30'}, {'start': '25:10'}, {'end': '08:00'}, {'grade': 'Q9'}):
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post('/api/admin/exams', json={**good, **changes}).status_code, 400)
        self.assertEqual(self.client.post('/api/admin/exams', json=good).status_code, 200)

    def test_color_keys_and_personal_exam_grade_survive_sync(self):
        profile = app._normalise_profile({
            'grade': 'Q1', 'colors': {'subjects': {'Q1:biology': '#ff0000'}},
            'klausuren': [{'id': 'exam', 'subject': 'Q1:biology', 'grade': 'Q1', 'name': 'Test', 'date': '2026-09-09', 'periodStart': 2, 'periodEnd': 2}],
        })
        self.assertIn('Q1:biology', profile['colors']['subjects'])
        self.assertEqual(profile['klausuren'][0].get('grade'), 'Q1')

    def test_manual_exams_are_filtered_by_requested_grade(self):
        self.login_admin()
        for grade in app.SUPPORTED_GRADES:
            self.client.post('/api/admin/exams', json={'subject': 'Biology', 'date': '2026-09-09', 'start': '09:10', 'end': '10:10', 'grade': grade})
        with patch.object(app, 'fetch_exams', return_value=[]), patch.object(app, 'fetch_subject_map', return_value={}), patch.object(app, 'fetch_class_map', return_value={}), patch.object(app, 'fetch_teacher_map', return_value={}), patch.object(app, 'record_seen_rooms_from_exams'):
            result = self.client.get('/api/exams?start=2026-09-07&end=2026-09-13&grade=Q1').get_json()
        self.assertEqual([exam['grade'] for exam in result['exams']], ['Q1'])


if __name__ == '__main__':
    unittest.main()
