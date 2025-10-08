import os, json, time, re
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from flask import (
    Flask, jsonify, render_template, request,
    redirect, url_for, session
)

import time, json, os, traceback
from flask import jsonify, make_response

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

# ---------- Normalisation (canonical across app) ----------
_UML = str.maketrans({"ä":"a","ö":"o","ü":"u","Ä":"a","Ö":"o","Ü":"u"})

def norm_key(s: str) -> str:
    """Canonical key for subjects/rooms: lower, umlaut fold, strip () content, dashes, tags, loose numbers, collapse spaces."""
    if not s:
        return ""
    s = s.strip().translate(_UML).lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\(.*?\)", " ", s)          # remove (…) blocks
    s = re.sub(r"\s*-\s*.*$", " ", s)       # cut after dash
    s = re.sub(r"\b(gk|lk|ag)\b", " ", s)   # simple tags
    s = re.sub(r"\b\d+\b", " ", s)          # lone numbers
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
    """Unified course list for selection: mapping RHS labels + seen variants + cached mapped lessons.
    - Includes entries even if course_mapping.txt RHS is empty by falling back to a representative variant or pretty key.
    - Sorted case-insensitively (de locale not available server-side; use basic lower()).
    """
    course_map = _parse_mapping(COURSE_MAP_PATH)  # { norm_key: display }
    seen_variants = _group_variants(SEEN_SUBJECTS_RAW)  # { norm_key: [raw variants] }

    display_set = set()

    # 1) From mapping file: take non-empty RHS
    for nk, disp in course_map.items():
        if (disp or "").strip():
            display_set.add(disp.strip())

    # 2) From seen variants: if no RHS, choose a representative variant (shortest)
    for nk, variants in seen_variants.items():
        if not variants:
            continue
        if nk in course_map and (course_map[nk] or "").strip():
            continue  # already covered by mapping label
        rep = sorted(variants, key=lambda s: (len(s), s.lower()))[0]
        display_set.add(rep)

    # 3) From cached mapped lessons (optional)
    try:
        lm_path = os.path.join(ROOT, "lessons_mapped.json")
        if os.path.exists(lm_path):
            with open(lm_path, "r", encoding="utf-8") as f:
                lessons = json.load(f)
                if isinstance(lessons, list):
                    for L in lessons:
                        subj = (L.get("subject") or "").strip()
                        if subj:
                            display_set.add(subj)
    except Exception:
        pass

    # 4) Also include mapping keys with empty RHS by prettifying the normalised key
    for nk, disp in course_map.items():
        if (disp or "").strip():
            continue
        pretty = " ".join(w.capitalize() for w in nk.split())
        if pretty:
            display_set.add(pretty)

    out = sorted(display_set, key=lambda s: (s.lower(), s))
    return _no_store(jsonify({"ok": True, "courses": out}))

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
