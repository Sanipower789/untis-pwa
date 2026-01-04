import os, time, requests
from datetime import date, timedelta

# ---- Env helpers ----
def _require(name: str) -> str:
    val = os.getenv(name)
    if val is None or str(val).strip() == "":
        raise RuntimeError(f"{name} environment variable is required and must not be empty.")
    return val


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except Exception:
        return default


# Base / school are shared across all logins
BASE   = _require("UNTIS_BASE")       # e.g. https://.../WebUntis/jsonrpc.do
SCHOOL = _require("UNTIS_SCHOOL")

# Primary (EF) login
USER   = _require("UNTIS_USER")
PASS   = _require("UNTIS_PASS")
EID    = _int_env("UNTIS_ELEMENT_ID", 0)
ETYPE  = _int_env("UNTIS_ELEMENT_TYPE", 5)  # 5=student, 1=class, ...

# Optional second (Q1) login
USER_Q1  = os.getenv("UNTIS_USER_Q1")
PASS_Q1  = os.getenv("UNTIS_PASS_Q1")
EID_Q1   = _int_env("UNTIS_ELEMENT_ID_Q1", EID)
ETYPE_Q1 = _int_env("UNTIS_ELEMENT_TYPE_Q1", ETYPE)


class UntisClient:
    """Thin helper around the Untis JSON-RPC/REST APIs (per login)."""

    def __init__(
        self,
        base: str,
        school: str,
        user: str,
        password: str,
        element_id: int,
        element_type: int = 5,
        label: str | None = None,
    ):
        self.base = (base or "").strip()
        self.school = (school or "").strip()
        self.user = (user or "").strip()
        self.password = password or ""
        self.element_id = int(element_id)
        self.element_type = int(element_type)
        self.label = label or self.user
        if not self.base or not self.school or not self.user or not self.password:
            raise ValueError("UntisClient requires base, school, user and password.")
        self.session = requests.Session()
        self._sess_id: str | None = None
        self._sess_exp: float = 0.0  # epoch seconds when cached session should be considered stale

    # ----- small utils -----
    def _rest_base(self) -> str:
        """Derive the REST base (/WebUntis) from Untis JSON-RPC base."""
        if "/WebUntis" in self.base:
            return self.base.split("/WebUntis", 1)[0] + "/WebUntis"
        if self.base.endswith("/jsonrpc.do"):
            return self.base[:-len("/jsonrpc.do")]
        return self.base.rstrip("/")

    @staticmethod
    def _yyyymmdd(d: date) -> int:
        return d.year * 10000 + d.month * 100 + d.day

    @staticmethod
    def _hm(n: int) -> str:
        return f"{n // 100:02d}:{n % 100:02d}"

    @staticmethod
    def _hm_from_int(n) -> str:
        try:
            n = int(n)
            return f"{n // 100:02d}:{n % 100:02d}"
        except Exception:
            return ""

    @staticmethod
    def _is_not_authenticated(err: Exception) -> bool:
        s = str(err).lower()
        # cover both message and code (-8526) just in case
        return ("not authenticated" in s) or ("-8526" in s)

    @staticmethod
    def _status_from_item(x, note: str) -> str:
        """entfaellt / vertretung / aenderung / normal."""
        txt = (note or "").lower()
        code = str(x.get("code", "")).lower()
        cell = str(x.get("cellState", "")).lower()
        subst = (x.get("substText") or "").lower()

        if code in ("cancelled", "canceled", "cancel"):
            return "entfaellt"
        if cell in ("cancelled", "canceled"):
            return "entfaellt"
        if x.get("cancelled", False) is True:
            return "entfaellt"
        if "entf" in subst or "cancel" in subst:
            return "entfaellt"
        if "vert" in subst or "vertret" in txt:
            return "vertretung"
        if any(k in subst or k in txt for k in ("aender",)):
            return "aenderung"
        if "entfall" in txt or "cancel" in txt:
            return "entfaellt"
        return "normal"

    # ----- auth / rpc -----
    def _rpc(self, method, params=None, cookies=None):
        """Low-level JSON-RPC. Raises RuntimeError on WebUntis 'error' payloads."""
        r = self.session.post(
            self.base,
            params={"school": self.school},
            json={"id": "1", "method": method, "params": params or {}, "jsonrpc": "2.0"},
            cookies=cookies,
            timeout=25,
        )
        r.raise_for_status()
        j = r.json()
        if "error" in j:
            # j["error"] typically contains {"message": "...", "code": -8526}
            raise RuntimeError(f"RPC {method} -> {j['error']}")
        return j["result"]

    def _invalidate_session(self):
        self._sess_id = None
        self._sess_exp = 0.0

    def _login(self, refresh: bool = False):
        """Authenticate if needed; cache JSESSIONID for a short window."""
        now = time.time()
        if not refresh and self._sess_id and now < self._sess_exp:
            return {"JSESSIONID": self._sess_id}

        # force new login
        res = self._rpc("authenticate", {"user": self.user, "password": self.password, "client": "untis-pwa"})
        self._sess_id = res["sessionId"]
        # Keep the window conservative; Render free dynos can idle - the token may vanish earlier.
        self._sess_exp = now + 10 * 60  # ~10 minutes
        return {"JSESSIONID": self._sess_id}

    def _rpc_auth(self, method: str, params=None):
        """
        Authenticated RPC with auto re-login:
        - attempt with current/valid session
        - on 'not authenticated', invalidate, re-login and retry once
        """
        try:
            return self._rpc(method, params, cookies=self._login())
        except RuntimeError as e:
            if self._is_not_authenticated(e):
                self._invalidate_session()
                return self._rpc(method, params, cookies=self._login(refresh=True))
            raise

    # ----- public APIs -----
    def _rest_exams(self, start_date: date, end_date: date, exam_type_id: int = 0):
        """
        Fetch exams via the new WebUntis REST endpoint (/api/exams).
        Falls back to the caller if the endpoint is not reachable.
        """
        base = self._rest_base()
        url = base.rstrip("/") + "/api/exams"
        params = {
            "startDate": self._yyyymmdd(start_date),
            "endDate": self._yyyymmdd(end_date),
            "examTypeId": int(exam_type_id),
            "withGrades": "true",
        }
        # Pass element information when available; class logins use klasseId, student logins studentId.
        if self.element_type == 1:   # class
            params["klasseId"] = self.element_id
            params["studentId"] = -1
        elif self.element_type == 5:  # student
            params["studentId"] = self.element_id
            params["klasseId"] = -1
        else:
            params["studentId"] = -1
            params["klasseId"] = -1

        headers = {
            "User-Agent": "untis-pwa/1.0",
            "Referer": base + "/",
        }
        resp = self.session.get(url, params=params, cookies=self._login(), headers=headers, timeout=25)
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"REST exams: invalid JSON ({exc})")

        if isinstance(payload, dict):
            if payload.get("errors"):
                raise RuntimeError(f"REST exams error: {payload.get('errors')}")
            data_section = payload.get("data") if isinstance(payload.get("data"), dict) else None
            exams = None
            if data_section and isinstance(data_section.get("exams"), list):
                exams = data_section.get("exams")
            elif isinstance(payload.get("exams"), list):
                exams = payload.get("exams")
            if exams is not None:
                return exams
            if payload.get("message"):
                raise RuntimeError(f"REST exams error: {payload.get('message')}")
        raise RuntimeError("REST exams: unexpected response")

    def fetch_week(self, week_start: date):
        """Fetch timetable Mon-Sun starting at week_start (Monday recommended)."""
        s, e = week_start, week_start + timedelta(days=7)

        # raw timetable (with auto re-login)
        tt = self._rpc_auth(
            "getTimetable",
            {
                "options": {
                    "element": {"id": self.element_id, "type": self.element_type},
                    "startDate": self._yyyymmdd(s),
                    "endDate": self._yyyymmdd(e),
                }
            },
        )

        # Lookups (also protected by auto re-login)
        try:
            teachers = {
                t["id"]: (t.get("longName") or t.get("name") or "")
                for t in self._rpc_auth("getTeachers", {})
            }
        except Exception:
            teachers = {}

        try:
            subjects = {
                s["id"]: (s.get("longName") or s.get("name") or "")
                for s in self._rpc_auth("getSubjects", {})
            }
        except Exception:
            subjects = {}

        try:
            rooms = {
                r["id"]: (r.get("longName") or r.get("name") or "")
                for r in self._rpc_auth("getRooms", {})
            }
        except Exception:
            rooms = {}

        lessons = []
        for x in tt:
            d = str(x.get("date", ""))
            date_iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d

            subj_id = (x.get("su") or [{}])[0].get("id")
            teach_id = (x.get("te") or [{}])[0].get("id")
            room_id = (x.get("ro") or [{}])[0].get("id")

            subj = subjects.get(subj_id, "")
            teach = teachers.get(teach_id, "")
            room = rooms.get(room_id, "")

            note = (x.get("lstext") or x.get("substitutionText") or x.get("periodText") or "").strip()
            status = self._status_from_item(x, note)

            # Choose a display subject for specials (like projects/exams) else normal subject
            lesson_code = str(x.get("lessonCode") or "")
            activity_type = str(x.get("activityType") or "")
            lesson_text = str(x.get("lessonText") or "")
            is_special = (
                lesson_code.upper() == "UNTIS_ADDITIONAL"
                or (activity_type and activity_type.lower() != "unterricht")
                or (not subj and (note or lesson_text))
            )
            display_subject = (note or lesson_text or subj or "Sondertermin") if is_special else (subj or "")

            lessons.append(
                {
                    "id": f"{x.get('id')}-{x.get('date')}-{x.get('startTime')}",
                    "date": date_iso,
                    "start": self._hm(x.get("startTime", 0)),
                    "end": self._hm(x.get("endTime", 0)),
                    # IMPORTANT: Leave 'subject' as the best live guess; mapping happens on the frontend
                    "subject": display_subject,
                    "subject_original": subj or "",
                    "teacher": teach or "",
                    "room": room or "",
                    "status": status,
                    "note": note,
                    "special": bool(is_special),
                }
            )

        return lessons

    def fetch_exams(self, start_date: date, end_date: date, exam_type_id: int = 0):
        """
        Fetch exams in a date range.

        WebUntis expects YYYYMMDD ints and an optional examTypeId (0 = all types).
        """
        first_error: Exception | None = None
        # Try the new REST endpoint first (post-update WebUntis). If it fails, fall back to JSON-RPC.
        try:
            return self._rest_exams(start_date, end_date, exam_type_id)
        except Exception as exc:  # keep the error in case RPC fails too
            first_error = exc

        payload = {
            "startDate": self._yyyymmdd(start_date),
            "endDate": self._yyyymmdd(end_date),
            "examTypeId": int(exam_type_id),
        }
        try:
            return self._rpc_auth("getExams", payload)
        except Exception:
            if first_error:
                raise first_error
            raise

    def fetch_subject_map(self) -> dict[int, str]:
        try:
            return {
                s["id"]: (s.get("longName") or s.get("name") or "")
                for s in self._rpc_auth("getSubjects", {})
            }
        except Exception:
            return {}

    def fetch_class_map(self) -> dict[int, str]:
        try:
            return {
                c["id"]: (c.get("longName") or c.get("name") or "")
                for c in self._rpc_auth("getKlassen", {})
            }
        except Exception:
            return {}

    def fetch_teacher_map(self) -> dict[int, str]:
        try:
            return {
                t["id"]: (t.get("longName") or t.get("name") or "")
                for t in self._rpc_auth("getTeachers", {})
            }
        except Exception:
            return {}


# ---- Instantiate clients ----
CLIENTS: dict[str, UntisClient] = {}

CLIENTS["EF"] = UntisClient(BASE, SCHOOL, USER, PASS, EID, ETYPE, label="EF")

if USER_Q1 and PASS_Q1:
    try:
        CLIENTS["Q1"] = UntisClient(BASE, SCHOOL, USER_Q1, PASS_Q1, EID_Q1, ETYPE_Q1, label="Q1")
    except Exception:
        # If Q1 creds are misconfigured, continue without blocking EF
        CLIENTS.pop("Q1", None)


def available_grades() -> list[str]:
    """Return the configured grade labels (keys in CLIENTS)."""
    return sorted(CLIENTS.keys())


def _pick_client(grade: str | None = None) -> UntisClient:
    if not CLIENTS:
        raise RuntimeError("No Untis clients configured.")
    if grade:
        key = str(grade).strip().upper()
        if key in CLIENTS:
            return CLIENTS[key]
    # default to EF if present, else first available
    if "EF" in CLIENTS:
        return CLIENTS["EF"]
    return next(iter(CLIENTS.values()))


# ---- Legacy-compatible module-level helpers ----
def fetch_week(week_start: date, grade: str | None = None):
    return _pick_client(grade).fetch_week(week_start)


def fetch_week_all(week_start: date) -> dict[str, list[dict]]:
    """Fetch timetables for all configured grades."""
    out: dict[str, list[dict]] = {}
    for label, client in CLIENTS.items():
        out[label] = client.fetch_week(week_start)
    return out


def fetch_exams(start_date: date, end_date: date, exam_type_id: int = 0, grade: str | None = None):
    return _pick_client(grade).fetch_exams(start_date, end_date, exam_type_id)


def fetch_subject_map(grade: str | None = None) -> dict[int, str]:
    return _pick_client(grade).fetch_subject_map()


def fetch_class_map(grade: str | None = None) -> dict[int, str]:
    return _pick_client(grade).fetch_class_map()


def fetch_teacher_map(grade: str | None = None) -> dict[int, str]:
    return _pick_client(grade).fetch_teacher_map()
