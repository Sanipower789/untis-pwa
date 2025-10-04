import os, json, time
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, render_template, request, make_response, redirect, url_for, session, send_from_directory

# ---- your existing untis client ----
from untis_client import fetch_week

APP_TZ = ZoneInfo("Europe/Berlin")
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = ROOT  # keep mappings in project root like before

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(24))

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")  # set on Render: ADMIN_TOKEN=deinpasswort

# ---------- helpers ----------
def _norm(s: str) -> str:
    return " ".join((s or "").strip().split()).lower()

def _parse_mapping(file_path: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not os.path.exists(file_path):
        return mapping
    with open(file_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"): continue
            if "=" not in line: continue
            left, right = line.split("=", 1)
            left = " ".join(left.strip().split())
            right = right.strip()  # allow empty on purpose (rooms hidden)
            mapping[_norm(left)] = right
    return mapping

def _write_mapping_txt(path: str, mapping: dict[str, str]) -> None:
    # Write normalized-left = exact-right, keep stable ordering
    lines = []
    for k in sorted(mapping.keys()):
        left_print = k  # already normalized for key
        right = mapping[k]
        lines.append(f"{left_print} = {right}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))

def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())

def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

# ---- seen stores (auto-learn) ----
SEEN_SUB_PATH = os.path.join(DATA, "seen_subjects.json")
SEEN_ROOM_PATH = os.path.join(DATA, "seen_rooms.json")

def _load_seen(path: str) -> set[str]:
    if not os.path.exists(path): return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            arr = json.load(f)
            return set(arr if isinstance(arr, list) else [])
    except:  # corrupt -> reset
        return set()

def _save_seen(path: str, values: set[str]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(values), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

SEEN_SUBJECTS = _load_seen(SEEN_SUB_PATH)
SEEN_ROOMS    = _load_seen(SEEN_ROOM_PATH)
_last_seen_flush = 0

def record_seen(lessons: list[dict]):
    global _last_seen_flush
    changed = False
    for L in lessons:
        s = _norm(L.get("subject_original") or L.get("subject") or "")
        r = _norm(L.get("room") or "")
        if s and s not in SEEN_SUBJECTS:
            SEEN_SUBJECTS.add(s); changed = True
        if r and r not in SEEN_ROOMS:
            SEEN_ROOMS.add(r); changed = True
    # flush at most every 15s, or when changed and >15s since last flush
    now = time.time()
    if changed and (now - _last_seen_flush > 15):
        _save_seen(SEEN_SUB_PATH, SEEN_SUBJECTS)
        _save_seen(SEEN_ROOM_PATH, SEEN_ROOMS)
        _last_seen_flush = now

# ---------- routes ----------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/mappings")
def api_mappings():
    course_map = _parse_mapping(os.path.join(DATA, "course_mapping.txt"))
    room_map   = _parse_mapping(os.path.join(DATA, "rooms_mapping.txt"))
    return _no_store(jsonify({"ok": True, "courses": course_map, "rooms": room_map}))

@app.route("/api/timetable")
def api_timetable():
    qs = request.args.get("weekStart")
    if qs:
        try:
            ws = datetime.strptime(qs, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"ok": False, "error": "bad weekStart; use YYYY-MM-DD"}), 400
    else:
        today = datetime.now(APP_TZ).date()
        if today.weekday() in (5, 6):
            ws = _monday_of(today) + timedelta(days=7)  # weekend → next week
        else:
            ws = _monday_of(today)

    try:
        lessons = fetch_week(ws)
    except Exception as e:
        app.logger.exception("fetch_week failed")
        return _no_store(jsonify({"ok": False, "weekStart": str(ws), "lessons": [], "error": str(e)}))

    # auto-learn what Untis delivered
    record_seen(lessons)

    return _no_store(jsonify({"ok": True, "weekStart": str(ws), "lessons": lessons}))

# ---- Admin: auth & UI ----
def _require_admin():
    if ADMIN_TOKEN and session.get("admin_ok") == True:
        return True
    return False

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

    courses = _parse_mapping(os.path.join(DATA, "course_mapping.txt"))
    rooms   = _parse_mapping(os.path.join(DATA, "rooms_mapping.txt"))
    seen_sub = sorted(SEEN_SUBJECTS)
    seen_room= sorted(SEEN_ROOMS)

    # mark which seen keys are not yet mapped
    unmapped_sub = [s for s in seen_sub if s not in courses]
    unmapped_room= [r for r in seen_room if r not in rooms]

    return _no_store(jsonify({
        "ok": True,
        "courses": courses,
        "rooms": rooms,
        "seen_subjects": seen_sub,
        "seen_rooms": seen_room,
        "unmapped_subjects": unmapped_sub,
        "unmapped_rooms": unmapped_room
    }))

@app.route("/api/admin/save", methods=["POST"])
def admin_save():
    if not _require_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    new_courses: dict = payload.get("courses") or {}
    new_rooms: dict   = payload.get("rooms") or {}

    # load current mappings
    courses = _parse_mapping(os.path.join(DATA, "course_mapping.txt"))
    rooms   = _parse_mapping(os.path.join(DATA, "rooms_mapping.txt"))

    # merge (normalize keys, keep right side exactly as given — empty allowed)
    for k, v in new_courses.items():
        courses[_norm(k)] = (v or "").strip()
    for k, v in new_rooms.items():
        rooms[_norm(k)] = (v or "").strip()

    _write_mapping_txt(os.path.join(DATA, "course_mapping.txt"), courses)
    _write_mapping_txt(os.path.join(DATA, "rooms_mapping.txt"), rooms)

    return _no_store(jsonify({"ok": True, "saved_courses": len(new_courses), "saved_rooms": len(new_rooms)}))

@app.after_request
def add_no_cache(resp):
    return _no_store(resp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)