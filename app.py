import os
from datetime import datetime, timedelta, date
from flask import Flask, jsonify, render_template, request, make_response
from zoneinfo import ZoneInfo

# keep your existing untis_client.py as-is
from untis_client import fetch_week

APP_TZ = ZoneInfo("Europe/Berlin")
ROOT = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder="static", template_folder="templates")

# ---------- helpers ----------
def _norm(s: str) -> str:
    return " ".join((s or "").strip().split()).lower()

def _parse_mapping(file_path: str) -> dict[str, str]:
    """
    Parse simple 'LEFT = RIGHT' mapping files.
    - Ignores blank lines and lines starting with '#'
    - Normalizes the LEFT key for robust lookups
    - Skips pairs with empty RIGHT
    """
    mapping: dict[str, str] = {}
    if not os.path.exists(file_path):
        return mapping
    with open(file_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            left, right = line.split("=", 1)
            left = " ".join(left.strip().split())
            right = right.strip()
            if not right:
                # if you want to allow blanks (meaning "hide" or keep as-is), drop this guard
                continue
            mapping[_norm(left)] = right
    return mapping

def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())

def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

# ---------- routes ----------
@app.route("/")
def index():
    # your templates/index.html stays unchanged
    return render_template("index.html")

@app.route("/api/mappings")
def api_mappings():
    course_map = _parse_mapping(os.path.join(ROOT, "course_mapping.txt"))
    room_map   = _parse_mapping(os.path.join(ROOT, "rooms_mapping.txt"))
    resp = jsonify({"ok": True, "courses": course_map, "rooms": room_map})
    return _no_store(resp)

@app.route("/api/timetable")
def api_timetable():
    # optional ?weekStart=YYYY-MM-DD (Berlin time) overrides the auto logic
    qs = request.args.get("weekStart")
    if qs:
        try:
            ws = datetime.strptime(qs, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"ok": False, "error": "bad weekStart; use YYYY-MM-DD"}), 400
    else:
        today = datetime.now(APP_TZ).date()
        # Monday = 0, Saturday = 5, Sunday = 6
        if today.weekday() in (5, 6):
            # show NEXT week when it's Sat/Sun
            ws = _monday_of(today) + timedelta(days=7)
        else:
            # show THIS week Mon–Fri
            ws = _monday_of(today)

    lessons = fetch_week(ws)
    resp = jsonify({"ok": True, "weekStart": str(ws), "lessons": lessons})
    return _no_store(resp)

@app.route("/api/debug")
def api_debug():
    has_courses = os.path.exists(os.path.join(ROOT, "course_mapping.txt"))
    has_rooms   = os.path.exists(os.path.join(ROOT, "rooms_mapping.txt"))
    resp = jsonify({
        "ok": True,
        "server_time": datetime.now(APP_TZ).isoformat(timespec="seconds"),
        "has_course_mapping": has_courses,
        "has_rooms_mapping": has_rooms
    })
    return _no_store(resp)

if __name__ == "__main__":
    # dev server
    app.run(host="0.0.0.0", port=5000, debug=True)