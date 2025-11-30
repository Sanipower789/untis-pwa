import os, time, requests
from datetime import date, timedelta

# --- ENV / Secrets ---
BASE   = os.getenv("UNTIS_BASE")       # e.g. https://.../WebUntis/jsonrpc.do
SCHOOL = os.getenv("UNTIS_SCHOOL")
USER   = os.getenv("UNTIS_USER")
PASS   = os.getenv("UNTIS_PASS")
EID    = int(os.getenv("UNTIS_ELEMENT_ID", "0"))
ETYPE  = int(os.getenv("UNTIS_ELEMENT_TYPE", "5"))  # 5=student, 1=class, ...

if not all([BASE, SCHOOL, USER, PASS]):
    raise RuntimeError("UNTIS_* environment variables missing.")

session = requests.Session()
_SESS_ID: str | None = None
_SESS_EXP: float = 0.0  # epoch seconds when our cached session should be considered stale


# ---------------- core RPC ----------------
def _rpc(method, params=None, cookies=None):
    """Low-level JSON-RPC. Raises RuntimeError on WebUntis 'error' payloads."""
    r = session.post(
        BASE,
        params={"school": SCHOOL},
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


def _is_not_authenticated(err: Exception) -> bool:
    s = str(err).lower()
    # cover both message and code (-8526) just in case
    return ("not authenticated" in s) or ("-8526" in s)


def _invalidate_session():
    global _SESS_ID, _SESS_EXP
    _SESS_ID = None
    _SESS_EXP = 0.0


def _login(refresh: bool = False):
    """Authenticate if needed; cache JSESSIONID for a short window."""
    global _SESS_ID, _SESS_EXP
    now = time.time()
    if not refresh and _SESS_ID and now < _SESS_EXP:
        return {"JSESSIONID": _SESS_ID}

    # force new login
    res = _rpc("authenticate", {"user": USER, "password": PASS, "client": "untis-pwa"})
    _SESS_ID = res["sessionId"]
    # Keep the window conservative; Render free dynos can idle—token may vanish earlier.
    _SESS_EXP = now + 10 * 60  # ~10 minutes
    return {"JSESSIONID": _SESS_ID}


def _rpc_auth(method: str, params=None):
    """
    Authenticated RPC with auto re-login:
    - attempt with current/valid session
    - on 'not authenticated', invalidate, re-login and retry once
    """
    try:
        return _rpc(method, params, cookies=_login())
    except RuntimeError as e:
        if _is_not_authenticated(e):
            _invalidate_session()
            return _rpc(method, params, cookies=_login(refresh=True))
        raise


# ---------------- small utils ----------------
def _yyyymmdd(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def _hm(n: int) -> str:
    return f"{n // 100:02d}:{n % 100:02d}"


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
    if any(k in subst or k in txt for k in ("änder", "aender")):
        return "aenderung"
    if "entfall" in txt or "cancel" in txt:
        return "entfaellt"
    return "normal"


# ---------------- public API ----------------
def fetch_week(week_start: date):
    """Fetch timetable Mon–Sun starting at week_start (Monday recommended)."""

    s, e = week_start, week_start + timedelta(days=7)

    # raw timetable (with auto re-login)
    tt = _rpc_auth(
        "getTimetable",
        {
            "options": {
                "element": {"id": EID, "type": ETYPE},
                "startDate": _yyyymmdd(s),
                "endDate": _yyyymmdd(e),
            }
        },
    )

    # Lookups (also protected by auto re-login)
    try:
        teachers = {
            t["id"]: (t.get("longName") or t.get("name") or "")
            for t in _rpc_auth("getTeachers", {})
        }
    except Exception:
        teachers = {}

    try:
        subjects = {
            s["id"]: (s.get("longName") or s.get("name") or "")
            for s in _rpc_auth("getSubjects", {})
        }
    except Exception:
        subjects = {}

    try:
        rooms = {
            r["id"]: (r.get("longName") or r.get("name") or "")
            for r in _rpc_auth("getRooms", {})
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
        status = _status_from_item(x, note)

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
                "start": _hm(x.get("startTime", 0)),
                "end": _hm(x.get("endTime", 0)),
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


# ---------------- Exams ----------------
def fetch_exams(start_date: date, end_date: date, exam_type_id: int = 0):
    """
    Fetch exams in a date range.

    WebUntis expects YYYYMMDD ints and an optional examTypeId (0 = all types).
    """
    payload = {
        "startDate": _yyyymmdd(start_date),
        "endDate": _yyyymmdd(end_date),
        "examTypeId": int(exam_type_id),
    }
    return _rpc_auth("getExams", payload)


def fetch_subject_map() -> dict[int, str]:
    try:
        return {
            s["id"]: (s.get("longName") or s.get("name") or "")
            for s in _rpc_auth("getSubjects", {})
        }
    except Exception:
        return {}


def fetch_class_map() -> dict[int, str]:
    try:
        return {
            c["id"]: (c.get("longName") or c.get("name") or "")
            for c in _rpc_auth("getKlassen", {})
        }
    except Exception:
        return {}


def fetch_teacher_map() -> dict[int, str]:
    try:
        return {
            t["id"]: (t.get("longName") or t.get("name") or "")
            for t in _rpc_auth("getTeachers", {})
        }
    except Exception:
        return {}
