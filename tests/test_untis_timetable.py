import os
import unittest
from datetime import date, timedelta
from unittest.mock import Mock, patch


for key, value in {
    "UNTIS_BASE": "https://example.invalid/WebUntis/jsonrpc.do",
    "UNTIS_SCHOOL": "test-school",
    "UNTIS_USER": "test-user",
    "UNTIS_PASS": "test-pass",
}.items():
    os.environ.setdefault(key, value)

from untis_client import UntisClient


class TimetableWeekTests(unittest.TestCase):
    def test_rest_exams_retries_once_after_non_json_session_response(self):
        client = UntisClient(
            "https://example.invalid/WebUntis/jsonrpc.do",
            "school",
            "user",
            "pass",
            element_id=2,
            element_type=1,
            label="Q1",
        )
        self.addCleanup(client.session.close)

        invalid = Mock(
            status_code=200,
            headers={"Content-Type": "text/html"},
            text="<html>login required</html>",
        )
        invalid.json.side_effect = ValueError("Expecting value")
        invalid.raise_for_status.return_value = None
        valid = Mock(
            status_code=200,
            headers={"Content-Type": "application/json"},
            text='{"data":{"exams":[]}}',
        )
        valid.json.return_value = {"data": {"exams": []}}
        valid.raise_for_status.return_value = None

        with patch.object(client.session, "get", side_effect=[invalid, valid]) as get, \
             patch.object(
                 client,
                 "_login",
                 side_effect=[{"JSESSIONID": "old"}, {"JSESSIONID": "fresh"}],
             ) as login:
            self.assertEqual(
                client._rest_exams(date(2026, 9, 7), date(2026, 9, 13)),
                [],
            )

        self.assertEqual(get.call_count, 2)
        self.assertEqual(login.call_args_list[0].kwargs, {"refresh": False})
        self.assertEqual(login.call_args_list[1].kwargs, {"refresh": True})

    def test_exam_fallback_error_keeps_rest_and_rpc_context(self):
        client = UntisClient("https://example.invalid", "school", "user", "pass", 2, 1, "Q1")
        self.addCleanup(client.session.close)
        with patch.object(client, "_rest_exams", side_effect=RuntimeError("REST invalid JSON")), \
             patch.object(client, "_rpc_auth", side_effect=RuntimeError("no right for getExams()")):
            with self.assertRaisesRegex(RuntimeError, "REST invalid JSON.*no right for getExams"):
                client.fetch_exams(date(2026, 9, 7), date(2026, 9, 13))

    def test_week_ends_on_sunday_including_across_year_boundary(self):
        for start in (date(2026, 9, 7), date(2026, 12, 28)):
            for grade in ("EF", "Q1", "Q2"):
                with self.subTest(start=start, grade=grade):
                    client = UntisClient(
                        "https://example.invalid", "school", "user", "pass",
                        element_id=1, element_type=1, label=grade,
                    )
                    self.addCleanup(client.session.close)
                    with patch.object(client, "_rpc_auth", return_value=[]) as rpc:
                        self.assertEqual(client.fetch_week(start), [])
                    options = rpc.call_args_list[0].args[1]["options"]
                    self.assertEqual(options["startDate"], client._yyyymmdd(start))
                    self.assertEqual(options["endDate"], client._yyyymmdd(start + timedelta(days=6)))

    def test_next_monday_cannot_cover_q1_ekg8_cancellation(self):
        client = UntisClient("https://example.invalid", "school", "user", "pass", 2, 1, "Q1")
        self.addCleanup(client.session.close)
        raw = [
            {"id": 1, "date": 20260907, "startTime": 910, "endTime": 1010,
             "su": [{"id": 461}], "code": "cancelled"},
            {"id": 2, "date": 20260909, "startTime": 755, "endTime": 855,
             "su": [{"id": 461}]},
            {"id": 3, "date": 20260913, "startTime": 910, "endTime": 1010,
             "su": [{"id": 461}]},
            {"id": 4, "date": 20260914, "startTime": 910, "endTime": 1010,
             "su": [{"id": 461}]},
        ]

        def rpc(method, params):
            if method == "getTimetable":
                options = params["options"]
                # WebUntis includes both startDate and endDate.
                return [row for row in raw if options["startDate"] <= row["date"] <= options["endDate"]]
            if method == "getSubjects":
                return [{"id": 461, "name": "EKEG8", "longName": "GK ERDKUNDE bili"}]
            return []

        with patch.object(client, "_rpc_auth", side_effect=rpc):
            lessons = client.fetch_week(date(2026, 9, 7))
        self.assertEqual([row["date"] for row in lessons], ["2026-09-07", "2026-09-09", "2026-09-13"])
        self.assertEqual(lessons[0]["status"], "entfaellt")
        self.assertEqual(lessons[0]["subject_original"], "GK ERDKUNDE bili")
        self.assertEqual(lessons[1]["status"], "normal")


if __name__ == "__main__":
    unittest.main()
