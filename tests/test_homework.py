import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import homework
from test_grade_isolation import app_module as app

TZ = ZoneInfo('Europe/Berlin')


def task(**changes):
    return {"id": "test-task", "text": "S. 42, Nr. 3", "course": "Q1:ekeg8", "mode": "next",
            "date": "", "anchor": "2026-09-14T10:11:00+02:00", "done": False,
            "remind": True, "due": None, "resolution": "pending", **changes}


def lesson(day, start='09:10', status='normal', **changes):
    return {"id": day + start, "date": day, "start": start, "end": "10:10",
            "subject": "EKEG8", "grade": "Q1", "status": status, **changes}


class HomeworkDeadlinesTests(unittest.TestCase):
    def solve(self, item, lessons):
        return homework.resolve(item, lambda week: lessons, lambda row: row['grade'] == 'Q1' and row['subject'] == 'EKEG8', TZ)

    def test_next_lesson_skips_current_and_multiple_cancellations(self):
        result = self.solve(task(), [lesson('2026-09-14'), lesson('2026-09-16', status='entfaellt'),
                                    lesson('2026-09-18', status='entfaellt'), lesson('2026-09-21')])
        self.assertEqual(result['due']['date'], '2026-09-21')

    def test_known_cancellation_moves_deadline_and_restays_overdue(self):
        initial = self.solve(task(), [lesson('2026-09-16')])
        changed = self.solve(initial, [lesson('2026-09-16', status='entfaellt'), lesson('2026-09-21')])
        self.assertEqual(changed['due']['date'], '2026-09-21')
        unchanged = self.solve(changed, [lesson('2026-09-21'), lesson('2026-09-23')])
        self.assertEqual(unchanged['due']['date'], '2026-09-21')

    def test_repeated_reads_never_advance_uncancelled_task(self):
        initial = self.solve(task(), [lesson('2026-09-16'), lesson('2026-09-18')])
        for _ in range(4):
            self.assertEqual(self.solve(initial, [lesson('2026-09-16'), lesson('2026-09-18')]), initial)

    def test_missing_known_lesson_is_uncertain_not_advanced(self):
        initial = self.solve(task(), [lesson('2026-09-16')])
        result = self.solve(initial, [lesson('2026-09-18')])
        self.assertEqual(result['resolution'], 'unavailable')
        self.assertEqual(result['due'], initial['due'])

    def test_network_failure_prevents_skipping_unknown_weeks(self):
        loader = Mock(side_effect=RuntimeError('offline'))
        result = homework.resolve(task(), loader, lambda row: True, TZ)
        self.assertEqual(result['resolution'], 'unavailable')
        self.assertIsNone(result['due'])
        self.assertEqual(loader.call_count, 1)

    def test_holidays_and_year_boundary_are_skipped_without_guessing(self):
        result = self.solve(task(anchor='2026-12-18T18:00:00+01:00'), [lesson('2027-01-06')])
        self.assertEqual(result['due']['date'], '2027-01-06')

    def test_search_is_bounded_when_no_next_class_is_published(self):
        loader = Mock(return_value=[])
        result = homework.resolve(task(), loader, lambda row: True, TZ)
        self.assertEqual(result['resolution'], 'pending')
        self.assertEqual(loader.call_count, 8)

    def test_same_course_in_other_grades_never_matches(self):
        result = self.solve(task(), [lesson('2026-09-15', grade='EF'), lesson('2026-09-16', grade='Q2'), lesson('2026-09-18')])
        self.assertEqual(result['due']['date'], '2026-09-18')

    def test_cancelled_duplicate_wins_over_normal_record(self):
        result = self.solve(task(), [lesson('2026-09-16'), lesson('2026-09-16', status='entfaellt'), lesson('2026-09-18')])
        self.assertEqual(result['due']['date'], '2026-09-18')

    def test_fixed_dates_do_not_move_on_cancellation(self):
        loader = Mock(side_effect=AssertionError('must not fetch'))
        result = homework.resolve(task(mode='date', date='2026-09-16'), loader, lambda row: True, TZ)
        self.assertEqual(result['due']['date'], '2026-09-16')
        loader.assert_not_called()

    def test_completed_tasks_do_not_reschedule(self):
        loader = Mock()
        initial = task(done=True)
        self.assertEqual(homework.resolve(initial, loader, lambda row: True, TZ), initial)
        loader.assert_not_called()

    def test_reminder_custom_time_and_before_lesson_clamp(self):
        item = self.solve(task(), [lesson('2026-09-16')])
        when, deadline = homework.reminder_at(item, homework.settings({'reminderDays': 2, 'reminderTime': '17:30'}), TZ)
        self.assertEqual(when.isoformat(), '2026-09-14T17:30:00+02:00')
        same_day, _ = homework.reminder_at(item, homework.settings({'reminderDays': 0, 'reminderTime': '18:00'}), TZ)
        self.assertEqual(same_day, deadline - timedelta(minutes=15))

    def test_unconfirmed_completed_and_muted_tasks_never_notify(self):
        initial = self.solve(task(), [lesson('2026-09-16')])
        for change in ({'done': True}, {'remind': False}, {'resolution': 'unavailable'}, {'resolution': 'pending'}):
            self.assertIsNone(homework.reminder_at({**initial, **change}, homework.settings({}), TZ))
        self.assertIsNone(homework.reminder_at({**initial, 'due': {'date': 'invalid'}}, homework.settings({}), TZ))

    def test_validation_rejects_ambiguous_course_bad_date_and_anchor(self):
        for change in ({'course': 'ekeg8'}, {'mode': 'date', 'date': '2026-02-31'}, {'anchor': 'yesterday'}, {'text': ''}):
            self.assertEqual(homework.tasks([task(**change)]), [])
        self.assertEqual(len(homework.tasks([task(), task()])), 1)
        prefs = homework.settings({'markerColor': '<script>', 'markerStyle': 'invalid', 'reminderTime': '99:10', 'reminderDays': 99})
        self.assertEqual(prefs['markerColor'], '#14b8a6')
        self.assertEqual(prefs['reminderTime'], '18:00')
        self.assertEqual(prefs['reminderDays'], 7)


class HomeworkAPITests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.enterContext(patch.object(app, 'DB_PATH', str(Path(root.name) / 'homework.db')))
        self.backup = self.enterContext(patch.object(app, '_maybe_send_backup'))
        self.enterContext(patch.object(app, '_course_map_normalized_for_grade', return_value={'ekeg8': 'EK G8'}))
        self.weeks = self.enterContext(patch.object(app, '_homework_week', return_value=[lesson('2026-09-16')]))
        with app.app.app_context():
            app.init_db()
            db = app.get_db()
            db.execute('INSERT INTO users (id, username, password_hash, profile_json) VALUES (1, ?, ?, ?)',
                       ('test-user', 'not-a-password', json.dumps({'grade': 'Q1', 'courses': ['Q1:ekeg8']})))
            db.commit()
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = 1

    def install(self, item=None, prefs=None):
        with app.app.app_context():
            profile = app._load_profile_for_user(app._load_user(1))
            profile['homework'] = [item or task()]
            profile['homeworkSettings'] = homework.settings(prefs or {})
            app._save_profile(1, profile)

    def profile(self):
        with app.app.app_context():
            return app._load_profile_for_user(app._load_user(1))

    def test_authentication_required(self):
        client = app.app.test_client()
        for method, path in [('get', '/api/homework'), ('post', '/api/homework'), ('patch', '/api/homework/x'), ('delete', '/api/homework/x'), ('put', '/api/homework-settings')]:
            self.assertEqual(getattr(client, method)(path, json={}).status_code, 401)

    def test_crud_roundtrip_validation_and_server_owned_anchor(self):
        result = self.client.post('/api/homework', json={'course': 'Q1:ekeg8', 'text': 'Seite 9', 'mode': 'date', 'date': '2026-09-18', 'anchor': '1900-01-01'})
        self.assertEqual(result.status_code, 200)
        item = result.json['homework'][0]
        self.assertNotEqual(item['anchor'], '1900-01-01')
        identifier = item['id']
        self.assertEqual(self.client.patch('/api/homework/' + identifier, json={'text': 'Seite 10', 'done': True}).status_code, 200)
        self.assertTrue(self.profile()['homework'][0]['done'])
        self.assertEqual(self.client.delete('/api/homework/' + identifier).status_code, 200)
        self.assertEqual(self.profile()['homework'], [])

    def test_committed_save_returns_while_remote_backup_is_blocked(self):
        entered, release = threading.Event(), threading.Event()
        workers = []
        thread_type = threading.Thread
        def thread_factory(**kwargs):
            worker = thread_type(**kwargs)
            workers.append(worker)
            return worker
        def slow_backup(*args):
            entered.set()
            release.wait(5)
        self.backup.side_effect = slow_backup
        with patch.object(app, 'BACKUP_WEBHOOK_URL', 'https://example.invalid/backup'), \
                patch.object(app.threading, 'Thread', side_effect=thread_factory):
            try:
                result = self.client.post('/api/homework', json={
                    'course': 'Q1:ekeg8', 'text': 'Diagramm', 'mode': 'next'})
                self.assertEqual(result.status_code, 200)
                self.assertTrue(entered.wait(2))
                self.assertFalse(release.is_set())
                self.assertEqual(self.profile()['homework'][0]['text'], 'Diagramm')
                self.assertEqual(self.client.put('/api/homework-settings', json={'reminders': True}).status_code, 200)
                self.assertEqual(len(workers), 1)
            finally:
                release.set()
                for worker in workers:
                    worker.join(5)
            self.assertFalse(app._homework_backup_running)
            self.assertEqual(self.backup.call_count, 2)

    def test_busy_reader_leaves_profile_unchanged_then_save_succeeds_after_release(self):
        reader = sqlite3.connect(app.DB_PATH, timeout=0)
        try:
            reader.execute('BEGIN')
            reader.execute('SELECT profile_json FROM users').fetchall()
            with patch.object(app, 'SQLITE_BUSY_TIMEOUT_MS', 50):
                result = self.client.post('/api/homework', json={
                    'course': 'Q1:ekeg8', 'text': 'Diagramm', 'mode': 'next'})
                self.assertEqual(result.status_code, 503)
                self.assertEqual(self.client.put('/api/homework-settings', json={
                    'reminders': True, 'reminderTime': '17:00'}).status_code, 503)
            self.assertEqual(self.profile()['homework'], [])
            reader.rollback()
            self.assertEqual(self.client.post('/api/homework', json={
                'course': 'Q1:ekeg8', 'text': 'Diagramm', 'mode': 'next'}).status_code, 200)
            self.assertEqual(self.client.put('/api/homework-settings', json={
                'reminders': True, 'reminderTime': '17:00'}).status_code, 200)
            profile = self.profile()
            self.assertEqual(profile['homework'][0]['text'], 'Diagramm')
            self.assertEqual(profile['homeworkSettings']['reminderTime'], '17:00')
        finally:
            reader.rollback()
            reader.close()

    def test_startup_converts_existing_wal_without_losing_homework(self):
        self.install()
        with sqlite3.connect(app.DB_PATH) as connection:
            self.assertEqual(connection.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        connection.close()
        with app.app.app_context():
            app.init_db()
            self.assertEqual(app.get_db().execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        self.assertEqual(self.profile()['homework'], [task()])

    def test_unselected_courses_and_other_users_ids_are_rejected(self):
        for course in ('EF:ekeg8', 'Q2:ekeg8', 'Q1:other'):
            self.assertEqual(self.client.post('/api/homework', json={'course': course, 'text': 'test', 'mode': 'next'}).status_code, 400)
        self.assertEqual(self.client.patch('/api/homework/not-mine', json={'text': 'changed'}).status_code, 404)

    def test_old_profile_client_and_preference_writes_preserve_homework(self):
        self.install(prefs={'markerColor': '#ef4444'})
        self.client.put('/api/profile', json={'grade': 'Q1', 'courses': ['Q1:ekeg8'], 'homework': [], 'homeworkSettings': {}})
        self.client.put('/api/notifications/preferences', json={'enabled': False})
        self.assertEqual(self.profile()['homework'], [task()])
        self.assertEqual(self.profile()['homeworkSettings']['markerColor'], '#ef4444')

    def test_stale_profile_save_cannot_erase_concurrent_homework_update(self):
        self.install()
        original = self.profile()
        self.client.patch('/api/homework/test-task', json={'text': 'new text'})
        with app.app.app_context():
            app._save_profile(1, original, preserve_homework=True)
        self.assertEqual(self.profile()['homework'][0]['text'], 'new text')

    def test_resolver_does_not_overwrite_task_edited_during_fetch(self):
        self.install()
        def fetching(*args):
            self.client.patch('/api/homework/test-task', json={'text': 'edited while loading'})
            return [lesson('2026-09-16')]
        self.weeks.side_effect = fetching
        self.client.get('/api/homework')
        self.assertEqual(self.profile()['homework'][0]['text'], 'edited while loading')

    def test_resolution_is_persisted_and_cancellation_reschedules(self):
        self.install()
        self.assertEqual(self.client.get('/api/homework').json['homework'][0]['due']['date'], '2026-09-16')
        self.weeks.return_value = [lesson('2026-09-16', status='entfaellt'), lesson('2026-09-18')]
        self.assertEqual(self.client.get('/api/homework').json['homework'][0]['due']['date'], '2026-09-18')

    def test_backups_contain_tasks_and_settings(self):
        self.install(prefs={'reminders': True})
        with app.app.app_context():
            backup = app._build_backup_payload()
        profile = backup['database']['users'][0]['profile']
        self.assertEqual(profile['homework'], [task()])
        self.assertTrue(profile['homeworkSettings']['reminders'])

    def test_backup_restore_roundtrip_keeps_homework_without_touching_real_files(self):
        self.install(prefs={'reminders': True, 'markerStyle': 'outline'})
        for name in ('_save_last_backup', '_course_map_write_for_grade', '_write_mapping_txt', '_save_seen_raw'):
            self.enterContext(patch.object(app, name))
        for name in ('SEEN_SUBJECTS_RAW', 'SEEN_SUBJECTS_RAW_BY_GRADE', 'SEEN_ROOMS_RAW', '_last_seen_flush'):
            self.enterContext(patch.object(app, name, copy.deepcopy(getattr(app, name))))
        with app.app.app_context():
            backup = app._build_backup_payload()
            app._apply_backup_payload(backup)
        self.assertEqual(self.profile()['homework'], [task()])
        self.assertEqual(self.profile()['homeworkSettings']['markerStyle'], 'outline')

    def test_homework_reminders_respect_master_task_and_course_opt_outs(self):
        with app.app.app_context():
            db = app.get_db()
            db.execute("INSERT INTO push_subscriptions (user_id,endpoint,p256dh,auth) VALUES (1,'https://push.example/a','test','test')")
            db.commit()
        for change in ({'done': True}, {'remind': False}, {'course': 'EF:ekeg8'}, {}):
            self.install(item=task(**change), prefs={'reminders': True})
            with app.app.app_context(), patch.object(app, '_send_push_to_user') as send:
                if not change:
                    profile = self.profile()
                    profile['notificationPreferences']['enabled'] = False
                    app._save_profile(1, profile)
                self.assertEqual(app._send_homework_reminders(datetime(2026,9,15,18,1,tzinfo=TZ)), 0)
                send.assert_not_called()

    def test_completion_during_remote_resolution_suppresses_reminder(self):
        self.install(prefs={'reminders': True})
        self.client.get('/api/homework')
        def fetch_and_complete(*args):
            app._edit_homework(1, lambda profile: profile['homework'][0].update(done=True))
            return [lesson('2026-09-16')]
        self.weeks.side_effect = fetch_and_complete
        with app.app.app_context():
            db = app.get_db()
            db.execute("INSERT INTO push_subscriptions (user_id,endpoint,p256dh,auth) VALUES (1,'https://push.example/a','test','test')")
            db.commit()
            with patch.object(app, '_send_push_to_user') as send:
                self.assertEqual(app._send_homework_reminders(datetime(2026,9,15,18,1,tzinfo=TZ)), 0)
                send.assert_not_called()

    def test_reminders_are_personal_deduplicated_and_shift_with_cancellation(self):
        self.install(prefs={'reminders': True, 'reminderTime': '18:00'})
        with app.app.app_context():
            db = app.get_db()
            db.execute("INSERT INTO push_subscriptions (user_id,endpoint,p256dh,auth) VALUES (1,'https://push.example/a','test','test')")
            db.commit()
            with patch.object(app, '_send_push_to_user', return_value={'sent': 1}) as send:
                now = datetime(2026, 9, 15, 18, 1, tzinfo=TZ)
                self.assertEqual(app._send_homework_reminders(now), 1)
                self.assertEqual(app._send_homework_reminders(now), 0)
                self.weeks.return_value = [lesson('2026-09-16', status='entfaellt'), lesson('2026-09-18')]
                self.assertEqual(app._send_homework_reminders(now), 0)
                self.assertEqual(app._send_homework_reminders(datetime(2026, 9, 17, 18, 1, tzinfo=TZ)), 1)
                self.assertEqual(send.call_count, 2)
                self.assertEqual(send.call_args.args[0], 1)
                self.assertIn('Hausaufgaben', send.call_args.args[1]['title'])


if __name__ == '__main__':
    unittest.main()
