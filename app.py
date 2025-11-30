import os, json, time, re, sqlite3
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from flask import (
    Flask, jsonify, make_response, render_template, request,
    redirect, url_for, session, g
)
from werkzeug.security import generate_password_hash, check_password_hash

LAST_GOOD_PATH = "last_good_timetable.json"
LAST_GOOD = None
LAST_GOOD_TS = 0

def no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp

def load_last_good():
    global LAST_GOOD, LAST_GOOD_TS
    try:
        if os.path.exists(LAST_GOOD_PATH):
            with open(LAST_GOOD_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                LAST_GOOD = data
                LAST_GOOD_TS = data.get("_cachedAt", 0)
    except Exception:
        pass

def save_last_good(payload):
    try:
        with open(LAST_GOOD_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass

load_last_good()

# ---- Untis client (your existing implementation) ----
from untis_client import (
    fetch_week,
    fetch_exams,
    fetch_subject_map,
    fetch_class_map,
    fetch_teacher_map,
)

APP_TZ = ZoneInfo("Europe/Berlin")
ROOT   = os.path.dirname(os.path.abspath(__file__))
DATA   = ROOT  # keep mappings & seen files in project root


app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(24))
ADMIN_TOKEN        = os.environ.get("ADMIN_TOKEN")
DB_PATH            = os.environ.get("DB_PATH", os.path.join(DATA, "user_data.db"))
SETTINGS_DEFAULTS  = {"timeColumnWidth": "60"}
BACKUP_VERSION     = 2

if not ADMIN_TOKEN:
    raise RuntimeError("ADMIN_TOKEN environment variable is required and must not be empty.")

def _ensure_db_path() -> None:
    """Make sure DB directory exists and is writable (SQLite only)."""
    db_dir = os.path.dirname(DB_PATH) or "."
    try:
        os.makedirs(db_dir, exist_ok=True)
        test_path = os.path.join(db_dir, ".db_write_test")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(test_path)
    except Exception as exc:
        raise RuntimeError(f"Database path not writable: {DB_PATH} ({exc})")

def init_db():
    _ensure_db_path()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            password_plain TEXT,
            profile_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    try:
        conn.execute("ALTER TABLE users ADD COLUMN password_plain TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vacations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    for key, value in SETTINGS_DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
    conn.commit()
    conn.close()

def get_db():
    if "db" not in g:
        _ensure_db_path()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db

@app.teardown_appcontext
def close_db(exception):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()

init_db()

def _current_user_id():
    user_id = session.get("user_id")
    if user_id is None:
        return None
    try:
        return int(user_id)
    except (TypeError, ValueError):
        return None

def _load_user(user_id):
    if not user_id:
        return None
    db = get_db()
    cur = db.execute(
        "SELECT id, username, password_hash, profile_json FROM users WHERE id = ?",
        (user_id,)
    )
    return cur.fetchone()

def _empty_profile():
    return {"name": "", "courses": [], "klausuren": [], "colors": {"theme": {}, "subjects": {}}}

def _normalise_courses(value):
    if not isinstance(value, list):
        return []
    seen = set()
    out = []
    for item in value:
        if not isinstance(item, str):
            item = str(item or "")
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out

def _normalise_klausuren(items):
    if not isinstance(items, list):
        return []
    cleaned = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        entry = {
            "id": str(raw.get("id") or "").strip(),
            "subject": str(raw.get("subject") or "").strip(),
            "name": str(raw.get("name") or "").strip(),
            "date": str(raw.get("date") or "").strip(),
        }
        try:
            entry["periodStart"] = int(raw.get("periodStart"))
        except (TypeError, ValueError):
            entry["periodStart"] = None
        try:
            entry["periodEnd"] = int(raw.get("periodEnd"))
        except (TypeError, ValueError):
            entry["periodEnd"] = None
        cleaned.append(entry)
    return cleaned

def _clean_hex_color(value: str | None) -> str | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if not s.startswith("#"):
        s = "#" + s
    if not re.fullmatch(r"#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?", s):
        return None
    if len(s) == 4:
        s = "#" + "".join([ch * 2 for ch in s[1:]])
    return s.lower()

def _normalise_colors(block):
    norm = {"theme": {}, "subjects": {}}
    if not isinstance(block, dict):
        return norm
    theme_raw = block.get("theme") or {}
    if isinstance(theme_raw, dict):
        for key in ("lessonBg", "lessonText", "lessonBorder", "grid", "gridBg", "brand", "klausurBg", "klausurBorder"):
            col = _clean_hex_color(theme_raw.get(key))
            if col:
                norm["theme"][key] = col
    subjects_raw = block.get("subjects") or {}
    if isinstance(subjects_raw, dict):
        for raw_key, col in subjects_raw.items():
            nk = norm_key(raw_key)
            cleaned = _clean_hex_color(col)
            if nk and cleaned:
                norm["subjects"][nk] = cleaned
    return norm

def _normalise_profile(payload):
    if not isinstance(payload, dict):
        payload = {}
    profile = _empty_profile()
    profile["name"] = str(payload.get("name") or "").strip()
    profile["courses"] = _normalise_courses(payload.get("courses"))
    profile["klausuren"] = _normalise_klausuren(payload.get("klausuren"))
    profile["colors"] = _normalise_colors(payload.get("colors"))
    return profile

def _load_profile_for_user(row):
    if not row:
        return _empty_profile()
    raw = row["profile_json"] if isinstance(row, sqlite3.Row) else row.get("profile_json")
    try:
        payload = json.loads(raw) if raw else {}
    except (TypeError, json.JSONDecodeError):
        payload = {}
    return _normalise_profile(payload)

def _get_setting(key, default=None):
    db = get_db()
    cur = db.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    if row and row["value"] is not None:
        return row["value"]
    return SETTINGS_DEFAULTS.get(key, default)


def _set_settings(values):
    if not values:
        return
    db = get_db()
    for key, value in values.items():
        if key not in SETTINGS_DEFAULTS:
            continue
        if key == "timeColumnWidth":
            try:
                numeric = int(float(value))
            except (TypeError, ValueError):
                numeric = int(SETTINGS_DEFAULTS["timeColumnWidth"])
            numeric = max(40, min(120, numeric))
            value = str(numeric)
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value))
        )
    db.commit()

def _save_profile(user_id, profile):
    db = get_db()
    norm = json.dumps(_normalise_profile(profile))
    db.execute(
        "UPDATE users SET profile_json = ? WHERE id = ?",
        (norm, user_id)
    )
    db.commit()

def _parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()

# ---- Exams helpers ----
def _date_int_to_iso(n) -> str:
    try:
        s = str(int(n)).zfill(8)
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    except Exception:
        return str(n or "")

def _hm_from_int(n) -> str:
    try:
        n = int(n)
        return f"{n // 100:02d}:{n % 100:02d}"
    except Exception:
        return ""

# ---------- Normalisation (canonical across app) ----------
_UML = str.maketrans({"ä":"a","ö":"o","ü":"u","Ä":"a","Ö":"o","Ü":"u"})

def norm_key(s: str) -> str:
    """Canonical key for subjects/rooms: lower, umlaut fold, drop paren chars, dashes, tags, collapse spaces."""
    if not s:
        return ""
    s = s.strip().translate(_UML).lower()
    s = re.sub(r"\s+", " ", s)
    s = s.replace("(", " ").replace(")", " ")  # keep inner text
    s = re.sub(r"[-–—]+", " ", s)       # replace hyphen-like chars
    s = re.sub(r"\b(gk|lk|ag)\b", " ", s)   # simple tags
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())

def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp

# ---------- Mapping I/O ----------
COURSE_MAP_PATH = os.path.join(DATA, "course_mapping.txt")
ROOM_MAP_PATH   = os.path.join(DATA, "rooms_mapping.txt")

def load_mapping_txt(path):
    """Return dict {lhs(normalized or raw key): rhs(display)} including empty rhs."""
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "=" not in s:
                    continue
                lhs, rhs = s.split("=", 1)
                lhs = lhs.strip()
                rhs = rhs.strip()
                data[lhs] = rhs
    except FileNotFoundError:
        pass
    return data

def _parse_mapping(file_path: str) -> dict[str, str]:
    """Read key=value lines; index by normalised key on the left. Empty right is allowed."""
    mapping: dict[str, str] = {}
    if not os.path.exists(file_path):
        return mapping
    with open(file_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            left, right = line.split("=", 1)
            mapping[norm_key(left)] = right.strip()
    return mapping

def _write_mapping_txt(path: str, mapping: dict[str, str]) -> None:
    """Persist mapping as normalised_left = right (right kept exactly; may be empty)."""
    lines = []
    for nk in sorted(mapping.keys()):
        lines.append(f"{nk}={mapping[nk]}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))

# ---------- Seen keys (store raw & normalised) ----------
SEEN_SUB_RAW_PATH = os.path.join(DATA, "seen_subjects_raw.json")
SEEN_ROOM_RAW_PATH = os.path.join(DATA, "seen_rooms_raw.json")

def _load_seen_raw(path: str) -> list[str]:
    if not os.path.exists(path): return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            v = json.load(f)
            return v if isinstance(v, list) else []
    except Exception:
        return []

def _save_seen_raw(path: str, arr: list[str]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(set(arr)), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

SEEN_SUBJECTS_RAW = _load_seen_raw(SEEN_SUB_RAW_PATH)
SEEN_ROOMS_RAW    = _load_seen_raw(SEEN_ROOM_RAW_PATH)
_last_seen_flush  = 0.0

def record_seen_raw(lessons: list[dict]):
    """Remember raw variants exactly as Untis sends them (for admin grouping)."""
    global _last_seen_flush
    changed = False
    for L in lessons:
        sraw = (L.get("subject_original") or L.get("subject") or "").strip()
        rraw = (L.get("room") or "").strip()
        if sraw and sraw not in SEEN_SUBJECTS_RAW:
            SEEN_SUBJECTS_RAW.append(sraw); changed = True
        if rraw and rraw not in SEEN_ROOMS_RAW:
            SEEN_ROOMS_RAW.append(rraw); changed = True
    now = time.time()
    if changed and (now - _last_seen_flush > 15):
        _save_seen_raw(SEEN_SUB_RAW_PATH, SEEN_SUBJECTS_RAW)
        _save_seen_raw(SEEN_ROOM_RAW_PATH, SEEN_ROOMS_RAW)
        _last_seen_flush = now

def _group_variants(raw_list: list[str]) -> dict[str, list[str]]:
    """Return { normalised_key: [raw variants…] }."""
    grouped: dict[str, set[str]] = {}
    for raw in raw_list:
        nk = norm_key(raw)
        grouped.setdefault(nk, set()).add(raw)
    return {k: sorted(v) for k, v in grouped.items()}

# ---------- Timetable cache/throttle ----------
_last_weekkey_ts: dict[str, float] = {}
_last_weekkey_payload: dict[str, dict] = {}

def _week_key(ws: date) -> str:
    return ws.isoformat()

# ---------- Exams cache/throttle ----------
_last_exam_key_ts: dict[str, float] = {}
_last_exam_payload: dict[str, dict] = {}

def _exam_key(start: date, end: date, exam_type: int) -> str:
    return f"{start.isoformat()}_{end.isoformat()}_{exam_type}"

# ---------------- Routes ----------------
@app.after_request
def add_no_cache(resp):
    return _no_store(resp)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/mappings")
def api_mappings():
    course_map = _parse_mapping(COURSE_MAP_PATH)
    room_map   = _parse_mapping(ROOM_MAP_PATH)
    return _no_store(jsonify({"ok": True, "courses": course_map, "rooms": room_map}))

@app.route("/api/courses")
def api_courses():
    """Return course options (key + display label) from course_mapping.txt.

    Key: normalised LHS via norm_key. Label: RHS if present, else original LHS.
    """
    raw_map = load_mapping_txt(COURSE_MAP_PATH)  # { raw_left: rhs }
    options: dict[str, str] = {}
    for left, right in raw_map.items():
        left = (left or "").strip()
        label = (right or "").strip() or left
        if not left or not label:
            continue
        nk = norm_key(left)
        if not nk:
            continue
        options[nk] = label

    items = [
        {"key": key, "label": options[key]}
        for key in sorted(options.keys(), key=lambda k: (options[k].lower(), options[k]))
    ]
    return _no_store(jsonify({"ok": True, "courses": items}))

@app.route("/api/health")
def api_health():
    return no_store(make_response(jsonify({"ok": True}), 200))

@app.route("/api/timetable")
def api_timetable():
    # week selection
    qs = request.args.get("weekStart")
    if qs:
        try:
            ws = datetime.strptime(qs, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"ok": False, "error": "bad weekStart; use YYYY-MM-DD"}), 400
    else:
        today = datetime.now(APP_TZ).date()
        ws = _monday_of(today) + (timedelta(days=7) if today.weekday() in (5, 6) else timedelta(0))

    weekkey = _week_key(ws)
    debug   = request.args.get("debug") == "1"
    force   = request.args.get("force") == "1" or debug

    # throttle Untis calls for 15s per week unless forced
    now_ts = time.time()
    if not force and weekkey in _last_weekkey_payload and (now_ts - _last_weekkey_ts.get(weekkey, 0)) < 15:
        return _no_store(jsonify(_last_weekkey_payload[weekkey]))

    raw_width = _get_setting("timeColumnWidth", SETTINGS_DEFAULTS["timeColumnWidth"])
    try:
        width_value = int(float(raw_width))
    except (TypeError, ValueError):
        width_value = int(SETTINGS_DEFAULTS["timeColumnWidth"])
    width_value = max(40, min(120, width_value))
    settings_payload = {"timeColumnWidth": width_value}

    try:
        lessons = fetch_week(ws)  # your Untis client returns raw lessons
    except Exception as e:
        app.logger.exception("fetch_week failed")
        payload = {"ok": False, "weekStart": str(ws), "lessons": [], "error": str(e), "settings": settings_payload}
        _last_weekkey_payload[weekkey] = payload
        _last_weekkey_ts[weekkey] = time.time()
        return _no_store(jsonify(payload))

    # remember raw variants for admin UI
    record_seen_raw(lessons)

    # optionally enrich with debug mapping fields
    if debug:
        cmap = _parse_mapping(COURSE_MAP_PATH)
        rmap = _parse_mapping(ROOM_MAP_PATH)
        for L in lessons:
            sr = (L.get("subject_original") or L.get("subject") or "")
            rr = (L.get("room") or "")
            sn = norm_key(sr); rn = norm_key(rr)
            L["debug"] = {
                "subject_raw": sr, "subject_norm": sn, "mapped_subject": cmap.get(sn),
                "room_raw": rr,    "room_norm": rn,    "mapped_room": rmap.get(rn),
                "server_now": datetime.now(APP_TZ).isoformat(), "week_start": ws.isoformat()
            }

    payload = {"ok": True, "weekStart": str(ws), "lessons": lessons, "settings": settings_payload}
    _last_weekkey_payload[weekkey] = payload
    _last_weekkey_ts[weekkey] = time.time()
    return _no_store(jsonify(payload))

@app.route("/api/exams")
def api_exams():
    today = datetime.now(APP_TZ).date()
    start_raw = request.args.get("start")
    end_raw   = request.args.get("end")
    type_raw  = request.args.get("type") or request.args.get("examTypeId") or "0"
    force     = request.args.get("force") == "1"

    start = today
    end   = today + timedelta(days=30)
    if start_raw:
        try:
            start = _parse_iso_date(start_raw)
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_start_date"}), 400
    if end_raw:
        try:
            end = _parse_iso_date(end_raw)
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_end_date"}), 400
    if end < start:
        start, end = end, start

    try:
        exam_type = int(type_raw)
    except (TypeError, ValueError):
        exam_type = 0

    cache_key = _exam_key(start, end, exam_type)
    now_ts = time.time()
    if not force and cache_key in _last_exam_payload and (now_ts - _last_exam_key_ts.get(cache_key, 0)) < 15:
        return _no_store(jsonify(_last_exam_payload[cache_key]))

    try:
        raw_exams = fetch_exams(start, end, exam_type) or []
        subjects  = fetch_subject_map()
        classes   = fetch_class_map()
        teachers  = fetch_teacher_map()
    except Exception as e:
        msg = str(e)
        err_code = "exam_fetch_failed"
        if "no right" in msg.lower() or "-8509" in msg:
            err_code = "exam_permission_denied"
        payload = {
            "ok": False,
            "error": msg,
            "errorCode": err_code,
            "start": str(start),
            "end": str(end),
            "examType": exam_type,
            "exams": [],
        }
        app.logger.warning("fetch_exams failed: %s", msg)
        _last_exam_payload[cache_key] = payload
        _last_exam_key_ts[cache_key] = time.time()
        return _no_store(jsonify(payload))

    def _norm_exam(rec):
        if not isinstance(rec, dict):
            return None
        eid = rec.get("id")
        date_iso = _date_int_to_iso(rec.get("date"))
        start_hm = rec.get("start") or rec.get("startTime")
        end_hm   = rec.get("end") or rec.get("endTime")
        start_hm = start_hm if isinstance(start_hm, str) and ":" in start_hm else _hm_from_int(start_hm)
        end_hm   = end_hm if isinstance(end_hm, str) and ":" in end_hm else _hm_from_int(end_hm)
        subj_id = rec.get("subject")
        subj_name = rec.get("subjectName") or subjects.get(subj_id, "")
        class_ids = rec.get("classes") or []
        teach_ids = rec.get("teachers") or []
        return {
            "id": eid,
            "date": date_iso,
            "start": start_hm,
            "end": end_hm,
            "subject": subj_name,
            "subjectId": subj_id,
            "classIds": class_ids,
            "classes": [classes.get(cid, "") for cid in class_ids if cid],
            "teacherIds": teach_ids,
            "teachers": [teachers.get(tid, "") for tid in teach_ids if tid],
        }

    exams = [_norm_exam(rec) for rec in raw_exams]
    exams = [e for e in exams if e and e.get("date")]

    payload = {
        "ok": True,
        "start": str(start),
        "end": str(end),
        "examType": exam_type,
        "exams": exams,
    }
    _last_exam_payload[cache_key] = payload
    _last_exam_key_ts[cache_key] = time.time()
    return _no_store(jsonify(payload))

@app.route("/api/vacations")
def api_vacations():
    db = get_db()
    cur = db.execute(
        "SELECT id, title, start_date, end_date FROM vacations ORDER BY start_date, title"
    )
    rows = [
        {
            "id": row["id"],
            "title": row["title"],
            "start_date": row["start_date"],
            "end_date": row["end_date"],
        }
        for row in cur.fetchall()
    ]
    return _no_store(jsonify({"ok": True, "vacations": rows}))

def _auth_response(row):
    profile = _load_profile_for_user(row) if row else _empty_profile()
    return {
        "ok": True,
        "authenticated": bool(row),
        "username": row["username"] if row else None,
        "profile": profile
    }

@app.route("/api/auth/status")
def api_auth_status():
    user = _load_user(_current_user_id())
    payload = _auth_response(user if user else None)
    return _no_store(jsonify(payload))

@app.route("/api/auth/register", methods=["POST"])
def api_auth_register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"ok": False, "error": "invalid_input"}), 400
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO users (username, password_hash, password_plain, profile_json) VALUES (?, ?, ?, ?)",
            (username, generate_password_hash(password), password, json.dumps(_empty_profile()))
        )
        new_id = cur.lastrowid
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "error": "username_exists"}), 409
    session["user_id"] = new_id
    row = _load_user(new_id)
    return _no_store(jsonify(_auth_response(row))), 201

@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"ok": False, "error": "invalid_input"}), 400
    row = None
    if username:
        cur = get_db().execute(
            "SELECT id, username, password_hash, profile_json FROM users WHERE username = ?",
            (username,)
        )
        row = cur.fetchone()
    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify({"ok": False, "error": "invalid_credentials"}), 401
    session["user_id"] = row["id"]
    return _no_store(jsonify(_auth_response(row)))

@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    session.pop("user_id", None)
    return _no_store(jsonify({"ok": True, "authenticated": False}))

@app.route("/api/profile", methods=["GET", "PUT"])
def api_profile():
    user_id = _current_user_id()
    row = _load_user(user_id)
    if not row:
        session.pop("user_id", None)
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    if request.method == "GET":
        payload = {
            "ok": True,
            "profile": _load_profile_for_user(row),
            "username": row["username"]
        }
        return _no_store(jsonify(payload))
    data = request.get_json(silent=True) or {}
    profile = _normalise_profile(data)
    _save_profile(user_id, profile)
    return _no_store(jsonify({"ok": True, "profile": profile}))

# ---- Admin auth/UI ----
def _require_admin() -> bool:
    return bool(ADMIN_TOKEN) and session.get("admin_ok") is True

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        token = request.form.get("token", "")
        if ADMIN_TOKEN and token == ADMIN_TOKEN:
            session["admin_ok"] = True
            return redirect(url_for("admin_mappings"))
        return render_template("admin_login.html", error="Falsches Passwort")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_ok", None)
    return redirect(url_for("admin_login"))

@app.route("/admin/mappings")
def admin_mappings():
    if not _require_admin():
        return redirect(url_for("admin_login"))
    return render_template("admin_mappings.html")

# ---- Admin APIs ----
def _build_backup_payload() -> dict:
    """Collect all editable data so admins can download a single backup file."""
    db = get_db()
    users = []
    try:
        cur = db.execute(
            "SELECT id, username, password_hash, password_plain, profile_json, created_at FROM users ORDER BY id"
        )
        for row in cur.fetchall():
            users.append({
                "id": row["id"],
                "username": row["username"],
                "password_hash": row["password_hash"],
                "password_plain": row["password_plain"],
                "profile": _load_profile_for_user(row),
                "created_at": row["created_at"],
            })
    except Exception:
        users = []

    vacations = []
    try:
        cur = db.execute(
            "SELECT id, title, start_date, end_date, created_at FROM vacations ORDER BY start_date, id"
        )
        for row in cur.fetchall():
            vacations.append({
                "id": row["id"],
                "title": row["title"],
                "start_date": row["start_date"],
                "end_date": row["end_date"],
                "created_at": row["created_at"],
            })
    except Exception:
        vacations = []

    settings_map = {}
    try:
        cur = db.execute("SELECT key, value FROM settings")
        for row in cur.fetchall():
            settings_map[row["key"]] = row["value"]
    except Exception:
        settings_map = {}
    for key, default in SETTINGS_DEFAULTS.items():
        settings_map.setdefault(key, default)

    payload = {
        "meta": {
            "version": BACKUP_VERSION,
            "exported_at": datetime.now(APP_TZ).isoformat(),
        },
        "database": {
            "users": users,
            "vacations": vacations,
            "settings": settings_map,
        },
        "mappings": {
            "courses": _parse_mapping(COURSE_MAP_PATH),
            "rooms": _parse_mapping(ROOM_MAP_PATH),
        },
        "seen": {
            "subjects_raw": sorted(set(SEEN_SUBJECTS_RAW)),
            "rooms_raw": sorted(set(SEEN_ROOMS_RAW)),
        },
    }
    return payload


def _apply_backup_payload(payload: dict) -> None:
    """Restore data from a backup payload (admin only)."""
    if not isinstance(payload, dict):
        raise ValueError("backup_payload_invalid")

    db_section = payload.get("database")
    mappings_section = payload.get("mappings")
    seen_section = payload.get("seen")
    if not isinstance(db_section, dict) or not isinstance(mappings_section, dict) or not isinstance(seen_section, dict):
        raise ValueError("backup_payload_invalid")

    # ---- Pre-validate and normalise before touching the DB ----
    users_norm = []
    users = db_section.get("users") or []
    if isinstance(users, list):
        for entry in users:
            if not isinstance(entry, dict):
                continue
            username = (entry.get("username") or "").strip()
            if not username:
                continue
            try:
                user_id = int(entry.get("id"))
            except (TypeError, ValueError):
                user_id = None
            profile = entry.get("profile") if isinstance(entry.get("profile"), dict) else None
            if profile is None:
                profile_raw = entry.get("profile_json")
                if isinstance(profile_raw, str) and profile_raw.strip():
                    try:
                        profile = json.loads(profile_raw)
                    except Exception:
                        profile = None
            if profile is None:
                profile = _empty_profile()
            profile_json = json.dumps(_normalise_profile(profile))
            created_at = entry.get("created_at") or datetime.utcnow().isoformat()
            users_norm.append((user_id, username, entry.get("password_hash") or "", entry.get("password_plain"), profile_json, created_at))

    vacations_norm = []
    vacations = db_section.get("vacations") or []
    if isinstance(vacations, list):
        for entry in vacations:
            if not isinstance(entry, dict):
                continue
            title = (entry.get("title") or "").strip()
            start_date = (entry.get("start_date") or "").strip()
            end_date = (entry.get("end_date") or start_date).strip()
            if not title or not start_date:
                continue
            try:
                vac_id = int(entry.get("id"))
            except (TypeError, ValueError):
                vac_id = None
            created_at = entry.get("created_at") or datetime.utcnow().isoformat()
            vacations_norm.append((vac_id, title, start_date, end_date, created_at))

    settings_in = db_section.get("settings") if isinstance(db_section, dict) else {}
    settings_payload = SETTINGS_DEFAULTS.copy()
    if isinstance(settings_in, dict):
        for key, value in settings_in.items():
            if key in SETTINGS_DEFAULTS:
                settings_payload[key] = str(value)

    courses_map = {}
    courses = mappings_section.get("courses")
    if isinstance(courses, dict):
        for k, v in courses.items():
            nk = norm_key(k)
            courses_map[nk] = (v or "").strip()

    rooms_map = {}
    rooms = mappings_section.get("rooms")
    if isinstance(rooms, dict):
        for k, v in rooms.items():
            nk = norm_key(k)
            rooms_map[nk] = (v or "").strip()

    subs_raw = seen_section.get("subjects_raw") if isinstance(seen_section, dict) else []
    rooms_raw = seen_section.get("rooms_raw") if isinstance(seen_section, dict) else []
    subs_norm = sorted({str(s or "").strip() for s in subs_raw if str(s or "").strip()}) if isinstance(subs_raw, list) else []
    rooms_norm = sorted({str(r or "").strip() for r in rooms_raw if str(r or "").strip()}) if isinstance(rooms_raw, list) else []

    db = get_db()
    try:
        db.execute("BEGIN")
        db.execute("DELETE FROM users")
        db.execute("DELETE FROM vacations")
        db.execute("DELETE FROM settings")

        for row in users_norm:
            db.execute(
                "INSERT INTO users (id, username, password_hash, password_plain, profile_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                row
            )

        for row in vacations_norm:
            db.execute(
                "INSERT INTO vacations (id, title, start_date, end_date, created_at) VALUES (?, ?, ?, ?, ?)",
                row
            )

        for key, value in settings_payload.items():
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                (key, str(value))
            )

        db.commit()
    except Exception:
        db.rollback()
        raise

    _write_mapping_txt(COURSE_MAP_PATH, courses_map)
    _write_mapping_txt(ROOM_MAP_PATH, rooms_map)

    global SEEN_SUBJECTS_RAW, SEEN_ROOMS_RAW, _last_seen_flush
    SEEN_SUBJECTS_RAW = subs_norm
    SEEN_ROOMS_RAW = rooms_norm
    _save_seen_raw(SEEN_SUB_RAW_PATH, SEEN_SUBJECTS_RAW)
    _save_seen_raw(SEEN_ROOM_RAW_PATH, SEEN_ROOMS_RAW)
    _last_seen_flush = time.time()


@app.route("/api/admin/backup")
def admin_backup():
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    payload = _build_backup_payload()
    filename = f"untis-backup-{datetime.now(APP_TZ).strftime('%Y%m%d-%H%M%S')}.json"
    resp = make_response(json.dumps(payload, ensure_ascii=False, indent=2))
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Content-Disposition"] = f'attachment; filename=\"{filename}\"'
    return _no_store(resp)


@app.route("/api/admin/restore", methods=["POST"])
def admin_restore():
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"ok": False, "error": "invalid_backup"}), 400
    try:
        _apply_backup_payload(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception:
        app.logger.exception("restore failed")
        return jsonify({"ok": False, "error": "restore_failed"}), 500
    return _no_store(jsonify({"ok": True}))

@app.route("/api/admin/state")
def admin_state():
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    courses = _parse_mapping(COURSE_MAP_PATH)
    rooms   = _parse_mapping(ROOM_MAP_PATH)

    groups_sub = _group_variants(SEEN_SUBJECTS_RAW)
    groups_rm  = _group_variants(SEEN_ROOMS_RAW)

    unmapped_sub = [nk for nk in sorted(groups_sub.keys()) if nk not in courses]
    unmapped_rm  = [nk for nk in sorted(groups_rm.keys())  if nk not in rooms]

    user_rows = []
    try:
        cur = get_db().execute(
            "SELECT id, username, password_plain, password_hash FROM users ORDER BY LOWER(username)"
        )
        rows = cur.fetchall()
        user_rows = []
        for row in rows:
            username = row["username"] if isinstance(row, dict) else row[1]
            pwd_plain = row["password_plain"] if isinstance(row, dict) else row[2]
            pwd_hash = row["password_hash"] if isinstance(row, dict) else row[3]
            uid = row["id"] if isinstance(row, dict) else row[0]
            user_rows.append({
                "id": uid,
                "username": username,
                "password": pwd_plain or pwd_hash,
            })
    except Exception:
        user_rows = []

    vacations = []
    try:
        cur = get_db().execute(
            "SELECT id, title, start_date, end_date, created_at FROM vacations ORDER BY start_date, title"
        )
        rows = cur.fetchall()
        vacations = []
        for row in rows:
            vacations.append({
                "id": row["id"] if isinstance(row, dict) else row[0],
                "title": row["title"] if isinstance(row, dict) else row[1],
                "start_date": row["start_date"] if isinstance(row, dict) else row[2],
                "end_date": row["end_date"] if isinstance(row, dict) else row[3],
                "created_at": row["created_at"] if isinstance(row, dict) else row[4],
            })
    except Exception:
        vacations = []

    settings_payload = {key: _get_setting(key, default) for key, default in SETTINGS_DEFAULTS.items()}

    return _no_store(jsonify({
        "ok": True,
        "courses": courses,                # { norm_key: display }
        "rooms": rooms,                    # { norm_key: display }
        "subjects_grouped": groups_sub,    # { norm_key: [raw variants…] }
        "rooms_grouped": groups_rm,        # { norm_key: [raw variants…] }
        "unmapped_subjects": unmapped_sub,
        "unmapped_rooms": unmapped_rm,
        "users": user_rows,
        "vacations": vacations,
        "settings": settings_payload
    }))

@app.route("/api/admin/save", methods=["POST"])
def admin_save():
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    new_courses: dict = payload.get("courses") or {}
    new_rooms: dict   = payload.get("rooms") or {}
    new_settings: dict = payload.get("settings") or {}
    new_settings: dict = payload.get("settings") or {}

    # load current
    courses = _parse_mapping(COURSE_MAP_PATH)
    rooms   = _parse_mapping(ROOM_MAP_PATH)

    # merge (normalise keys, keep RHS exactly as typed; empty allowed)
    for k, v in new_courses.items():
        courses[norm_key(k)] = (v or "").strip()
    for k, v in new_rooms.items():
        rooms[norm_key(k)] = (v or "").strip()

    _write_mapping_txt(COURSE_MAP_PATH, courses)
    _write_mapping_txt(ROOM_MAP_PATH, rooms)

    sanitized_settings = {}
    if isinstance(new_settings, dict):
        for key, value in new_settings.items():
            if key in SETTINGS_DEFAULTS:
                sanitized_settings[key] = str(value)
    if sanitized_settings:
        _set_settings(sanitized_settings)

    return _no_store(jsonify({"ok": True, "saved_courses": len(new_courses), "saved_rooms": len(new_rooms), "saved_settings": len(sanitized_settings)}))

@app.route("/api/admin/vacations", methods=["GET", "POST"])
def admin_vacations():
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    db = get_db()
    if request.method == "GET":
        cur = db.execute(
            "SELECT id, title, start_date, end_date, created_at FROM vacations ORDER BY start_date, title"
        )
        rows = [
            {
                "id": row["id"],
                "title": row["title"],
                "start_date": row["start_date"],
                "end_date": row["end_date"],
                "created_at": row["created_at"],
            }
            for row in cur.fetchall()
        ]
        return _no_store(jsonify({"ok": True, "vacations": rows}))

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    start_raw = (data.get("start_date") or "").strip()
    end_raw = (data.get("end_date") or "").strip() or start_raw
    if not title or not start_raw or not end_raw:
        return jsonify({"ok": False, "error": "invalid_input"}), 400
    try:
        start = _parse_iso_date(start_raw)
        end = _parse_iso_date(end_raw)
    except ValueError:
        return jsonify({"ok": False, "error": "invalid_date"}), 400
    if end < start:
        start, end = end, start
    db.execute(
        "INSERT INTO vacations (title, start_date, end_date) VALUES (?, ?, ?)",
        (title, start.isoformat(), end.isoformat())
    )
    db.commit()
    return _no_store(jsonify({"ok": True}))

@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
def admin_delete_user(user_id: int):
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    db = get_db()
    cur = db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    if cur.rowcount == 0:
        return _no_store(jsonify({"ok": False, "error": "not_found"})), 404
    return _no_store(jsonify({"ok": True, "deleted": user_id}))

@app.route("/api/admin/vacations/<int:vac_id>", methods=["DELETE"])
def admin_delete_vacation(vac_id: int):
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    db = get_db()
    cur = db.execute("DELETE FROM vacations WHERE id = ?", (vac_id,))
    db.commit()
    if cur.rowcount == 0:
        return _no_store(jsonify({"ok": False, "error": "not_found"})), 404
    return _no_store(jsonify({"ok": True, "deleted": vac_id}))

if __name__ == "__main__":
    debug_enabled = str(os.environ.get("FLASK_DEBUG", "")).lower() in ("1", "true", "yes")
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("PORT", "5000"))
    except (TypeError, ValueError):
        port = 5000
    app.run(host=host, port=port, debug=debug_enabled)
