import os, json, time, re, sqlite3, shutil
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

if load_dotenv:
    load_dotenv(".env")
from flask import (
    Flask, jsonify, make_response, render_template, request,
    redirect, url_for, session, g
)
from werkzeug.security import generate_password_hash, check_password_hash

LAST_GOOD_PATH = "last_good_timetable.json"
LAST_GOOD = None
LAST_GOOD_TS = 0
LAST_BACKUP_PATH = "last_backup.json"

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
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _save_last_backup(payload: dict) -> None:
    """Persist the last imported backup so we can fall back to it for profiles."""
    try:
        with open(LAST_BACKUP_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _backup_profile_for(username: str) -> dict | None:
    """Return profile from last saved backup for a given username (if present)."""
    try:
        if not os.path.exists(LAST_BACKUP_PATH):
            return None
        with open(LAST_BACKUP_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        users = data.get("database", {}).get("users", [])
        for u in users:
            if str(u.get("username") or "").strip() == username:
                prof = u.get("profile")
                if isinstance(prof, dict):
                    return _normalise_profile(prof)
        return None
    except Exception:
        return None

load_last_good()

# ---- Untis client (your existing implementation) ----
from untis_client import (
    fetch_week,
    fetch_week_all,
    fetch_exams,
    fetch_subject_map,
    fetch_class_map,
    fetch_teacher_map,
    available_grades,
)

try:
    APP_TZ = ZoneInfo("Europe/Berlin")
except Exception:
    from datetime import timezone
    APP_TZ = timezone.utc
ROOT   = os.path.dirname(os.path.abspath(__file__))
DATA   = ROOT  # base directory for DB and legacy files
DATA_DIR = os.path.join(ROOT, "data")  # organized data folder for mappings/seen
os.makedirs(DATA_DIR, exist_ok=True)


app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(24))
SESSION_LIFETIME_DAYS = os.environ.get("SESSION_LIFETIME_DAYS")
try:
    SESSION_LIFETIME_DAYS = int(SESSION_LIFETIME_DAYS) if SESSION_LIFETIME_DAYS else 90
except Exception:
    SESSION_LIFETIME_DAYS = 90
app.permanent_session_lifetime = timedelta(days=SESSION_LIFETIME_DAYS)
ADMIN_TOKEN        = os.environ.get("ADMIN_TOKEN")
DB_PATH            = os.environ.get("DB_PATH", os.path.join(DATA, "user_data.db"))
SETTINGS_DEFAULTS  = {
    "timeColumnWidth": "60",
    "updateBannerText": "",
    "updateBannerEnabled": "0",
    "updateBannerUpdatedAt": "0",
}
BACKUP_VERSION     = 3

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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exams_manual (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            name TEXT NOT NULL,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            classes_json TEXT NOT NULL DEFAULT '[]',
            teachers_json TEXT NOT NULL DEFAULT '[]',
            room TEXT,
            note TEXT,
            grade TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    try:
        conn.execute("ALTER TABLE exams_manual ADD COLUMN grade TEXT")
    except sqlite3.OperationalError:
        pass
    for key, value in SETTINGS_DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )

    # seed sample exams if table empty (pre-load manual list)
    try:
        cur = conn.execute("SELECT COUNT(*) FROM exams_manual")
        count = cur.fetchone()[0]
        if count == 0:
            seed_exams = [
            ]
            for entry in seed_exams:
                conn.execute(
                    """
                    INSERT INTO exams_manual (subject, name, date, start_time, end_time, classes_json, teachers_json, room, note, grade)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry["subject"],
                        entry.get("name") or entry["subject"],
                        entry["date"],
                        entry["start_time"],
                        entry["end_time"],
                        json.dumps(entry.get("classes") or []),
                        json.dumps(entry.get("teachers") or []),
                        entry.get("room") or "",
                        entry.get("note") or "",
                        (entry.get("grade") or "").strip().upper(),
                    )
                )
    except Exception:
        pass

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


@app.before_request
def _keep_sessions_permanent():
    """
    Refresh logged-in sessions as "permanent" so the cookie survives browser restarts.
    Flask will refresh the expiry on each request when SESSION_REFRESH_EACH_REQUEST is True.
    """
    if session.get("user_id") or session.get("admin_ok"):
        session.permanent = True

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

def _grade_prefixed_courses(values: list[str]) -> list[str]:
    """Normalise courses and add EF:/Q1: prefix when grade can be inferred from mappings."""
    if not isinstance(values, list):
        return []
    courses_ef = _course_map_normalized_for_grade("EF")
    courses_q1 = _course_map_normalized_for_grade("Q1")
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            item = str(item or "")
        raw = item.strip()
        if not raw:
            continue
        upper = raw.upper()
        if upper.startswith("EF:") or upper.startswith("Q1:"):
            key = raw
        else:
            nk = norm_key(raw)
            key = None
            if nk:
                in_ef = nk in courses_ef
                in_q1 = nk in courses_q1
                if in_ef and not in_q1:
                    key = f"EF:{nk}"
                elif in_q1 and not in_ef:
                    key = f"Q1:{nk}"
                elif in_ef and in_q1:
                    key = f"EF:{nk}"  # ambiguous: default EF
            if key is None:
                key = raw
        if key in seen:
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

def _ensure_grade_prefix(courses: list[str], default_grade: str = "EF") -> list[str]:
    """If no grade prefixes are present, prefix all courses with the given default grade."""
    if not isinstance(courses, list):
        return []
    has_prefix = any(isinstance(c, str) and c.strip().upper().startswith(("EF:", "Q1:")) for c in courses)
    out: list[str] = []
    for c in courses:
        if not isinstance(c, str):
            c = str(c or "")
        raw = c.strip()
        if not raw:
            continue
        if has_prefix:
            out.append(raw)
            continue
        nk = norm_key(raw)
        out.append(f"{default_grade}:{nk}" if nk else raw)
    return out

def _load_profile_for_user(row):
    if not row:
        return _empty_profile()
    raw = row["profile_json"] if isinstance(row, sqlite3.Row) else row.get("profile_json")
    try:
        payload = json.loads(raw) if raw else {}
    except (TypeError, json.JSONDecodeError):
        payload = {}
    prof = _normalise_profile(payload)
    prof["courses"] = _ensure_grade_prefix(prof.get("courses") or [], "EF")
    return prof

def _get_setting(key, default=None):
    db = get_db()
    cur = db.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    if row and row["value"] is not None:
        return row["value"]
    return SETTINGS_DEFAULTS.get(key, default)


def _setting_as_bool(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _update_banner_payload() -> dict | None:
    """Return banner payload for clients or None if disabled/empty."""
    message = str(_get_setting("updateBannerText", "") or "").strip()
    enabled = _setting_as_bool(_get_setting("updateBannerEnabled", "0"))
    updated_raw = _get_setting("updateBannerUpdatedAt", "0")
    try:
        updated_at = int(updated_raw)
    except Exception:
        updated_at = 0
    if not message or not enabled:
        return None
    version = str(updated_at or "").strip() or message
    return {"message": message, "enabled": True, "updatedAt": updated_at, "version": version}


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
        if key == "updateBannerEnabled":
            value = "1" if _setting_as_bool(value) else "0"
        if key == "updateBannerUpdatedAt":
            try:
                value = str(int(value))
            except Exception:
                value = str(int(time.time()))
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

def _hm_from_str(s: str) -> str:
    try:
        parts = str(s or "").strip().split(":")
        if len(parts) == 2:
            h = int(parts[0]); m = int(parts[1])
            return f"{h:02d}:{m:02d}"
        num = int(float(s))
        return f"{num // 100:02d}:{num % 100:02d}"
    except Exception:
        return ""

def _split_rooms(value) -> list[str]:
    rooms: list[str] = []
    if isinstance(value, str):
        rooms.extend([p.strip() for p in value.split(",")])
    elif isinstance(value, list):
        for v in value:
            if isinstance(v, str):
                rooms.extend([p.strip() for p in v.split(",")])
            else:
                rooms.append(str(v or "").strip())
    else:
        rooms.append(str(value or "").strip())
    return [r for r in rooms if r]

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
    # keep GK/LK/AG markers to distinguish course types (previously stripped)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())

def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp

#  ---------- Mapping I/O ----------
COURSE_MAP_PATH_EF = os.path.join(ROOT, "course_mapping_ef.txt")
COURSE_MAP_PATH_Q1 = os.path.join(ROOT, "course_mapping_q1.txt")
COURSE_MAP_PATHS   = {"EF": COURSE_MAP_PATH_EF, "Q1": COURSE_MAP_PATH_Q1}
ROOM_MAP_PATH   = os.path.join(DATA_DIR, "rooms_mapping.txt")
LEGACY_COURSE_MAP_PATH = os.path.join(DATA, "course_mapping.txt")
LEGACY_ROOM_MAP_PATH   = os.path.join(DATA, "rooms_mapping.txt")

def _bootstrap_data_file(preferred: str, legacy: str | None = None) -> None:
    """Ensure preferred file exists by copying a legacy one if present."""
    os.makedirs(os.path.dirname(preferred) or ".", exist_ok=True)
    if os.path.exists(preferred):
        return
    if legacy and os.path.exists(legacy):
        try:
            shutil.copyfile(legacy, preferred)
        except Exception:
            pass

for _p in COURSE_MAP_PATHS.values():
    _bootstrap_data_file(_p, LEGACY_COURSE_MAP_PATH)
_bootstrap_data_file(ROOM_MAP_PATH, LEGACY_ROOM_MAP_PATH)

def _mirror_to_legacy(source: str, legacy: str | None = None) -> None:
    """Best-effort copy back to legacy path for fallbacks (txt fallback in frontend)."""
    if not legacy:
        return
    try:
        if os.path.exists(source):
            shutil.copyfile(source, legacy)
    except Exception:
        pass

for _p in COURSE_MAP_PATHS.values():
    _mirror_to_legacy(_p, LEGACY_COURSE_MAP_PATH)
_mirror_to_legacy(ROOM_MAP_PATH, LEGACY_ROOM_MAP_PATH)

def _load_raw_subjects_for_grade(grade: str) -> list[str]:
    fname = os.path.join(DATA_DIR, f"subjects_raw_{grade.lower()}.txt")
    out: list[str] = []
    try:
        with open(fname, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#"):
                    out.append(s)
    except FileNotFoundError:
        pass
    return out

def _load_cached_lessons_for_grade(grade: str) -> list[dict]:
    fname = os.path.join(DATA_DIR, "exports", f"lessons_mapped_{grade.lower()}.json")
    try:
        with open(fname, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                lessons = []
                for L in data:
                    if isinstance(L, dict):
                        L = dict(L)
                        L.setdefault("grade", grade)
                        lessons.append(L)
                return lessons
    except Exception:
        pass
    return []

def load_mapping_txt(path):
    """Return dict {lhs(normalized or raw key): rhs(display)} including empty rhs.

    Supports both key=value (legacy) and JSON with top-level grade blocks:
    {"EF": { raw: label, ... }, "Q1": {...}}
    """
    data: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        stripped = content.lstrip()
        # JSON grade-aware format
        if stripped.startswith("{") and ":" in stripped:
            try:
                obj = json.loads(content)
                if isinstance(obj, dict):
                    for grade, mapping in obj.items():
                        if not isinstance(mapping, dict):
                            continue
                        for lhs, rhs in mapping.items():
                            key = f"{grade}:{lhs}".strip()
                            data[key] = str(rhs or "").strip()
                    return data
            except Exception:
                pass  # fall back to legacy parsing
        # legacy key=value lines
        for line in content.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            lhs, rhs = s.split("=", 1)
            lhs = lhs.strip()
            rhs = rhs.strip()
            data[lhs] = rhs
    except FileNotFoundError:
        pass
    return data

def _parse_mapping(file_path: str) -> dict[str, str]:
    """Read mapping file; index by normalised key on the left. Empty right is allowed.

    Supports legacy key=value and JSON grade blocks; for grade blocks the grade
    prefix is ignored for subject mapping, we index by normalised raw label only.
    """
    mapping: dict[str, str] = {}
    if not os.path.exists(file_path):
        return mapping
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    stripped = content.lstrip()
    if stripped.startswith("{") and ":" in stripped:
        try:
            obj = json.loads(content)
            if isinstance(obj, dict):
                for grade_map in obj.values():
                    if not isinstance(grade_map, dict):
                        continue
                    for left, right in grade_map.items():
                        mapping[norm_key(left)] = str(right or "").strip()
                return mapping
        except Exception:
            pass
    for line in content.splitlines():
        line = line.strip()
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

# course mapping helpers (per-grade files, merged views)
def _course_map_path_for_grade(grade: str) -> str | None:
    return COURSE_MAP_PATHS.get((grade or "").upper())

def _course_raw_map_for_grade(grade: str) -> dict[str, str]:
    path = _course_map_path_for_grade(grade)
    if path:
        return load_mapping_txt(path)
    # Unknown grade: do not cross-mix
    return {}

def _course_map_normalized_for_grade(grade: str) -> dict[str, str]:
    path = _course_map_path_for_grade(grade)
    if path:
        return _parse_mapping(path)
    return {}

def _course_map_normalized_all() -> dict[str, str]:
    merged: dict[str, str] = {}
    for p in COURSE_MAP_PATHS.values():
        merged.update(_parse_mapping(p))
    return merged

def _course_map_normalized_for_grade(grade: str) -> dict[str, str]:
    path = _course_map_path_for_grade(grade)
    if path:
        return _parse_mapping(path)
    return {}

def _course_map_write_all(mapping: dict[str, str]) -> None:
    for p in COURSE_MAP_PATHS.values():
        _write_mapping_txt(p, mapping)
        _mirror_to_legacy(p, LEGACY_COURSE_MAP_PATH)

def _course_map_write_for_grade(grade: str, mapping: dict[str, str]) -> None:
    """Persist a mapping only for the given grade, with legacy mirror."""
    path = _course_map_path_for_grade(grade)
    if not path:
        return
    _write_mapping_txt(path, mapping)
    _mirror_to_legacy(path, LEGACY_COURSE_MAP_PATH)

# ---------- Seen keys (store raw & normalised) ----------
SEEN_SUB_RAW_PATH = os.path.join(DATA_DIR, "seen_subjects_raw.json")
SEEN_ROOM_RAW_PATH = os.path.join(DATA_DIR, "seen_rooms_raw.json")
LEGACY_SEEN_SUB = os.path.join(DATA, "seen_subjects_raw.json")
LEGACY_SEEN_ROOM = os.path.join(DATA, "seen_rooms_raw.json")

_bootstrap_data_file(SEEN_SUB_RAW_PATH, LEGACY_SEEN_SUB)
_bootstrap_data_file(SEEN_ROOM_RAW_PATH, LEGACY_SEEN_ROOM)

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

def record_seen_rooms_from_exams(exams: list[dict]):
    """Capture room variants from exams (manual or remote)."""
    global _last_seen_flush
    if not exams:
        return
    changed = False
    for e in exams:
        if not isinstance(e, dict):
            continue
        rooms = []
        if "rooms" in e:
            rlist = e.get("rooms")
            if isinstance(rlist, list):
                rooms.extend([str(r or "").strip() for r in rlist])
        if "room" in e:
            rooms.extend(_split_rooms(e.get("room")))
        for r in rooms:
            r = (r or "").strip()
            if r and r not in SEEN_ROOMS_RAW:
                SEEN_ROOMS_RAW.append(r)
                changed = True
    now = time.time()
    if changed and (now - _last_seen_flush > 15):
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

def _exam_key(start: date, end: date, exam_type: int, grades: list[str] | tuple[str, ...] | None = None) -> str:
    grade_part = "ALL"
    if grades:
        norm = {str(g or "").strip().upper() for g in grades if str(g or "").strip()}
        if norm:
            grade_part = ",".join(sorted(norm))
    return f"{start.isoformat()}_{end.isoformat()}_{exam_type}_{grade_part}"

# ---- Manual exams (admin-managed) ----
def _clean_str(value) -> str:
    return str(value or "").strip()

def _clean_list_str(values) -> list[str]:
    out: list[str] = []
    if isinstance(values, list):
        for v in values:
            s = _clean_str(v)
            if s:
                out.append(s)
    return out

def _normalise_hm(value: str) -> str:
    return _hm_from_str(value) or _hm_from_str(_hm_from_int(value))

def _normalize_manual_exam_input(data: dict) -> dict | None:
    if not isinstance(data, dict):
        return None
    subj = _clean_str(data.get("subject"))
    date_iso = _clean_str(data.get("date"))
    start_hm = _normalise_hm(data.get("start_time") or data.get("start") or data.get("startTime"))
    end_hm   = _normalise_hm(data.get("end_time")   or data.get("end")   or data.get("endTime"))
    if not subj or not date_iso or not start_hm or not end_hm:
        return None
    name = _clean_str(data.get("name")) or subj
    classes = _clean_list_str(data.get("classes"))
    teachers = _clean_list_str(data.get("teachers"))
    room = _clean_str(data.get("room"))
    rooms = _split_rooms(room or data.get("rooms") or [])
    room_label = ", ".join(_clean_list_str(rooms) or ([] if not room else [room]))
    note = _clean_str(data.get("note"))
    grade = _clean_str(data.get("grade")).upper()
    return {
        "subject": subj,
        "name": name,
        "date": date_iso,
        "start_time": start_hm,
        "end_time": end_hm,
        "classes": classes,
        "teachers": teachers,
        "room": room_label,
        "rooms": _clean_list_str(rooms),
        "note": note,
        "grade": grade,
    }

def _row_to_manual_exam(row) -> dict:
    try:
        classes = json.loads(row["classes_json"]) if row.get("classes_json") else []
    except Exception:
        classes = []
    try:
        teachers = json.loads(row["teachers_json"]) if row.get("teachers_json") else []
    except Exception:
        teachers = []
    rooms = _split_rooms(row.get("room"))
    return {
        "id": f"manual-{row.get('id')}",
        "subject": row.get("subject") or "",
        "name": row.get("name") or row.get("subject") or "Klausur",
        "date": row.get("date") or "",
        "start": row.get("start_time") or "",
        "end": row.get("end_time") or "",
        "classes": classes,
        "teachers": teachers,
        "room": row.get("room") or "",
        "rooms": rooms,
        "note": row.get("note") or "",
        "grade": (row.get("grade") or "").strip().upper(),
        "source": "manual",
    }

def _load_manual_exams(start: date, end: date) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT id, subject, name, date, start_time, end_time, classes_json, teachers_json, room, note, grade FROM exams_manual WHERE date BETWEEN ? AND ? ORDER BY date, start_time",
        (start.isoformat(), end.isoformat())
    ).fetchall()
    return [_row_to_manual_exam(dict(r)) for r in rows]

# ---------------- Routes ----------------
@app.after_request
def add_no_cache(resp):
    return _no_store(resp)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/mappings")
def api_mappings():
    course_map = _course_map_normalized_all()
    room_map   = _parse_mapping(ROOM_MAP_PATH)
    return _no_store(jsonify({"ok": True, "courses": course_map, "rooms": room_map}))

@app.route("/api/courses")
def api_courses():
    """Return course options (key + display label) from course_mapping.txt.

    Key: grade-prefixed normalised LHS (GRADE:norm_key). Label: RHS if present, else original LHS.
    """
    def _label_for(raw_left: str, raw_map: dict[str, str]) -> tuple[str, str] | None:
        left = (raw_left or "").strip()
        label = (raw_map.get(raw_left, "") or "").strip() or left
        nk = norm_key(left)
        if not nk:
            return None
        return nk, label

    def _options_for_grade(grade: str) -> dict[str, str]:
        """Build per-grade options so EF/Q1 stay separated."""
        opts: dict[str, str] = {}
        raw_map = _course_raw_map_for_grade(grade)
        for left in raw_map.keys():
            pair = _label_for(left, raw_map)
            if pair:
                nk, label = pair
                opts[nk] = label
        for raw_subj in _load_raw_subjects_for_grade(grade):
            pair = _label_for(raw_subj, raw_map)
            if pair:
                nk, label = pair
                opts.setdefault(nk, label)
        return opts

    grades = available_grades() or ["EF"]
    items: list[dict] = []
    for grade in grades:
        grade_opts = _options_for_grade(grade)
        for key in sorted(grade_opts.keys(), key=lambda k: (grade_opts[k].lower(), grade_opts[k])):
            items.append({"key": f"{grade}:{key}", "label": grade_opts[key], "grade": grade})
    return _no_store(jsonify({"ok": True, "courses": items}))

@app.route("/api/health")
def api_health():
    return no_store(make_response(jsonify({"ok": True}), 200))

@app.route("/api/update-banner")
def api_update_banner():
    payload = _update_banner_payload()
    return _no_store(jsonify({"ok": True, "updateBanner": payload}))

@app.route("/api/timetable")
def api_timetable():
    try:
        return _api_timetable_impl()
    except Exception as exc:
        app.logger.exception("timetable failed")
        # Fallback to last good payload if available
        if LAST_GOOD:
            fallback = dict(LAST_GOOD)
            fallback["ok"] = True
            fallback["error"] = f"served cached timetable because of: {exc}"
            return _no_store(jsonify(fallback))
        return jsonify({"ok": False, "error": "timetable_failed"}), 500

def _api_timetable_impl():
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
    banner_payload = _update_banner_payload()
    settings_payload = {
        "timeColumnWidth": width_value,
        "updateBanner": banner_payload,
    }

    lessons: list[dict] = []
    errors: list[str] = []
    grades = available_grades()
    if not grades:
        grades = ["EF"]
    for grade in grades:
        try:
            grade_lessons = fetch_week(ws, grade)
            for L in grade_lessons:
                L["grade"] = grade
            lessons.extend(grade_lessons)
        except Exception as e:
            msg = f"{grade}: {e}"
            errors.append(msg)
            app.logger.warning("fetch_week failed for %s: %s", grade, e)
            cached = _load_cached_lessons_for_grade(grade)
            if cached:
                lessons.extend(cached)
                errors[-1] = msg + " (served cached lessons)"

    if errors and not lessons:
        payload = {
            "ok": False,
            "weekStart": str(ws),
            "lessons": [],
            "error": "; ".join(errors),
            "settings": settings_payload,
            "grades": grades,
        }
        _last_weekkey_payload[weekkey] = payload
        _last_weekkey_ts[weekkey] = time.time()
        return _no_store(jsonify(payload))

    # remember raw variants for admin UI
    record_seen_raw(lessons)

    # optionally enrich with debug mapping fields
    if debug:
        # per-lesson mapping lookup by its grade to avoid cross mixing
        rmap = _parse_mapping(ROOM_MAP_PATH)
        for L in lessons:
            sr = (L.get("subject_original") or L.get("subject") or "")
            rr = (L.get("room") or "")
            sn = norm_key(sr); rn = norm_key(rr)
            cmap = _course_map_normalized_for_grade(L.get("grade"))
            L["debug"] = {
                "subject_raw": sr, "subject_norm": sn, "mapped_subject": cmap.get(sn),
                "room_raw": rr,    "room_norm": rn,    "mapped_room": rmap.get(rn),
                "server_now": datetime.now(APP_TZ).isoformat(), "week_start": ws.isoformat()
            }

    payload = {
        "ok": True,
        "weekStart": str(ws),
        "lessons": lessons,
        "settings": settings_payload,
        "updateBanner": banner_payload,
        "grades": grades,
        "errors": errors if errors else [],
    }
    _last_weekkey_payload[weekkey] = payload
    _last_weekkey_ts[weekkey] = time.time()
    save_last_good({**payload, "_cachedAt": time.time()})
    return _no_store(jsonify(payload))

@app.route("/api/exams")
def api_exams():
    today = datetime.now(APP_TZ).date()
    start_raw = request.args.get("start")
    end_raw   = request.args.get("end")
    type_raw  = request.args.get("type") or request.args.get("examTypeId") or "0"
    grade_raw = request.args.get("grade") or request.args.get("grades") or ""
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

    requested_grades: list[str] = []
    if grade_raw:
        for part in grade_raw.replace(";", ",").split(","):
            val = part.strip().upper()
            if val:
                requested_grades.append(val)
    available = available_grades() or ["EF"]
    grades = [g for g in requested_grades if g in available] if requested_grades else available
    if not grades:
        grades = available

    cache_key = _exam_key(start, end, exam_type, grades)
    now_ts = time.time()
    if not force and cache_key in _last_exam_payload and (now_ts - _last_exam_key_ts.get(cache_key, 0)) < 15:
        return _no_store(jsonify(_last_exam_payload[cache_key]))

    manual_exams: list[dict] = []
    try:
        manual_exams = _load_manual_exams(start, end)
    except Exception:
        manual_exams = []

    def _norm_exam(rec, grade_label: str, subjects: dict, classes: dict, teachers: dict):
        if not isinstance(rec, dict):
            return None
        eid = rec.get("id") or rec.get("examId") or rec.get("exam_id")
        date_val = rec.get("examDate") or rec.get("date")
        date_iso = _date_int_to_iso(date_val)
        start_hm = rec.get("start") or rec.get("startTime")
        end_hm   = rec.get("end") or rec.get("endTime")
        start_hm = start_hm if isinstance(start_hm, str) and ":" in start_hm else _hm_from_int(start_hm)
        end_hm   = end_hm if isinstance(end_hm, str) and ":" in end_hm else _hm_from_int(end_hm)
        subj_id = rec.get("subjectId") or rec.get("subject")
        subj_name = rec.get("subjectName") or rec.get("subject") or subjects.get(subj_id, "")
        name = rec.get("name") or rec.get("title") or subj_name

        class_ids = rec.get("classes") or rec.get("classIds") or []
        class_labels: list[str] = []
        if isinstance(class_ids, list) and class_ids and all(isinstance(cid, int) for cid in class_ids):
            class_labels = [classes.get(cid, "") for cid in class_ids if cid]
        elif isinstance(rec.get("studentClass"), list):
            class_labels = [str(c or "").strip() for c in rec.get("studentClass") if str(c or "").strip()]

        teach_ids = rec.get("teacherIds") or rec.get("teachers") or []
        teacher_labels: list[str] = []
        if isinstance(teach_ids, list) and teach_ids and all(isinstance(tid, int) for tid in teach_ids):
            teacher_labels = [teachers.get(tid, "") for tid in teach_ids if tid]
        elif isinstance(rec.get("teachers"), list):
            teacher_labels = [str(t or "").strip() for t in rec.get("teachers") if str(t or "").strip()]

        rooms_list: list[str] = []
        room_label = ""
        if isinstance(rec.get("rooms"), list):
            rooms_list = [str(r or "").strip() for r in rec.get("rooms") if str(r or "").strip()]
            room_label = ", ".join(rooms_list)
        if not room_label and rec.get("room"):
            room_label = str(rec.get("room") or "").strip()

        if not eid:
            eid = f"rest-{date_iso}-{subj_name}-{start_hm}-{end_hm}"
        return {
            "id": eid,
            "grade": grade_label,
            "date": date_iso,
            "start": start_hm,
            "end": end_hm,
            "subject": subj_name,
            "subjectId": subj_id,
            "classIds": class_ids if isinstance(class_ids, list) else [],
            "classes": class_labels,
            "teacherIds": teach_ids,
            "teachers": teacher_labels,
            "name": name,
            "rooms": rooms_list,
            "room": room_label,
            "note": rec.get("text") or rec.get("note") or "",
        }

    exams_remote: list[dict] = []
    warnings: list[str] = []
    fetch_failed = False
    permission_denied = False

    for grade in grades:
        try:
            raw_exams = fetch_exams(start, end, exam_type, grade) or []
            subjects  = fetch_subject_map(grade)
            classes   = fetch_class_map(grade)
            teachers  = fetch_teacher_map(grade)
        except Exception as e:
            msg = str(e)
            fetch_failed = True
            if "no right" in msg.lower() or "-8509" in msg:
                permission_denied = True
            warnings.append(f"{grade}: {msg}")
            app.logger.warning("fetch_exams failed for %s: %s", grade, msg)
            continue

        normed = [_norm_exam(rec, grade, subjects, classes, teachers) for rec in raw_exams]
        exams_remote.extend([e for e in normed if e and e.get("date")])
        try:
            record_seen_rooms_from_exams(raw_exams)
        except Exception:
            pass

    exams = manual_exams + exams_remote
    try:
        record_seen_rooms_from_exams(manual_exams)
    except Exception:
        pass

    payload = {
        "ok": True,
        "start": str(start),
        "end": str(end),
        "examType": exam_type,
        "grades": grades,
        "exams": exams,
    }
    if warnings:
        payload["warning"] = "; ".join(warnings)
        payload["warnings"] = warnings
        if permission_denied:
            payload["errorCode"] = "exam_permission_denied"
        elif fetch_failed:
            payload["errorCode"] = "exam_fetch_failed"
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
    session.permanent = True
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
    session.permanent = True
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
        profile = _load_profile_for_user(row)
        # fallback: if empty courses, try last imported backup for this user
        if not profile.get("courses"):
            backup_prof = _backup_profile_for(row["username"])
            if backup_prof:
                profile = backup_prof
                _save_profile(user_id, profile)
        payload = {
            "ok": True,
            "profile": profile,
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
            session.permanent = True
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
            prof = _load_profile_for_user(row)
            users.append({
                "id": row["id"],
                "username": row["username"],
                "password_hash": row["password_hash"],
                "password_plain": row["password_plain"],
                "profile": prof,
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

    exams_manual = []
    try:
        cur = db.execute(
            "SELECT id, subject, name, date, start_time, end_time, classes_json, teachers_json, room, note, grade, created_at FROM exams_manual ORDER BY date, start_time, id"
        )
        for row in cur.fetchall():
            exams_manual.append({
                "id": row["id"],
                "subject": row["subject"],
                "name": row["name"],
                "date": row["date"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "classes": json.loads(row["classes_json"] or "[]"),
                "teachers": json.loads(row["teachers_json"] or "[]"),
                "room": row["room"],
                "note": row["note"],
                "grade": str(row["grade"] if "grade" in row.keys() else "").strip().upper(),
                "created_at": row["created_at"],
            })
    except Exception:
        exams_manual = []

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
            "exams_manual": exams_manual,
            "settings": settings_map,
        },
        "mappings": {
            # keep legacy merged view plus grade-specific maps for clarity
            "courses": _course_map_normalized_all(),
            "courses_ef": _course_map_normalized_for_grade("EF"),
            "courses_q1": _course_map_normalized_for_grade("Q1"),
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

    exams_manual_norm = []
    exams_manual_in = db_section.get("exams_manual") if isinstance(db_section, dict) else []
    if isinstance(exams_manual_in, list):
        for entry in exams_manual_in:
            if not isinstance(entry, dict):
                continue
            subj = (entry.get("subject") or "").strip()
            date_iso = (entry.get("date") or "").strip()
            start_hm = _normalise_hm(entry.get("start_time") or entry.get("start"))
            end_hm = _normalise_hm(entry.get("end_time") or entry.get("end"))
            if not subj or not date_iso or not start_hm or not end_hm:
                continue
            name = (entry.get("name") or "").strip() or subj
            try:
                exam_id = int(entry.get("id"))
            except (TypeError, ValueError):
                exam_id = None
            classes = _clean_list_str(entry.get("classes") if isinstance(entry.get("classes"), list) else [])
            teachers = _clean_list_str(entry.get("teachers") if isinstance(entry.get("teachers"), list) else [])
            room = (entry.get("room") or "").strip()
            note = (entry.get("note") or "").strip()
            created_at = entry.get("created_at") or datetime.utcnow().isoformat()
            grade = (entry.get("grade") or "").strip().upper()
            exams_manual_norm.append((exam_id, subj, name, date_iso, start_hm, end_hm, json.dumps(classes), json.dumps(teachers), room, note, grade, created_at))

    courses_map = {}
    courses = mappings_section.get("courses")
    if isinstance(courses, dict):
        for k, v in courses.items():
            nk = norm_key(k)
            courses_map[nk] = (v or "").strip()
    # grade-specific maps, if present (preferred)
    courses_map_ef = {}
    courses_map_q1 = {}
    courses_ef_in = mappings_section.get("courses_ef")
    courses_q1_in = mappings_section.get("courses_q1")
    if isinstance(courses_ef_in, dict):
        for k, v in courses_ef_in.items():
            nk = norm_key(k)
            courses_map_ef[nk] = (v or "").strip()
    if isinstance(courses_q1_in, dict):
        for k, v in courses_q1_in.items():
            nk = norm_key(k)
            courses_map_q1[nk] = (v or "").strip()

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
        db.execute("DELETE FROM exams_manual")

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

        for row in exams_manual_norm:
            db.execute(
                "INSERT INTO exams_manual (id, subject, name, date, start_time, end_time, classes_json, teachers_json, room, note, grade, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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

    # persist last backup for fallback logic
    _save_last_backup(payload)

    # write courses: prefer grade-specific maps when provided, otherwise legacy merged
    if courses_map_ef or courses_map_q1:
        if courses_map_ef:
            _course_map_write_for_grade("EF", courses_map_ef)
        if courses_map_q1:
            _course_map_write_for_grade("Q1", courses_map_q1)
        # keep legacy merged in sync for fallbacks
        merged_for_legacy = _course_map_normalized_all()
        _course_map_write_all(merged_for_legacy)
    else:
        _course_map_write_all(courses_map)
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

    courses_ef = _course_map_normalized_for_grade("EF")
    courses_q1 = _course_map_normalized_for_grade("Q1")
    courses = _course_map_normalized_all()
    rooms   = _parse_mapping(ROOM_MAP_PATH)

    groups_sub_ef = _group_variants(_load_raw_subjects_for_grade("EF"))
    groups_sub_q1 = _group_variants(_load_raw_subjects_for_grade("Q1"))
    groups_rm  = _group_variants(SEEN_ROOMS_RAW)

    unmapped_sub_ef = [nk for nk in sorted(groups_sub_ef.keys()) if nk not in courses_ef]
    unmapped_sub_q1 = [nk for nk in sorted(groups_sub_q1.keys()) if nk not in courses_q1]
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
            uid = row["id"] if isinstance(row, dict) else row[0]
            user_rows.append({
                "id": uid,
                "username": username,
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

    exams_manual = []
    try:
        cur = get_db().execute(
            "SELECT id, subject, name, date, start_time, end_time, classes_json, teachers_json, room, note, grade FROM exams_manual ORDER BY date, start_time"
        )
        exams_manual = [_row_to_manual_exam(dict(r)) for r in cur.fetchall()]
    except Exception:
        exams_manual = []

    settings_payload = {key: _get_setting(key, default) for key, default in SETTINGS_DEFAULTS.items()}

    return _no_store(jsonify({
        "ok": True,
        "courses": courses,  # merged legacy view
        "courses_ef": courses_ef,
        "courses_q1": courses_q1,
        "rooms": rooms,
        "subjects_grouped_ef": groups_sub_ef,
        "subjects_grouped_q1": groups_sub_q1,
        "rooms_grouped": groups_rm,
        "unmapped_subjects_ef": unmapped_sub_ef,
        "unmapped_subjects_q1": unmapped_sub_q1,
        "unmapped_rooms": unmapped_rm,
        "users": user_rows,
        "vacations": vacations,
        "settings": settings_payload,
        "exams_manual": exams_manual,
    }))

@app.route("/api/admin/save", methods=["POST"])
def admin_save():
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    new_courses: dict = payload.get("courses") or {}
    new_courses_ef: dict = payload.get("courses_ef") or {}
    new_courses_q1: dict = payload.get("courses_q1") or {}
    new_rooms: dict   = payload.get("rooms") or {}
    new_settings: dict = payload.get("settings") or {}

    wrote_courses = False
    # if grade-specific payload present, handle independently; else legacy path applies to both
    if new_courses_ef or new_courses_q1:
        courses_ef = _course_map_normalized_for_grade("EF")
        courses_q1 = _course_map_normalized_for_grade("Q1")
        for k, v in new_courses_ef.items():
            courses_ef[norm_key(k)] = (v or "").strip()
        for k, v in new_courses_q1.items():
            courses_q1[norm_key(k)] = (v or "").strip()
        _write_mapping_txt(COURSE_MAP_PATH_EF, courses_ef)
        _write_mapping_txt(COURSE_MAP_PATH_Q1, courses_q1)
        _mirror_to_legacy(COURSE_MAP_PATH_EF, LEGACY_COURSE_MAP_PATH)
        _mirror_to_legacy(COURSE_MAP_PATH_Q1, LEGACY_COURSE_MAP_PATH)
        wrote_courses = True

    # legacy merge (apply to both grade files)
    courses = _course_map_normalized_all()
    rooms   = _parse_mapping(ROOM_MAP_PATH)

    # merge (normalise keys, keep RHS exactly as typed; empty allowed)
    for k, v in new_courses.items():
        courses[norm_key(k)] = (v or "").strip()
    for k, v in new_rooms.items():
        rooms[norm_key(k)] = (v or "").strip()

    if not wrote_courses:
        _course_map_write_all(courses)
    _write_mapping_txt(ROOM_MAP_PATH, rooms)

    sanitized_settings = {}
    if isinstance(new_settings, dict):
        for key, value in new_settings.items():
            if key in SETTINGS_DEFAULTS:
                sanitized_settings[key] = str(value)
        # handle update-banner timestamp bump when content/flag changes
        if "updateBannerText" in new_settings or "updateBannerEnabled" in new_settings:
            banner_text = (new_settings.get("updateBannerText") or "").strip()
            banner_enabled = _setting_as_bool(new_settings.get("updateBannerEnabled"))
            current_text = str(_get_setting("updateBannerText", "") or "").strip()
            current_enabled = _setting_as_bool(_get_setting("updateBannerEnabled", "0"))

            sanitized_settings["updateBannerText"] = banner_text
            # Do not show an empty banner even if enabled is true.
            sanitized_settings["updateBannerEnabled"] = "1" if banner_enabled and banner_text else "0"

            if (banner_text != current_text) or (banner_enabled != current_enabled):
                sanitized_settings["updateBannerUpdatedAt"] = str(int(time.time()))
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


@app.route("/api/admin/exams", methods=["GET", "POST"])
def admin_exams():
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    db = get_db()
    if request.method == "GET":
        rows = db.execute(
            "SELECT id, subject, name, date, start_time, end_time, classes_json, teachers_json, room, note, grade FROM exams_manual ORDER BY date, start_time"
        ).fetchall()
        items = [_row_to_manual_exam(dict(r)) for r in rows]
        return _no_store(jsonify({"ok": True, "exams": items}))

    data = request.get_json(silent=True) or {}
    payload = _normalize_manual_exam_input(data)
    if not payload:
        return jsonify({"ok": False, "error": "invalid_input"}), 400
    db.execute(
        """
        INSERT INTO exams_manual (subject, name, date, start_time, end_time, classes_json, teachers_json, room, note, grade)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["subject"],
            payload["name"],
            payload["date"],
            payload["start_time"],
            payload["end_time"],
            json.dumps(payload["classes"]),
            json.dumps(payload["teachers"]),
            payload["room"],
            payload["note"],
            payload["grade"],
        )
    )
    db.commit()
    return _no_store(jsonify({"ok": True}))


@app.route("/api/admin/exams/<int:exam_id>", methods=["DELETE"])
def admin_delete_exam(exam_id: int):
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    db = get_db()
    cur = db.execute("DELETE FROM exams_manual WHERE id = ?", (exam_id,))
    db.commit()
    if cur.rowcount == 0:
        return _no_store(jsonify({"ok": False, "error": "not_found"})), 404
    return _no_store(jsonify({"ok": True, "deleted": exam_id}))

if __name__ == "__main__":
    debug_enabled = str(os.environ.get("FLASK_DEBUG", "")).lower() in ("1", "true", "yes")
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("PORT", "5000"))
    except (TypeError, ValueError):
        port = 5000
    app.run(host=host, port=port, debug=debug_enabled)
