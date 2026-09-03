import os, json, time, re, sqlite3, shutil, requests, threading, base64, binascii, tempfile, hashlib
from datetime import datetime, timedelta, date
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

if load_dotenv:
    load_dotenv(".env")
from flask import (
    Flask, jsonify, make_response, render_template, request,
    redirect, url_for, session, g, send_from_directory
)
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from pywebpush import webpush
except Exception:
    webpush = None

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
    fetch_schoolyear_subjects,
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
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable is required and must not be empty.")
app.config["SECRET_KEY"] = SECRET_KEY
app.config["SESSION_PERMANENT"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

ADMIN_TOKEN        = os.environ.get("ADMIN_TOKEN")
DB_PATH            = os.environ.get("DB_PATH", os.path.join(DATA, "user_data.db"))
AUTO_RESTORE_FORCE   = str(os.environ.get("AUTO_RESTORE_FORCE", "")).strip().lower() in ("1", "true", "yes", "on")
BACKUP_WEBHOOK_URL   = os.environ.get("BACKUP_WEBHOOK_URL")
BACKUP_WEBHOOK_TOKEN = os.environ.get("BACKUP_WEBHOOK_TOKEN")
AUTO_RESTORE_URL     = os.environ.get("AUTO_RESTORE_URL")
AUTO_BACKUP_INTERVAL_MIN = int(os.environ.get("AUTO_BACKUP_INTERVAL_MIN", "5"))
SETTINGS_DEFAULTS  = {
    "timeColumnWidth": "60",
    "updateBannerText": "",
    "updateBannerEnabled": "0",
    "updateBannerUpdatedAt": "0",
    "imageBannerData": "",
    "imageBannerEnabled": "0",
    "imageBannerAlt": "Schulbanner",
    "imageBannerUpdatedAt": "0",
}
BACKUP_VERSION     = 6
SUPPORTED_GRADES   = ("EF", "Q1", "Q2")
IMAGE_BANNER_WIDTH = 1600
IMAGE_BANNER_HEIGHT = 400
IMAGE_BANNER_MAX_BYTES = 1536 * 1024
IMAGE_BANNER_MAX_REQUEST_BYTES = 3 * 1024 * 1024
VAPID_PUBLIC_KEY = str(os.environ.get("VAPID_PUBLIC_KEY") or "").strip()
VAPID_PRIVATE_KEY = str(os.environ.get("VAPID_PRIVATE_KEY") or "").strip()
VAPID_SUBJECT = str(os.environ.get("VAPID_SUBJECT") or "").strip()
NOTIFICATION_MONITOR_ENABLED = str(
    os.environ.get("NOTIFICATION_MONITOR_ENABLED") or ""
).strip().lower() in ("1", "true", "yes", "on")
try:
    NOTIFICATION_CHECK_INTERVAL_SECONDS = int(
        os.environ.get("NOTIFICATION_CHECK_INTERVAL_SECONDS", "300")
    )
except (TypeError, ValueError):
    NOTIFICATION_CHECK_INTERVAL_SECONDS = 300
NOTIFICATION_CHECK_INTERVAL_SECONDS = max(30, min(3600, NOTIFICATION_CHECK_INTERVAL_SECONDS))
SQLITE_BUSY_TIMEOUT_MS = 30_000

NOTIFICATION_PREFERENCES_DEFAULTS = {
    "enabled": True,
    "timetableChanges": True,
    "cancellations": True,
    "additions": True,
    "roomChanges": True,
    "timeChanges": True,
    "otherChanges": True,
    "examReminders": True,
    "examReminderDays": 1,
    "dailySummary": False,
    "dailySummaryTime": "18:00",
}

if not ADMIN_TOKEN:
    raise RuntimeError("ADMIN_TOKEN environment variable is required and must not be empty.")

def _ensure_db_path() -> None:
    """Make sure DB directory exists and is writable (SQLite only)."""
    db_dir = os.path.dirname(DB_PATH) or "."
    try:
        os.makedirs(db_dir, exist_ok=True)
        fd, test_path = tempfile.mkstemp(prefix=".db_write_test_", dir=db_dir)
        os.close(fd)
        os.remove(test_path)
    except Exception as exc:
        raise RuntimeError(f"Database path not writable: {DB_PATH} ({exc})")


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    _ensure_db_path()
    conn = _connect_db()
    # Render's ephemeral filesystem does not keep SQLite WAL sidecar files
    # reliable across instance lifecycle events. Use the rollback journal and
    # keep notification transactions short instead.
    journal_mode = str(conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).lower()
    if journal_mode != "delete":
        app.logger.warning("SQLite DELETE journal unavailable; active mode is %s", journal_mode)
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
    conn.execute("UPDATE users SET password_plain = NULL WHERE password_plain IS NOT NULL")
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            user_agent TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user_id ON push_subscriptions(user_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_snapshots (
            grade TEXT NOT NULL,
            week_start TEXT NOT NULL,
            lessons_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (grade, week_start)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_deliveries (
            user_id INTEGER NOT NULL,
            event_key TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, event_key),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_runtime (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
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
        conn = _connect_db()
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
    return {
        "name": "",
        "grade": "",
        "courses": [],
        "klausuren": [],
        "colors": {"theme": {}, "subjects": {}},
        "notificationPreferences": dict(NOTIFICATION_PREFERENCES_DEFAULTS),
    }

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

def _normalise_grade(value) -> str:
    grade = str(value or "").strip().upper()
    return grade if grade in SUPPORTED_GRADES else ""

def _course_grade_and_body(value: str) -> tuple[str, str]:
    """Return an explicit grade and the unprefixed course value, if present."""
    raw = str(value or "").strip()
    if not raw:
        return "", ""
    prefix = re.match(r"^(EF|Q1|Q2)\s*:\s*(.+)$", raw, flags=re.IGNORECASE)
    if prefix:
        return prefix.group(1).upper(), prefix.group(2).strip()
    suffix = re.match(r"^(.+?)\s*\((EF|Q1|Q2)\)\s*$", raw, flags=re.IGNORECASE)
    if suffix:
        return suffix.group(2).upper(), suffix.group(1).strip()
    tail = re.match(r"^(.+?)\s+(EF|Q1|Q2)\s*$", raw, flags=re.IGNORECASE)
    if tail:
        return tail.group(2).upper(), tail.group(1).strip()
    return "", raw

def _normalise_profile_courses(values, profile_grade: str = "") -> tuple[list[str], str]:
    """Normalise course keys while preserving every explicit grade prefix."""
    courses = _normalise_courses(values)
    selected_grade = _normalise_grade(profile_grade)
    explicit_grades = {
        course_grade
        for course_grade, _ in (_course_grade_and_body(value) for value in courses)
        if course_grade
    }
    if not selected_grade and len(explicit_grades) == 1:
        selected_grade = next(iter(explicit_grades))

    out: list[str] = []
    seen: set[str] = set()
    for value in courses:
        course_grade, body = _course_grade_and_body(value)
        nk = norm_key(body)
        if not nk:
            continue
        if course_grade:
            # Keep mixed legacy selections visible so the client can warn and
            # let the user choose which grade to retain.
            key = f"{course_grade}:{nk}"
        elif selected_grade:
            key = f"{selected_grade}:{nk}"
        else:
            # Keep ambiguous legacy values ungraded so they cannot be assigned
            # to EF merely because EF happens to be first.
            key = nk
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out, selected_grade

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


def _normalise_notification_preferences(value) -> dict:
    source = value if isinstance(value, dict) else {}
    preferences = dict(NOTIFICATION_PREFERENCES_DEFAULTS)
    for key in (
        "enabled",
        "timetableChanges",
        "cancellations",
        "additions",
        "roomChanges",
        "timeChanges",
        "otherChanges",
        "examReminders",
        "dailySummary",
    ):
        if key in source:
            preferences[key] = bool(source[key])

    try:
        reminder_days = int(source.get("examReminderDays", preferences["examReminderDays"]))
    except (TypeError, ValueError):
        reminder_days = preferences["examReminderDays"]
    preferences["examReminderDays"] = max(0, min(14, reminder_days))

    summary_time = str(source.get("dailySummaryTime") or preferences["dailySummaryTime"]).strip()
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", summary_time)
    preferences["dailySummaryTime"] = summary_time if match else NOTIFICATION_PREFERENCES_DEFAULTS["dailySummaryTime"]
    return preferences

def _normalise_profile(payload):
    if not isinstance(payload, dict):
        payload = {}
    profile = _empty_profile()
    profile["name"] = str(payload.get("name") or "").strip()
    profile["courses"], profile["grade"] = _normalise_profile_courses(
        payload.get("courses"),
        payload.get("grade"),
    )
    profile["klausuren"] = _normalise_klausuren(payload.get("klausuren"))
    profile["colors"] = _normalise_colors(payload.get("colors"))
    profile["notificationPreferences"] = _normalise_notification_preferences(
        payload.get("notificationPreferences")
    )
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


def _image_banner_payload(include_disabled: bool = False) -> dict | None:
    image_data = str(_get_setting("imageBannerData", "") or "").strip()
    if not image_data:
        return None
    enabled = _setting_as_bool(_get_setting("imageBannerEnabled", "0"))
    if not enabled and not include_disabled:
        return None
    updated_raw = _get_setting("imageBannerUpdatedAt", "0")
    try:
        updated_at = int(updated_raw)
    except (TypeError, ValueError):
        updated_at = 0
    alt = str(_get_setting("imageBannerAlt", "Schulbanner") or "").strip()
    version = str(updated_at or int(time.time()))
    return {
        "enabled": enabled,
        "alt": alt,
        "updatedAt": updated_at,
        "version": version,
        "width": IMAGE_BANNER_WIDTH,
        "height": IMAGE_BANNER_HEIGHT,
        "imageUrl": f"/api/banner-image?v={version}",
    }


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read JPEG dimensions without decoding the full image."""
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        return None
    offset = 2
    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            break
        segment_length = int.from_bytes(data[offset:offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            break
        if marker in sof_markers and segment_length >= 7:
            height = int.from_bytes(data[offset + 3:offset + 5], "big")
            width = int.from_bytes(data[offset + 5:offset + 7], "big")
            return width, height
        offset += segment_length
    return None


def _decode_banner_image(data_url: str) -> bytes:
    prefix = "data:image/jpeg;base64,"
    raw = str(data_url or "").strip()
    if not raw.startswith(prefix):
        raise ValueError("banner_image_must_be_jpeg")
    try:
        image_bytes = base64.b64decode(raw[len(prefix):], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("banner_image_invalid") from exc
    if not image_bytes or len(image_bytes) > IMAGE_BANNER_MAX_BYTES:
        raise ValueError("banner_image_too_large")
    if _jpeg_dimensions(image_bytes) != (IMAGE_BANNER_WIDTH, IMAGE_BANNER_HEIGHT):
        raise ValueError("banner_image_wrong_dimensions")
    return image_bytes


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
        if key in ("updateBannerEnabled", "imageBannerEnabled"):
            value = "1" if _setting_as_bool(value) else "0"
        if key in ("updateBannerUpdatedAt", "imageBannerUpdatedAt"):
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
COURSE_MAP_PATH_Q2 = os.path.join(ROOT, "course_mapping_q2.txt")
COURSE_MAP_PATHS   = {"EF": COURSE_MAP_PATH_EF, "Q1": COURSE_MAP_PATH_Q1, "Q2": COURSE_MAP_PATH_Q2}
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
    # A legacy map has no grade ownership, so copying it into every grade would
    # make all EF/Q1/Q2 subjects appear interchangeable.
    _bootstrap_data_file(_p)
_bootstrap_data_file(ROOM_MAP_PATH, LEGACY_ROOM_MAP_PATH)

def _load_raw_subjects_for_grade(grade: str) -> list[str]:
    fname = os.path.join(DATA_DIR, f"subjects_raw_{grade.lower()}.txt")
    out: list[str] = []
    schoolyear: str | None = None
    try:
        with open(fname, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.lower().startswith("# schoolyear:"):
                    schoolyear = s.split(":", 1)[1].strip()
                elif s and not s.startswith("#"):
                    out.append(s)
    except FileNotFoundError:
        pass
    if schoolyear != _current_schoolyear_label():
        return []
    return out


def _current_schoolyear_label(today: date | None = None) -> str:
    today = today or datetime.now(APP_TZ).date()
    start_year = today.year if today.month >= 8 else today.year - 1
    return f"{start_year}/{start_year + 1}"


SUBJECT_CATALOG_TTL_SECONDS = 6 * 60 * 60
SUBJECT_CATALOG_RETRY_SECONDS = 5 * 60
_subject_catalog_cache: dict[str, list[str]] = {}
_subject_catalog_cached_at: dict[str, float] = {}
_subject_catalog_live: dict[str, bool] = {}
_subject_catalog_lock = threading.Lock()


def _current_subjects_for_grade(grade: str) -> list[str]:
    """Return the current school-year catalog for exactly one grade."""
    grade = _normalise_grade(grade)
    if not grade:
        return []

    now = time.time()
    cached_at = _subject_catalog_cached_at.get(grade, 0.0)
    ttl = (
        SUBJECT_CATALOG_TTL_SECONDS
        if _subject_catalog_live.get(grade, False)
        else SUBJECT_CATALOG_RETRY_SECONDS
    )
    if grade in _subject_catalog_cache and now - cached_at < ttl:
        return list(_subject_catalog_cache[grade])

    with _subject_catalog_lock:
        now = time.time()
        cached_at = _subject_catalog_cached_at.get(grade, 0.0)
        ttl = (
            SUBJECT_CATALOG_TTL_SECONDS
            if _subject_catalog_live.get(grade, False)
            else SUBJECT_CATALOG_RETRY_SECONDS
        )
        if grade in _subject_catalog_cache and now - cached_at < ttl:
            return list(_subject_catalog_cache[grade])

        try:
            live_subjects = fetch_schoolyear_subjects(grade)
            if not live_subjects:
                raise RuntimeError("current school-year timetable contains no subjects")
            subjects = live_subjects
            live = True
        except Exception as exc:
            app.logger.warning(
                "current subject catalog fetch failed for %s: %s; using cached fallback",
                grade,
                exc,
            )
            subjects = (
                _subject_catalog_cache[grade]
                if _subject_catalog_live.get(grade) and _subject_catalog_cache.get(grade)
                else _load_raw_subjects_for_grade(grade)
            )
            live = False

        subjects = sorted(
            {str(subject or "").strip() for subject in subjects if str(subject or "").strip()},
            key=str.casefold,
        )
        if live:
            try:
                _prune_course_mapping_for_grade(grade, subjects)
            except Exception as exc:
                app.logger.warning("stale course mapping cleanup failed for %s: %s", grade, exc)
        _subject_catalog_cache[grade] = subjects
        _subject_catalog_cached_at[grade] = now
        _subject_catalog_live[grade] = live
        return list(subjects)

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
    {"EF": { raw: label, ... }, "Q1": {...}, "Q2": {...}}
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
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    os.replace(tmp_path, path)

# course mapping helpers (per-grade files, merged views)
def _course_map_path_for_grade(grade: str) -> str | None:
    return COURSE_MAP_PATHS.get((grade or "").upper())

def _course_map_normalized_for_grade(grade: str) -> dict[str, str]:
    path = _course_map_path_for_grade(grade)
    if path:
        return _parse_mapping(path)
    return {}

def _course_map_write_for_grade(grade: str, mapping: dict[str, str]) -> None:
    """Persist a mapping only for the given grade."""
    path = _course_map_path_for_grade(grade)
    if not path:
        return
    _write_mapping_txt(path, mapping)


def _active_course_map_for_grade(
    grade: str,
    active_subjects: list[str] | tuple[str, ...] | set[str],
) -> dict[str, str]:
    """Return rename mappings only for subjects in the active school year."""
    active_keys = {norm_key(subject) for subject in active_subjects}
    active_keys.discard("")
    mapping = _course_map_normalized_for_grade(grade)
    return {key: value for key, value in mapping.items() if key in active_keys}


def _prune_course_mapping_for_grade(grade: str, active_subjects: list[str]) -> None:
    """Remove stale mappings only after a successful live catalog fetch."""
    current = _course_map_normalized_for_grade(grade)
    active = _active_course_map_for_grade(grade, active_subjects)
    if active != current:
        _course_map_write_for_grade(grade, active)

# ---------- Seen keys (store raw & normalised) ----------
SEEN_SUB_RAW_PATH = os.path.join(DATA_DIR, "seen_subjects_raw.json")
SEEN_SUB_RAW_PATHS = {
    grade: os.path.join(DATA_DIR, f"seen_subjects_raw_{grade.lower()}.json")
    for grade in SUPPORTED_GRADES
}
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
SEEN_SUBJECTS_RAW_BY_GRADE = {
    grade: _load_seen_raw(path)
    for grade, path in SEEN_SUB_RAW_PATHS.items()
}
SEEN_ROOMS_RAW    = _load_seen_raw(SEEN_ROOM_RAW_PATH)
_last_seen_flush  = 0.0

def record_seen_raw(lessons: list[dict]):
    """Remember subjects per grade and rooms globally, exactly as Untis sends them."""
    global _last_seen_flush
    changed_grades: set[str] = set()
    subjects_changed = False
    rooms_changed = False
    for L in lessons:
        sraw = (L.get("subject_original") or L.get("subject") or "").strip()
        rraw = (L.get("room") or "").strip()
        grade = _normalise_grade(L.get("grade"))
        if grade and sraw and sraw not in SEEN_SUBJECTS_RAW_BY_GRADE[grade]:
            SEEN_SUBJECTS_RAW_BY_GRADE[grade].append(sraw)
            changed_grades.add(grade)
        if sraw and sraw not in SEEN_SUBJECTS_RAW:
            SEEN_SUBJECTS_RAW.append(sraw)
            subjects_changed = True
        if rraw and rraw not in SEEN_ROOMS_RAW:
            SEEN_ROOMS_RAW.append(rraw)
            rooms_changed = True
    if changed_grades or subjects_changed or rooms_changed:
        for grade in changed_grades:
            _save_seen_raw(
                SEEN_SUB_RAW_PATHS[grade],
                SEEN_SUBJECTS_RAW_BY_GRADE[grade],
            )
    if subjects_changed:
        _save_seen_raw(SEEN_SUB_RAW_PATH, SEEN_SUBJECTS_RAW)
    if rooms_changed:
        _save_seen_raw(SEEN_ROOM_RAW_PATH, SEEN_ROOMS_RAW)
    if changed_grades or subjects_changed or rooms_changed:
        _last_seen_flush = time.time()

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
        if nk:
            grouped.setdefault(nk, set()).add(raw)
    return {k: sorted(v) for k, v in grouped.items()}

def _subject_groups_for_grade(grade: str) -> dict[str, list[str]]:
    """Return every renameable subject key for one grade only."""
    grade = _normalise_grade(grade)
    if not grade:
        return {}
    grouped = _group_variants(_current_subjects_for_grade(grade))
    return dict(sorted(grouped.items()))

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
    if request.endpoint == "banner_image":
        return resp
    return _no_store(resp)


@app.errorhandler(sqlite3.Error)
def handle_sqlite_error(exc):
    conn = g.get("db")
    if conn is not None:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
    app.logger.exception("SQLite operation failed")
    if request.path.startswith("/api/"):
        locked = "locked" in str(exc).lower() or "busy" in str(exc).lower()
        message = (
            "Datenbank beschäftigt. Bitte in wenigen Sekunden erneut versuchen."
            if locked
            else "Datenbankfehler. Bitte erneut versuchen."
        )
        return _no_store(jsonify({"ok": False, "error": message})), 503 if locked else 500
    return "Database error", 500

@app.route("/")
def index():
    return render_template(
        "index.html",
        image_banner=_image_banner_payload(),
    )

@app.route("/sw.js")
def service_worker():
    # Serve the PWA service worker from the root path
    return send_from_directory(app.static_folder, "sw.js", max_age=0)

@app.route("/api/mappings")
def api_mappings():
    active_subjects = {
        grade: _current_subjects_for_grade(grade)
        for grade in SUPPORTED_GRADES
    }
    course_maps = {
        grade: _active_course_map_for_grade(grade, active_subjects[grade])
        for grade in SUPPORTED_GRADES
    }
    room_map   = _parse_mapping(ROOM_MAP_PATH)
    return _no_store(jsonify({
        "ok": True,
        "schoolyear": _current_schoolyear_label(),
        "coursesByGrade": course_maps,
        "rooms": room_map,
    }))

@app.route("/api/courses")
def api_courses():
    """Return course options from the separate EF, Q1 and Q2 mappings.

    Key: grade-prefixed normalised LHS (GRADE:norm_key). Label: RHS if present, else original LHS.
    """
    def _options_for_grade(grade: str) -> dict[str, str]:
        """Build per-grade options so EF/Q1/Q2 stay separated."""
        opts: dict[str, str] = {}
        raw_subjects = _current_subjects_for_grade(grade)
        mapping = _active_course_map_for_grade(grade, raw_subjects)
        for raw_subject in raw_subjects:
            left = str(raw_subject or "").strip()
            key = norm_key(left)
            if key:
                opts.setdefault(key, (mapping.get(key) or "").strip() or left)
        return opts

    grades = available_grades() or ["EF"]
    items: list[dict] = []
    for grade in grades:
        grade_opts = _options_for_grade(grade)
        for key in sorted(grade_opts.keys(), key=lambda k: (grade_opts[k].lower(), grade_opts[k])):
            items.append({"key": f"{grade}:{key}", "label": grade_opts[key], "grade": grade})
    return _no_store(jsonify({
        "ok": True,
        "schoolyear": _current_schoolyear_label(),
        "courses": items,
    }))

@app.route("/api/health")
def api_health():
    return no_store(make_response(jsonify({"ok": True}), 200))

@app.route("/api/update-banner")
def api_update_banner():
    payload = _update_banner_payload()
    return _no_store(jsonify({"ok": True, "updateBanner": payload}))


@app.route("/api/banner-image")
def banner_image():
    data_url = str(_get_setting("imageBannerData", "") or "").strip()
    if not data_url:
        return jsonify({"ok": False, "error": "not_found"}), 404
    try:
        image_bytes = _decode_banner_image(data_url)
    except ValueError:
        return jsonify({"ok": False, "error": "invalid_banner_image"}), 404
    response = make_response(image_bytes)
    response.headers["Content-Type"] = "image/jpeg"
    response.headers["Content-Length"] = str(len(image_bytes))
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response

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
            "INSERT INTO users (username, password_hash, profile_json) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), json.dumps(_empty_profile()))
        )
        new_id = cur.lastrowid
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "error": "username_exists"}), 409
    session.permanent = True
    session["user_id"] = new_id
    _maybe_send_backup("user_register")
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
    session.clear()
    return _no_store(jsonify({"ok": True, "authenticated": False}))


def _push_configured() -> bool:
    valid_subject = VAPID_SUBJECT.startswith("mailto:") or VAPID_SUBJECT.startswith("https://")
    return bool(webpush and VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY and valid_subject)


def _normalise_push_subscription(payload) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("push_subscription_invalid")
    subscription = payload.get("subscription") if isinstance(payload.get("subscription"), dict) else payload
    endpoint = str(subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    parsed_endpoint = urlparse(endpoint)
    if (
        parsed_endpoint.scheme != "https"
        or not parsed_endpoint.netloc
        or len(endpoint) > 4096
        or not p256dh
        or len(p256dh) > 1024
        or not auth
        or len(auth) > 1024
    ):
        raise ValueError("push_subscription_invalid")
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}


def _send_push_rows(rows, payload: dict, ttl: int = 300) -> dict:
    notification = json.dumps(payload, ensure_ascii=False)
    sent = 0
    failed = 0
    stale_ids: list[int] = []
    for row in rows:
        try:
            webpush(
                subscription_info={
                    "endpoint": row["endpoint"],
                    "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
                },
                data=notification,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_SUBJECT},
                ttl=ttl,
                timeout=10,
            )
            sent += 1
        except Exception as exc:
            failed += 1
            response = getattr(exc, "response", None)
            if getattr(response, "status_code", None) in (404, 410):
                stale_ids.append(int(row["id"]))
            app.logger.warning("push failed for subscription %s: %s", row["id"], exc)

    if stale_ids:
        placeholders = ",".join("?" for _ in stale_ids)
        get_db().execute(
            f"DELETE FROM push_subscriptions WHERE id IN ({placeholders})",
            stale_ids,
        )
        get_db().commit()
        _maybe_send_backup("push_stale_cleanup")
    return {
        "sent": sent,
        "failed": failed,
        "removed": len(stale_ids),
        "attempted": len(rows),
    }


def _send_push_to_user(user_id: int, payload: dict, ttl: int = 300) -> dict:
    rows = get_db().execute(
        "SELECT id, user_id, endpoint, p256dh, auth "
        "FROM push_subscriptions WHERE user_id = ? ORDER BY id",
        (user_id,),
    ).fetchall()
    return _send_push_rows(rows, payload, ttl=ttl)


@app.route("/api/push/subscription", methods=["GET", "POST", "DELETE"])
def api_push_subscription():
    user_id = _current_user_id()
    if not _load_user(user_id):
        session.pop("user_id", None)
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    db = get_db()
    if request.method == "GET":
        subscriptions = db.execute(
            "SELECT endpoint FROM push_subscriptions WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()
        endpoint_hashes = [
            hashlib.sha256(str(row["endpoint"]).encode("utf-8")).hexdigest()
            for row in subscriptions
        ]
        return _no_store(jsonify({
            "ok": True,
            "configured": _push_configured(),
            "publicKey": VAPID_PUBLIC_KEY if _push_configured() else "",
            "subscribed": bool(subscriptions),
            "subscriptionCount": len(subscriptions),
            "endpointHashes": endpoint_hashes,
        }))

    data = request.get_json(silent=True) or {}
    try:
        subscription = _normalise_push_subscription(data)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    endpoint = subscription["endpoint"]
    if request.method == "DELETE":
        cursor = db.execute(
            "DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?",
            (user_id, endpoint),
        )
        db.commit()
        if cursor.rowcount:
            _maybe_send_backup("push_subscription_delete")
        return _no_store(jsonify({"ok": True, "deleted": cursor.rowcount > 0}))

    if not _push_configured():
        return jsonify({"ok": False, "error": "push_not_configured"}), 503
    db.execute(
        """
        INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, user_agent)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET
            user_id = excluded.user_id,
            p256dh = excluded.p256dh,
            auth = excluded.auth,
            user_agent = excluded.user_agent,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            user_id,
            endpoint,
            subscription["keys"]["p256dh"],
            subscription["keys"]["auth"],
            str(request.headers.get("User-Agent") or "")[:500],
        ),
    )
    db.commit()
    _maybe_send_backup("push_subscription_update")
    return _no_store(jsonify({"ok": True, "subscribed": True}))


@app.route("/api/notifications/preferences", methods=["GET", "PUT"])
def api_notification_preferences():
    user_id = _current_user_id()
    row = _load_user(user_id)
    if not row:
        session.pop("user_id", None)
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    profile = _load_profile_for_user(row)
    if request.method == "GET":
        return _no_store(jsonify({
            "ok": True,
            "preferences": profile["notificationPreferences"],
        }))

    data = request.get_json(silent=True) or {}
    incoming = data.get("preferences") if isinstance(data.get("preferences"), dict) else data
    preferences = _normalise_notification_preferences(incoming)
    profile["notificationPreferences"] = preferences
    _save_profile(user_id, profile)
    _maybe_send_backup("notification_preferences_update")
    return _no_store(jsonify({"ok": True, "preferences": preferences}))

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
    _maybe_send_backup("profile_update")
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
    session.clear()
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
            "SELECT id, username, password_hash, profile_json, created_at FROM users ORDER BY id"
        )
        for row in cur.fetchall():
            prof = _load_profile_for_user(row)
            users.append({
                "id": row["id"],
                "username": row["username"],
                "password_hash": row["password_hash"],
                "profile": prof,
                "created_at": row["created_at"],
            })
    except Exception:
        users = []

    push_subscriptions = []
    try:
        cur = db.execute(
            "SELECT id, user_id, endpoint, p256dh, auth, user_agent, created_at, updated_at "
            "FROM push_subscriptions ORDER BY id"
        )
        for row in cur.fetchall():
            push_subscriptions.append({
                "id": row["id"],
                "user_id": row["user_id"],
                "subscription": {
                    "endpoint": row["endpoint"],
                    "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
                },
                "user_agent": row["user_agent"] or "",
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
    except Exception:
        push_subscriptions = []

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
            "push_subscriptions": push_subscriptions,
            "vacations": vacations,
            "exams_manual": exams_manual,
            "settings": settings_map,
        },
        "mappings": {
            **{
                f"courses_{grade.lower()}": _course_map_normalized_for_grade(grade)
                for grade in SUPPORTED_GRADES
            },
            "rooms": _parse_mapping(ROOM_MAP_PATH),
        },
        "seen": {
            "subjects_raw": sorted(set(SEEN_SUBJECTS_RAW)),
            "subjects_raw_by_grade": {
                grade: sorted(set(SEEN_SUBJECTS_RAW_BY_GRADE.get(grade, [])))
                for grade in SUPPORTED_GRADES
            },
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
            users_norm.append((user_id, username, entry.get("password_hash") or "", profile_json, created_at))

    push_subscriptions_norm = []
    push_subscriptions_in = db_section.get("push_subscriptions") or []
    if isinstance(push_subscriptions_in, list):
        for entry in push_subscriptions_in:
            if not isinstance(entry, dict):
                continue
            try:
                subscription_id = int(entry.get("id"))
                user_id = int(entry.get("user_id"))
                subscription = _normalise_push_subscription(entry.get("subscription"))
            except (TypeError, ValueError):
                continue
            push_subscriptions_norm.append((
                subscription_id,
                user_id,
                subscription["endpoint"],
                subscription["keys"]["p256dh"],
                subscription["keys"]["auth"],
                str(entry.get("user_agent") or "")[:500],
                entry.get("created_at") or datetime.utcnow().isoformat(),
                entry.get("updated_at") or datetime.utcnow().isoformat(),
            ))

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
    courses_by_grade: dict[str, dict[str, str]] = {}
    for grade in SUPPORTED_GRADES:
        grade_key = f"courses_{grade.lower()}"
        grade_payload = mappings_section.get(grade_key)
        grade_map: dict[str, str] = {}
        if isinstance(grade_payload, dict):
            for k, v in grade_payload.items():
                nk = norm_key(k)
                if nk:
                    grade_map[nk] = (v or "").strip()
            courses_by_grade[grade] = grade_map
    if courses_map and not courses_by_grade:
        # Legacy backups may contain one merged subject map. Restore their
        # database content, but never copy that ambiguous map into any grade.
        app.logger.warning(
            "legacy backup contains only an unscoped subject map; "
            "restoring database content and leaving grade mappings unchanged"
        )

    rooms_map = {}
    rooms = mappings_section.get("rooms")
    if isinstance(rooms, dict):
        for k, v in rooms.items():
            nk = norm_key(k)
            rooms_map[nk] = (v or "").strip()

    subs_raw = seen_section.get("subjects_raw") if isinstance(seen_section, dict) else []
    subs_raw_by_grade = seen_section.get("subjects_raw_by_grade") if isinstance(seen_section, dict) else None
    rooms_raw = seen_section.get("rooms_raw") if isinstance(seen_section, dict) else []
    subs_norm = sorted({str(s or "").strip() for s in subs_raw if str(s or "").strip()}) if isinstance(subs_raw, list) else []
    subs_by_grade_norm: dict[str, list[str]] | None = None
    if isinstance(subs_raw_by_grade, dict):
        subs_by_grade_norm = {}
        for grade in SUPPORTED_GRADES:
            grade_values = subs_raw_by_grade.get(grade, [])
            subs_by_grade_norm[grade] = (
                sorted({
                    str(s or "").strip()
                    for s in grade_values
                    if str(s or "").strip()
                })
                if isinstance(grade_values, list)
                else []
            )
    rooms_norm = sorted({str(r or "").strip() for r in rooms_raw if str(r or "").strip()}) if isinstance(rooms_raw, list) else []

    db = get_db()
    try:
        db.execute("BEGIN")
        db.execute("DELETE FROM notification_deliveries")
        db.execute("DELETE FROM notification_snapshots")
        db.execute("DELETE FROM notification_runtime")
        db.execute("DELETE FROM push_subscriptions")
        db.execute("DELETE FROM users")
        db.execute("DELETE FROM vacations")
        db.execute("DELETE FROM settings")
        db.execute("DELETE FROM exams_manual")

        for row in users_norm:
            db.execute(
                "INSERT INTO users (id, username, password_hash, profile_json, created_at) VALUES (?, ?, ?, ?, ?)",
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

        for row in push_subscriptions_norm:
            db.execute(
                "INSERT INTO push_subscriptions "
                "(id, user_id, endpoint, p256dh, auth, user_agent, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                row,
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

    # Never restore an unscoped subject map into multiple grades.
    for grade, grade_map in courses_by_grade.items():
        _course_map_write_for_grade(grade, grade_map)
    _write_mapping_txt(ROOM_MAP_PATH, rooms_map)

    global SEEN_SUBJECTS_RAW, SEEN_SUBJECTS_RAW_BY_GRADE, SEEN_ROOMS_RAW, _last_seen_flush
    SEEN_SUBJECTS_RAW = subs_norm
    SEEN_ROOMS_RAW = rooms_norm
    _save_seen_raw(SEEN_SUB_RAW_PATH, SEEN_SUBJECTS_RAW)
    if subs_by_grade_norm is not None:
        SEEN_SUBJECTS_RAW_BY_GRADE = subs_by_grade_norm
        for grade, values in SEEN_SUBJECTS_RAW_BY_GRADE.items():
            _save_seen_raw(SEEN_SUB_RAW_PATHS[grade], values)
    _save_seen_raw(SEEN_ROOM_RAW_PATH, SEEN_ROOMS_RAW)
    _last_seen_flush = time.time()


def _backup_user_count(payload: dict) -> int | None:
    if not isinstance(payload, dict):
        return None
    database = payload.get("database")
    if not isinstance(database, dict):
        return None
    users = database.get("users")
    return len(users) if isinstance(users, list) else None


def _database_user_count() -> int:
    return int(get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0])


_BACKUP_SHRINK_TRIGGERS = {"admin_user_delete", "admin_restore"}


def _backup_request_options() -> tuple[dict, dict]:
    params = {}
    headers = {"User-Agent": "untis-pwa/backup"}
    if BACKUP_WEBHOOK_TOKEN:
        params["token"] = BACKUP_WEBHOOK_TOKEN
        headers["X-Backup-Token"] = BACKUP_WEBHOOK_TOKEN
        headers["Authorization"] = f"Bearer {BACKUP_WEBHOOK_TOKEN}"
    return params, headers


def _fetch_remote_backup() -> dict:
    if not AUTO_RESTORE_URL:
        raise ValueError("backup_restore_url_missing")
    params, headers = _backup_request_options()
    response = requests.get(
        AUTO_RESTORE_URL,
        params=params,
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()
    if not response.content:
        raise ValueError("backup_response_empty")
    payload = response.json()
    if _backup_user_count(payload) is None:
        raise ValueError("backup_users_invalid")
    return payload


def _maybe_send_backup(trigger: str = "manual", payload: dict | None = None) -> bool:
    """
    Push a fresh backup to a webhook if configured (Render free tier loses disk).
    The call is best-effort and time-limited so API responses are not blocked.
    """
    if not BACKUP_WEBHOOK_URL:
        return False
    try:
        data = payload or _build_backup_payload()
        local_user_count = _backup_user_count(data)
        if local_user_count is None:
            app.logger.warning("backup skipped (%s): invalid local payload", trigger)
            return False

        allow_shrink = trigger in _BACKUP_SHRINK_TRIGGERS
        if not allow_shrink and local_user_count == 0:
            app.logger.warning("backup skipped (%s): local user database is empty", trigger)
            return False

        if not allow_shrink and AUTO_RESTORE_URL:
            try:
                remote_user_count = _backup_user_count(_fetch_remote_backup())
            except Exception as exc:
                app.logger.warning(
                    "backup skipped (%s): remote safety check failed: %s",
                    trigger,
                    exc,
                )
                return False
            if remote_user_count is not None and remote_user_count > local_user_count:
                app.logger.error(
                    "backup skipped (%s): refusing to replace %s remote users "
                    "with %s local users",
                    trigger,
                    remote_user_count,
                    local_user_count,
                )
                return False

        params, headers = _backup_request_options()
        response = requests.post(
            BACKUP_WEBHOOK_URL,
            json=data,
            params=params,
            timeout=8,
            headers=headers,
        )
        response.raise_for_status()
        if response.content:
            try:
                acknowledgement = response.json()
            except (TypeError, ValueError):
                acknowledgement = None
            if isinstance(acknowledgement, dict) and acknowledgement.get("ok") is False:
                raise RuntimeError(
                    f"backup service rejected request: {acknowledgement.get('error', 'unknown')}"
                )
        return True
    except Exception as exc:
        app.logger.warning("backup webhook failed (%s): %s", trigger, exc)
        return False


def _maybe_auto_restore() -> bool:
    """If AUTO_RESTORE_URL is set and DB is empty, pull a backup JSON and restore it."""
    if not AUTO_RESTORE_URL:
        return _database_user_count() > 0
    try:
        if _database_user_count() > 0 and not AUTO_RESTORE_FORCE:
            return True
    except Exception as exc:
        app.logger.warning("auto-restore precheck failed: %s", exc)
        return False
    try:
        payload = _fetch_remote_backup()
        expected_users = _backup_user_count(payload)
        _apply_backup_payload(payload)
        restored_users = _database_user_count()
        if restored_users != expected_users:
            raise RuntimeError(
                f"restored {restored_users} users, expected {expected_users}"
            )
        app.logger.info("auto-restore from AUTO_RESTORE_URL succeeded")
        return True
    except Exception as exc:
        app.logger.warning("auto-restore failed: %s", exc)
        return False


def _notification_lesson(raw: dict, grade: str) -> dict:
    return {
        "id": str(raw.get("id") or "").strip(),
        "grade": _normalise_grade(grade),
        "date": str(raw.get("date") or "").strip(),
        "start": str(raw.get("start") or "").strip(),
        "end": str(raw.get("end") or "").strip(),
        "subject": str(raw.get("subject") or "").strip(),
        "subject_original": str(raw.get("subject_original") or "").strip(),
        "teacher": str(raw.get("teacher") or "").strip(),
        "room": str(raw.get("room") or "").strip(),
        "status": str(raw.get("status") or "normal").strip().lower(),
        "note": str(raw.get("note") or "").strip(),
        "special": bool(raw.get("special")),
    }


def _notification_lesson_identity(lesson: dict) -> str:
    raw_id = str(lesson.get("id") or "").strip()
    match = re.fullmatch(r"(.+)-\d{8}-\d{1,4}", raw_id)
    if match:
        return match.group(1)
    if raw_id:
        return raw_id
    fallback = "|".join(
        str(lesson.get(key) or "")
        for key in ("date", "start", "end", "subject_original", "subject")
    )
    return hashlib.sha256(fallback.encode("utf-8")).hexdigest()[:24]


def _notification_event_key(grade: str, week_start: date, identity: str, lesson: dict) -> str:
    fingerprint = json.dumps(lesson, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:20]
    return f"timetable:{grade}:{week_start.isoformat()}:{identity}:{digest}"


def _compare_and_store_notification_snapshot(
    grade: str,
    week_start: date,
    raw_lessons: list[dict],
    today: date,
) -> list[dict]:
    grade = _normalise_grade(grade)
    if not grade:
        return []
    lessons = sorted(
        (_notification_lesson(item, grade) for item in raw_lessons if isinstance(item, dict)),
        key=lambda item: (
            item.get("date", ""),
            item.get("start", ""),
            _notification_lesson_identity(item),
        ),
    )
    db = get_db()
    row = db.execute(
        "SELECT lessons_json FROM notification_snapshots WHERE grade = ? AND week_start = ?",
        (grade, week_start.isoformat()),
    ).fetchone()
    previous = None
    if row:
        try:
            loaded = json.loads(row["lessons_json"])
            previous = loaded if isinstance(loaded, list) else []
        except (TypeError, json.JSONDecodeError):
            previous = []

    # A transient Untis response can be empty. Keep the last non-empty baseline
    # so recovery does not look like an entire week of newly added lessons.
    if previous and not lessons:
        return []

    db.execute(
        """
        INSERT INTO notification_snapshots (grade, week_start, lessons_json, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(grade, week_start) DO UPDATE SET
            lessons_json = excluded.lessons_json,
            updated_at = excluded.updated_at
        """,
        (grade, week_start.isoformat(), json.dumps(lessons, ensure_ascii=False), int(time.time())),
    )
    db.commit()
    if previous is None:
        return []

    old_by_id = {_notification_lesson_identity(item): item for item in previous}
    events: list[dict] = []
    for current in lessons:
        try:
            if _parse_iso_date(current["date"]) < today:
                continue
        except (KeyError, ValueError):
            continue
        identity = _notification_lesson_identity(current)
        old = old_by_id.get(identity)
        if old is None:
            if current.get("status") != "entfaellt":
                changes = ["addition"]
            else:
                continue
        else:
            changes = []
            if old.get("status") != "entfaellt" and current.get("status") == "entfaellt":
                changes.append("cancellation")
            if (old.get("date"), old.get("start"), old.get("end")) != (
                current.get("date"), current.get("start"), current.get("end")
            ):
                changes.append("time")
            if old.get("room") != current.get("room"):
                changes.append("room")
            if (
                old.get("status") != current.get("status")
                and "cancellation" not in changes
            ) or old.get("note") != current.get("note"):
                changes.append("other")
        if not changes:
            continue
        events.append({
            "key": _notification_event_key(grade, week_start, identity, current),
            "grade": grade,
            "weekStart": week_start.isoformat(),
            "changes": changes,
            "lesson": current,
            "previous": old,
        })
    return events


def _fetch_notification_timetable_events(now: datetime) -> list[dict]:
    events: list[dict] = []
    current_week = _monday_of(now.date())
    grades = [grade for grade in available_grades() if grade in SUPPORTED_GRADES]
    for grade in grades:
        for week_start in (current_week, current_week + timedelta(days=7)):
            try:
                lessons = fetch_week(week_start, grade) or []
                events.extend(
                    _compare_and_store_notification_snapshot(
                        grade,
                        week_start,
                        lessons,
                        now.date(),
                    )
                )
            except Exception as exc:
                get_db().rollback()
                app.logger.warning(
                    "notification timetable fetch failed for %s/%s: %s",
                    grade,
                    week_start,
                    exc,
                )
    return events


def _profile_selected_courses(profile: dict, grade: str) -> set[str]:
    selected: set[str] = set()
    for value in profile.get("courses") or []:
        course_grade, body = _course_grade_and_body(value)
        if course_grade == grade:
            key = norm_key(body)
            if key:
                selected.add(f"{grade}:{key}")
    return selected


def _subject_candidates(grade: str, subject: str, course_map: dict[str, str]) -> set[str]:
    key = norm_key(subject)
    candidates = {f"{grade}:{key}"} if key else set()
    mapped = str(course_map.get(key) or "").strip() if key else ""
    mapped_key = norm_key(mapped)
    if mapped_key:
        candidates.add(f"{grade}:{mapped_key}")
    return candidates


def _lesson_matches_selected_courses(
    event: dict,
    selected: set[str],
    course_map: dict[str, str],
) -> bool:
    if not selected:
        return False
    grade = event["grade"]
    for lesson in (event.get("lesson"), event.get("previous")):
        if not isinstance(lesson, dict):
            continue
        for subject in (lesson.get("subject_original"), lesson.get("subject")):
            if selected.intersection(_subject_candidates(grade, subject, course_map)):
                return True
    return False


def _notification_subject(lesson: dict, course_map: dict[str, str]) -> str:
    raw = str(lesson.get("subject_original") or lesson.get("subject") or "Stunde").strip()
    return str(course_map.get(norm_key(raw)) or lesson.get("subject") or raw or "Stunde").strip()


def _format_notification_lesson_time(lesson: dict) -> str:
    weekdays = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
    try:
        lesson_date = _parse_iso_date(str(lesson.get("date") or ""))
        day = f"{weekdays[lesson_date.weekday()]}, {lesson_date.strftime('%d.%m.')}"
    except ValueError:
        day = str(lesson.get("date") or "")
    start = str(lesson.get("start") or "").strip()
    return f"{day}, {start}" if start else day


def _allowed_timetable_changes(event: dict, preferences: dict) -> list[str]:
    preference_by_change = {
        "cancellation": "cancellations",
        "addition": "additions",
        "room": "roomChanges",
        "time": "timeChanges",
        "other": "otherChanges",
    }
    return [
        change
        for change in event.get("changes") or []
        if preferences.get(preference_by_change.get(change, "otherChanges"), True)
    ]


def _describe_timetable_event(event: dict, changes: list[str], course_map: dict[str, str]) -> str:
    lesson = event["lesson"]
    previous = event.get("previous") or {}
    subject = _notification_subject(lesson, course_map)
    when = _format_notification_lesson_time(lesson)
    if changes == ["addition"]:
        return f"{subject} am {when} wurde hinzugefügt."

    details: list[str] = []
    if "cancellation" in changes:
        details.append("fällt aus")
    if "time" in changes:
        old_time = _format_notification_lesson_time(previous)
        details.append(f"neue Zeit {when} (vorher {old_time})")
    if "room" in changes:
        old_room = str(previous.get("room") or "ohne Raum")
        new_room = str(lesson.get("room") or "ohne Raum")
        details.append(f"Raum {old_room} → {new_room}")
    if "other" in changes:
        status = str(lesson.get("status") or "geändert")
        note = str(lesson.get("note") or "").strip()
        details.append(note or status.capitalize())
    return f"{subject} am {when}: " + "; ".join(details) + "."


def _delivery_exists(user_id: int, event_key: str) -> bool:
    return get_db().execute(
        "SELECT 1 FROM notification_deliveries WHERE user_id = ? AND event_key = ?",
        (user_id, event_key),
    ).fetchone() is not None


def _record_deliveries(user_id: int, event_keys: list[str], created_at: int) -> None:
    db = get_db()
    db.executemany(
        "INSERT OR IGNORE INTO notification_deliveries (user_id, event_key, created_at) VALUES (?, ?, ?)",
        ((user_id, key, created_at) for key in event_keys),
    )
    db.commit()


def _send_timetable_event_notifications(events: list[dict], now: datetime) -> int:
    if not events:
        return 0
    users = get_db().execute(
        """
        SELECT DISTINCT u.id, u.username, u.profile_json
        FROM users u
        JOIN push_subscriptions p ON p.user_id = u.id
        ORDER BY u.id
        """
    ).fetchall()
    course_maps = {
        grade: _course_map_normalized_for_grade(grade)
        for grade in SUPPORTED_GRADES
    }
    sent_users = 0
    for row in users:
        profile = _load_profile_for_user(row)
        grade = _normalise_grade(profile.get("grade"))
        preferences = profile["notificationPreferences"]
        if not grade or not preferences["enabled"] or not preferences["timetableChanges"]:
            continue
        selected = _profile_selected_courses(profile, grade)
        matching: list[tuple[dict, list[str]]] = []
        for event in events:
            if event["grade"] != grade or _delivery_exists(int(row["id"]), event["key"]):
                continue
            if not _lesson_matches_selected_courses(event, selected, course_maps[grade]):
                continue
            allowed = _allowed_timetable_changes(event, preferences)
            if allowed:
                matching.append((event, allowed))
        if not matching:
            continue

        descriptions = [
            _describe_timetable_event(event, changes, course_maps[grade])
            for event, changes in matching
        ]
        body = descriptions[0]
        if len(descriptions) > 1:
            body = f"{len(descriptions)} Änderungen. {descriptions[0]}"
        aggregate = "|".join(event["key"] for event, _ in matching)
        result = _send_push_to_user(int(row["id"]), {
            "title": f"Stundenplanänderung {grade}",
            "body": body[:240],
            "url": "/",
            "tag": "timetable-" + hashlib.sha256(aggregate.encode("utf-8")).hexdigest()[:20],
        }, ttl=3600)
        if result["sent"] > 0:
            _record_deliveries(int(row["id"]), [event["key"] for event, _ in matching], int(now.timestamp()))
            sent_users += 1
    return sent_users


def _normalise_remote_exam_for_notification(rec: dict, grade: str, subjects: dict) -> dict | None:
    if not isinstance(rec, dict):
        return None
    subject_id = rec.get("subjectId") or rec.get("subject")
    subject = rec.get("subjectName") or rec.get("subject") or subjects.get(subject_id, "")
    if not isinstance(subject, str):
        subject = subjects.get(subject_id, "")
    exam_date = _date_int_to_iso(rec.get("examDate") or rec.get("date"))
    if not subject or not exam_date:
        return None
    return {
        "id": str(rec.get("id") or rec.get("examId") or ""),
        "grade": grade,
        "date": exam_date,
        "subject": str(subject).strip(),
        "name": str(rec.get("name") or rec.get("title") or subject).strip(),
        "start": _normalise_hm(rec.get("start") or rec.get("startTime")),
        "source": "untis",
    }


def _remote_exam_notification_sources(grade: str, today: date) -> list[dict]:
    cache_key = f"exam_cache:{grade}"
    row = get_db().execute(
        "SELECT value FROM notification_runtime WHERE key = ?",
        (cache_key,),
    ).fetchone()
    cached: dict = {}
    if row:
        try:
            cached = json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            cached = {}
    if int(cached.get("expires", 0) or 0) > int(time.time()) and isinstance(cached.get("exams"), list):
        return cached["exams"]

    try:
        raw_exams = fetch_exams(today, today + timedelta(days=14), 0, grade) or []
        subjects = fetch_subject_map(grade)
        exams = [
            exam
            for exam in (
                _normalise_remote_exam_for_notification(rec, grade, subjects)
                for rec in raw_exams
            )
            if exam
        ]
        value = json.dumps({"expires": int(time.time()) + 6 * 3600, "exams": exams}, ensure_ascii=False)
        get_db().execute(
            "INSERT INTO notification_runtime (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (cache_key, value),
        )
        get_db().commit()
        return exams
    except Exception as exc:
        get_db().rollback()
        app.logger.warning("notification exam fetch failed for %s: %s", grade, exc)
        exams = cached.get("exams", []) if isinstance(cached.get("exams"), list) else []
        get_db().execute(
            "INSERT INTO notification_runtime (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (cache_key, json.dumps({"expires": int(time.time()) + 30 * 60, "exams": exams}, ensure_ascii=False)),
        )
        get_db().commit()
        return exams


def _send_exam_reminders(now: datetime) -> int:
    users = get_db().execute(
        """
        SELECT DISTINCT u.id, u.username, u.profile_json
        FROM users u
        JOIN push_subscriptions p ON p.user_id = u.id
        ORDER BY u.id
        """
    ).fetchall()
    grades = {
        _normalise_grade(_load_profile_for_user(row).get("grade"))
        for row in users
    }
    grades.discard("")
    remote_by_grade = {
        grade: _remote_exam_notification_sources(grade, now.date())
        for grade in grades
    }
    manual_exams = _load_manual_exams(now.date(), now.date() + timedelta(days=14))
    course_maps = {
        grade: _course_map_normalized_for_grade(grade)
        for grade in grades
    }
    sent = 0
    for row in users:
        user_id = int(row["id"])
        profile = _load_profile_for_user(row)
        grade = _normalise_grade(profile.get("grade"))
        preferences = profile["notificationPreferences"]
        if not grade or not preferences["enabled"] or not preferences["examReminders"]:
            continue
        selected = _profile_selected_courses(profile, grade)
        reminder_days = preferences["examReminderDays"]
        target_date = now.date() + timedelta(days=reminder_days)
        candidates: list[dict] = []
        for exam in profile.get("klausuren") or []:
            candidates.append({**exam, "grade": grade, "source": "personal"})
        candidates.extend(
            exam for exam in manual_exams
            if _normalise_grade(exam.get("grade")) == grade
        )
        candidates.extend(remote_by_grade.get(grade, []))

        unique: dict[str, dict] = {}
        for exam in candidates:
            if str(exam.get("date") or "") != target_date.isoformat():
                continue
            subject = str(exam.get("subject") or "").strip()
            if exam.get("source") != "personal" and not selected.intersection(
                _subject_candidates(grade, subject, course_maps[grade])
            ):
                continue
            identity = "|".join((grade, target_date.isoformat(), norm_key(subject), norm_key(exam.get("name"))))
            unique[identity] = exam

        for identity, exam in unique.items():
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
            event_key = f"exam:{digest}:days-{reminder_days}"
            if _delivery_exists(user_id, event_key):
                continue
            subject = str(exam.get("subject") or "Klausur").strip()
            name = str(exam.get("name") or subject).strip()
            when = "heute" if reminder_days == 0 else (
                "morgen" if reminder_days == 1 else f"in {reminder_days} Tagen"
            )
            result = _send_push_to_user(user_id, {
                "title": f"Klausur {when}",
                "body": f"{name} ({subject}) am {target_date.strftime('%d.%m.%Y')}.",
                "url": "/",
                "tag": event_key,
            }, ttl=12 * 3600)
            if result["sent"] > 0:
                _record_deliveries(user_id, [event_key], int(now.timestamp()))
                sent += 1
    return sent


def _snapshot_lessons_for_date(grade: str, target_date: date) -> list[dict]:
    week_start = _monday_of(target_date)
    row = get_db().execute(
        "SELECT lessons_json FROM notification_snapshots WHERE grade = ? AND week_start = ?",
        (grade, week_start.isoformat()),
    ).fetchone()
    if not row:
        return []
    try:
        lessons = json.loads(row["lessons_json"])
    except (TypeError, json.JSONDecodeError):
        return []
    return [
        lesson for lesson in lessons
        if lesson.get("date") == target_date.isoformat() and lesson.get("status") != "entfaellt"
    ]


def _send_daily_summaries(now: datetime) -> int:
    users = get_db().execute(
        """
        SELECT DISTINCT u.id, u.username, u.profile_json
        FROM users u
        JOIN push_subscriptions p ON p.user_id = u.id
        ORDER BY u.id
        """
    ).fetchall()
    target_date = now.date() + timedelta(days=1)
    sent = 0
    course_maps: dict[str, dict[str, str]] = {}
    for row in users:
        user_id = int(row["id"])
        profile = _load_profile_for_user(row)
        grade = _normalise_grade(profile.get("grade"))
        preferences = profile["notificationPreferences"]
        if (
            not grade
            or not preferences["enabled"]
            or not preferences["dailySummary"]
            or now.strftime("%H:%M") < preferences["dailySummaryTime"]
        ):
            continue
        event_key = f"daily:{target_date.isoformat()}"
        if _delivery_exists(user_id, event_key):
            continue
        selected = _profile_selected_courses(profile, grade)
        if not selected:
            continue
        course_map = course_maps.setdefault(grade, _course_map_normalized_for_grade(grade))
        lessons = []
        for lesson in _snapshot_lessons_for_date(grade, target_date):
            event = {"grade": grade, "lesson": lesson, "previous": None}
            if _lesson_matches_selected_courses(event, selected, course_map):
                lessons.append(lesson)
        lessons.sort(key=lambda lesson: lesson.get("start") or "")
        if lessons:
            first = lessons[0]
            body = (
                f"{len(lessons)} Stunden. Erste Stunde: "
                f"{_notification_subject(first, course_map)} um {first.get('start') or '--:--'}."
            )
        else:
            body = "Keine ausgewählten Kurse im Stundenplan."
        result = _send_push_to_user(user_id, {
            "title": f"Morgen, {target_date.strftime('%d.%m.')}",
            "body": body,
            "url": "/",
            "tag": event_key,
        }, ttl=12 * 3600)
        if result["sent"] > 0:
            _record_deliveries(user_id, [event_key], int(now.timestamp()))
            sent += 1
    return sent


def _run_notification_cycle(now: datetime | None = None) -> dict:
    current = now or datetime.now(APP_TZ)
    events = _fetch_notification_timetable_events(current)
    result = {
        "events": len(events),
        "timetableUsers": _send_timetable_event_notifications(events, current),
        "examReminders": _send_exam_reminders(current),
        "dailySummaries": _send_daily_summaries(current),
    }
    cutoff = int(current.timestamp()) - 90 * 24 * 3600
    db = get_db()
    db.execute("DELETE FROM notification_deliveries WHERE created_at < ?", (cutoff,))
    db.execute(
        "DELETE FROM notification_snapshots WHERE updated_at < ?",
        (int(current.timestamp()) - 28 * 24 * 3600,),
    )
    db.execute(
        "INSERT INTO notification_runtime (key, value) VALUES ('last_result', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (json.dumps({**result, "finishedAt": current.isoformat()}, ensure_ascii=False),),
    )
    db.commit()
    return result


def _acquire_notification_monitor_lease(now_ts: int) -> bool:
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT value FROM notification_runtime WHERE key = 'monitor_lease_until'"
        ).fetchone()
        lease_until = int(row["value"]) if row else 0
        if lease_until > now_ts:
            db.rollback()
            return False
        db.execute(
            "INSERT INTO notification_runtime (key, value) VALUES ('monitor_lease_until', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(now_ts + max(600, NOTIFICATION_CHECK_INTERVAL_SECONDS * 3)),),
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def _release_notification_monitor_lease() -> None:
    db = get_db()
    db.execute(
        "INSERT INTO notification_runtime (key, value) VALUES ('monitor_lease_until', '0') "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    )
    db.commit()


_notification_worker_started = False


def _start_notification_worker() -> None:
    global _notification_worker_started
    if _notification_worker_started or not NOTIFICATION_MONITOR_ENABLED:
        return
    if not _push_configured():
        app.logger.warning("notification monitor is enabled but Web Push is not configured")
        return
    _notification_worker_started = True

    def _worker():
        time.sleep(3)
        while True:
            acquired = False
            try:
                with app.app_context():
                    now_ts = int(time.time())
                    acquired = _acquire_notification_monitor_lease(now_ts)
                    if acquired:
                        result = _run_notification_cycle(datetime.now(APP_TZ))
                        app.logger.info("notification monitor completed: %s", result)
            except Exception:
                app.logger.exception("notification monitor failed")
            finally:
                if acquired:
                    try:
                        with app.app_context():
                            _release_notification_monitor_lease()
                    except Exception:
                        app.logger.exception("notification monitor lease release failed")
            time.sleep(NOTIFICATION_CHECK_INTERVAL_SECONDS)

    threading.Thread(target=_worker, name="notification-monitor", daemon=True).start()


_auto_backup_started = False


def _start_auto_backup_worker():
    """Fire a daemon thread that pushes backups on a fixed interval (default: 5 min)."""
    global _auto_backup_started
    if _auto_backup_started:
        return
    if not BACKUP_WEBHOOK_URL or AUTO_BACKUP_INTERVAL_MIN <= 0:
        return
    _auto_backup_started = True

    interval = max(1, AUTO_BACKUP_INTERVAL_MIN) * 60

    def _worker():
        # fire one backup immediately, then on interval
        try:
            with app.app_context():
                _maybe_send_backup("auto_timer_initial")
        except Exception as exc:
            app.logger.warning("auto-backup initial push failed: %s", exc)
        while True:
            try:
                with app.app_context():
                    _maybe_send_backup("auto_timer")
            except Exception as exc:
                app.logger.warning("auto-backup loop failed: %s", exc)
            time.sleep(interval)

    t = threading.Thread(target=_worker, name="auto-backup", daemon=True)
    t.start()

# Attempt a one-time auto-restore on cold start if the DB is empty, then start periodic backups
try:
    with app.app_context():
        if _maybe_auto_restore():
            _maybe_send_backup("startup")
            _start_auto_backup_worker()
            _start_notification_worker()
        else:
            app.logger.error(
                "startup backup disabled because the user database could not "
                "be restored safely"
            )
            if AUTO_RESTORE_URL:
                raise RuntimeError("configured remote backup could not be restored")
except Exception:
    app.logger.exception("auto-restore hook failed")
    if AUTO_RESTORE_URL:
        raise


@app.route("/api/admin/backup")
def admin_backup():
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    payload = _build_backup_payload()
    _maybe_send_backup("admin_backup", payload)
    # fixed filename so browser download matches the single-file backup pattern
    filename = "untis-backup.json"
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
    _maybe_send_backup("admin_restore")
    return _no_store(jsonify({"ok": True}))

@app.route("/api/admin/state")
def admin_state():
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    active_subjects_by_grade = {
        grade: _current_subjects_for_grade(grade)
        for grade in SUPPORTED_GRADES
    }
    groups_by_grade = {
        grade: dict(sorted(_group_variants(active_subjects_by_grade[grade]).items()))
        for grade in SUPPORTED_GRADES
    }
    courses_by_grade = {
        grade: _active_course_map_for_grade(grade, active_subjects_by_grade[grade])
        for grade in SUPPORTED_GRADES
    }
    rooms   = _parse_mapping(ROOM_MAP_PATH)
    groups_rm  = _group_variants(SEEN_ROOMS_RAW)

    unmapped_by_grade = {
        grade: [nk for nk in sorted(groups_by_grade[grade].keys()) if nk not in courses_by_grade[grade]]
        for grade in SUPPORTED_GRADES
    }
    unmapped_rm  = [nk for nk in sorted(groups_rm.keys())  if nk not in rooms]

    user_rows = []
    try:
        cur = get_db().execute(
            """
            SELECT u.id, u.username, COUNT(p.id) AS push_subscription_count
            FROM users u
            LEFT JOIN push_subscriptions p ON p.user_id = u.id
            GROUP BY u.id, u.username
            ORDER BY LOWER(u.username)
            """
        )
        rows = cur.fetchall()
        user_rows = []
        for row in rows:
            username = row["username"] if isinstance(row, dict) else row[1]
            uid = row["id"] if isinstance(row, dict) else row[0]
            user_rows.append({
                "id": uid,
                "username": username,
                "push_subscription_count": int(row["push_subscription_count"] or 0),
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

    settings_payload = {
        key: _get_setting(key, default)
        for key, default in SETTINGS_DEFAULTS.items()
        if key != "imageBannerData"
    }
    push_subscription_count = int(
        get_db().execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
    )
    last_notification_result = None
    try:
        result_row = get_db().execute(
            "SELECT value FROM notification_runtime WHERE key = 'last_result'"
        ).fetchone()
        if result_row:
            last_notification_result = json.loads(result_row["value"])
    except (TypeError, json.JSONDecodeError):
        last_notification_result = None

    return _no_store(jsonify({
        "ok": True,
        "schoolyear": _current_schoolyear_label(),
        **{
            f"courses_{grade.lower()}": courses_by_grade[grade]
            for grade in SUPPORTED_GRADES
        },
        "rooms": rooms,
        **{
            f"subjects_grouped_{grade.lower()}": groups_by_grade[grade]
            for grade in SUPPORTED_GRADES
        },
        "rooms_grouped": groups_rm,
        **{
            f"unmapped_subjects_{grade.lower()}": unmapped_by_grade[grade]
            for grade in SUPPORTED_GRADES
        },
        "unmapped_rooms": unmapped_rm,
        "users": user_rows,
        "push": {
            "configured": _push_configured(),
            "subscription_count": push_subscription_count,
            "monitor_enabled": NOTIFICATION_MONITOR_ENABLED,
            "check_interval_seconds": NOTIFICATION_CHECK_INTERVAL_SECONDS,
            "last_result": last_notification_result,
        },
        "vacations": vacations,
        "settings": settings_payload,
        "image_banner": _image_banner_payload(include_disabled=True),
        "exams_manual": exams_manual,
    }))

@app.route("/api/admin/save", methods=["POST"])
def admin_save():
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    new_rooms: dict   = payload.get("rooms") or {}
    new_settings: dict = payload.get("settings") or {}

    if payload.get("courses"):
        return jsonify({
            "ok": False,
            "error": "grade_required_for_course_mappings",
        }), 400

    new_courses_by_grade: dict[str, dict] = {}
    for grade in SUPPORTED_GRADES:
        key = f"courses_{grade.lower()}"
        if key not in payload:
            continue
        incoming = payload.get(key)
        if not isinstance(incoming, dict):
            return jsonify({"ok": False, "error": f"invalid_{key}"}), 400
        new_courses_by_grade[grade] = incoming

    for grade, incoming in new_courses_by_grade.items():
        active_subjects = _current_subjects_for_grade(grade)
        active_keys = {norm_key(subject) for subject in active_subjects}
        active_keys.discard("")
        if not active_keys:
            return jsonify({
                "ok": False,
                "error": f"active_course_catalog_unavailable_{grade.lower()}",
            }), 503
        grade_courses = _active_course_map_for_grade(grade, active_subjects)
        for k, v in incoming.items():
            nk = norm_key(k)
            if nk in active_keys:
                grade_courses[nk] = (v or "").strip()
        _course_map_write_for_grade(grade, grade_courses)

    rooms   = _parse_mapping(ROOM_MAP_PATH)

    for k, v in new_rooms.items():
        rooms[norm_key(k)] = (v or "").strip()

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

    _maybe_send_backup("admin_save")
    saved_grade_courses = sum(len(v) for v in new_courses_by_grade.values() if isinstance(v, dict))
    return _no_store(jsonify({
        "ok": True,
        "saved_courses": saved_grade_courses,
        "saved_rooms": len(new_rooms),
        "saved_settings": len(sanitized_settings),
    }))


@app.route("/api/admin/push/test", methods=["POST"])
def admin_push_test():
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    if not _push_configured():
        return jsonify({"ok": False, "error": "push_not_configured"}), 503

    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "Test-Benachrichtigung").strip()[:80]
    body = str(data.get("body") or "Push-Benachrichtigungen funktionieren.").strip()[:240]
    target_raw = data.get("user_id")
    target_user_id = None
    if target_raw not in (None, "", "all"):
        try:
            target_user_id = int(target_raw)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "push_target_invalid"}), 400
    if not title or not body:
        return jsonify({"ok": False, "error": "push_message_invalid"}), 400

    notification_url = str(data.get("url") or "/").strip()
    if not notification_url.startswith("/") or notification_url.startswith("//"):
        notification_url = "/"
    notification = {
        "title": title,
        "body": body,
        "url": notification_url,
        "tag": f"admin-test-{int(time.time())}",
    }

    sql = (
        "SELECT id, user_id, endpoint, p256dh, auth FROM push_subscriptions"
        + (" WHERE user_id = ?" if target_user_id is not None else "")
        + " ORDER BY id"
    )
    params = (target_user_id,) if target_user_id is not None else ()
    rows = get_db().execute(sql, params).fetchall()
    if not rows:
        return jsonify({"ok": False, "error": "push_no_subscriptions"}), 409

    result = _send_push_rows(rows, notification, ttl=300)
    return _no_store(jsonify({"ok": result["failed"] == 0, **result}))


@app.route("/api/admin/push/run-monitor", methods=["POST"])
def admin_run_notification_monitor():
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    if not _push_configured():
        return jsonify({"ok": False, "error": "push_not_configured"}), 503
    if not _acquire_notification_monitor_lease(int(time.time())):
        return jsonify({"ok": False, "error": "notification_monitor_busy"}), 409
    try:
        result = _run_notification_cycle(datetime.now(APP_TZ))
        return _no_store(jsonify({"ok": True, **result}))
    except Exception:
        app.logger.exception("manual notification monitor run failed")
        return jsonify({"ok": False, "error": "notification_monitor_failed"}), 500
    finally:
        _release_notification_monitor_lease()


@app.route("/api/admin/banner-image", methods=["POST", "DELETE"])
def admin_banner_image():
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    if request.method == "DELETE":
        _set_settings({
            "imageBannerData": "",
            "imageBannerEnabled": "0",
            "imageBannerUpdatedAt": str(int(time.time() * 1000)),
        })
        _maybe_send_backup("admin_banner_delete")
        return _no_store(jsonify({"ok": True, "image_banner": None}))

    if request.content_length and request.content_length > IMAGE_BANNER_MAX_REQUEST_BYTES:
        return jsonify({"ok": False, "error": "banner_image_too_large"}), 413
    payload = request.get_json(silent=True) or {}
    current_data = str(_get_setting("imageBannerData", "") or "").strip()
    incoming_data = payload.get("imageData")
    if incoming_data is not None:
        try:
            _decode_banner_image(incoming_data)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        current_data = str(incoming_data).strip()
    if not current_data:
        return jsonify({"ok": False, "error": "banner_image_required"}), 400

    alt = str(payload.get("alt") or "Schulbanner").strip()[:180]
    enabled = _setting_as_bool(payload.get("enabled", True))
    _set_settings({
        "imageBannerData": current_data,
        "imageBannerEnabled": "1" if enabled else "0",
        "imageBannerAlt": alt,
        "imageBannerUpdatedAt": str(int(time.time() * 1000)),
    })
    _maybe_send_backup("admin_banner_save")
    return _no_store(jsonify({
        "ok": True,
        "image_banner": _image_banner_payload(include_disabled=True),
    }))

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
    _maybe_send_backup("admin_vacations_create")
    return _no_store(jsonify({"ok": True}))

@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
def admin_delete_user(user_id: int):
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    db = get_db()
    db.execute("DELETE FROM push_subscriptions WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM notification_deliveries WHERE user_id = ?", (user_id,))
    cur = db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    if cur.rowcount == 0:
        return _no_store(jsonify({"ok": False, "error": "not_found"})), 404
    _maybe_send_backup("admin_user_delete")
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
    _maybe_send_backup("admin_vacation_delete")
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
    _maybe_send_backup("admin_exams_create")
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
    _maybe_send_backup("admin_exams_delete")
    return _no_store(jsonify({"ok": True, "deleted": exam_id}))

if __name__ == "__main__":
    debug_enabled = str(os.environ.get("FLASK_DEBUG", "")).lower() in ("1", "true", "yes")
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("PORT", "5000"))
    except (TypeError, ValueError):
        port = 5000
    app.run(host=host, port=port, debug=debug_enabled)
