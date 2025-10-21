import os, json, time, re, sqlite3
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from flask import (
    Flask, jsonify, render_template, request,
    redirect, url_for, session, g
)

import time, json, os, traceback
from flask import jsonify, make_response
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
from untis_client import fetch_week

APP_TZ = ZoneInfo("Europe/Berlin")
ROOT   = os.path.dirname(os.path.abspath(__file__))
DATA   = ROOT  # keep mappings & seen files in project root

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(24))
ADMIN_TOKEN    = os.environ.get("ADMIN_TOKEN")
DB_PATH        = os.path.join(DATA, "user_data.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            profile_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()

def get_db():
    if "db" not in g:
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
    return {"name": "", "courses": [], "klausuren": []}

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

def _normalise_profile(payload):
    if not isinstance(payload, dict):
        payload = {}
    profile = _empty_profile()
    profile["name"] = str(payload.get("name") or "").strip()
    profile["courses"] = _normalise_courses(payload.get("courses"))
    profile["klausuren"] = _normalise_klausuren(payload.get("klausuren"))
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

def _save_profile(user_id, profile):
    db = get_db()
    db.execute(
        "UPDATE users SET profile_json = ? WHERE id = ?",
        (json.dumps(_normalise_profile(profile)), user_id)
    )
    db.commit()

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

    try:
        lessons = fetch_week(ws)  # your Untis client returns raw lessons
    except Exception as e:
        app.logger.exception("fetch_week failed")
        payload = {"ok": False, "weekStart": str(ws), "lessons": [], "error": str(e)}
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

    payload = {"ok": True, "weekStart": str(ws), "lessons": lessons}
    _last_weekkey_payload[weekkey] = payload
    _last_weekkey_ts[weekkey] = time.time()
    return _no_store(jsonify(payload))

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
            "INSERT INTO users (username, password_hash, profile_json) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), json.dumps(_empty_profile()))
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "error": "username_exists"}), 409
    session["user_id"] = cur.lastrowid
    row = _load_user(cur.lastrowid)
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
        row = get_db().execute(
            "SELECT id, username, password_hash, profile_json FROM users WHERE username = ?",
            (username,)
        ).fetchone()
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

    return _no_store(jsonify({
        "ok": True,
        "courses": courses,                # { norm_key: display }
        "rooms": rooms,                    # { norm_key: display }
        "subjects_grouped": groups_sub,    # { norm_key: [raw variants…] }
        "rooms_grouped": groups_rm,        # { norm_key: [raw variants…] }
        "unmapped_subjects": unmapped_sub,
        "unmapped_rooms": unmapped_rm
    }))

@app.route("/api/admin/save", methods=["POST"])
def admin_save():
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    new_courses: dict = payload.get("courses") or {}
    new_rooms: dict   = payload.get("rooms") or {}

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

    return _no_store(jsonify({"ok": True, "saved_courses": len(new_courses), "saved_rooms": len(new_rooms)}))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
