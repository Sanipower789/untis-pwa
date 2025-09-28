import os
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template, request, send_from_directory, make_response
from untis_client import fetch_week
from zoneinfo import ZoneInfo

app = Flask(__name__, static_folder="static", template_folder="templates")

# --- serve lessons_mapped.json from repo root (no caching) ---
@app.route("/lessons_mapped.json")
def lessons_mapped():
    resp = make_response(send_from_directory(app.root_path, "lessons_mapped.json"))
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

@app.route("/")
def index():
    # Expect templates/index.html in your repo (unchanged)
    return render_template("index.html")

@app.route("/api/timetable")
def api_timetable():
    # optional ?weekStart=YYYY-MM-DD (DE time)
    qs = request.args.get("weekStart")
    if qs:
        try:
            ws = datetime.strptime(qs, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"ok": False, "error": "bad weekStart; use YYYY-MM-DD"}), 400
    else:
        today = datetime.now(ZoneInfo("Europe/Berlin")).date()
        ws = today - timedelta(days=today.weekday())  # Monday of current week

    lessons = fetch_week(ws)

    resp = jsonify({
        "ok": True,
        "weekStart": str(ws),
        "lessons": lessons
    })
    # Ensure the app always sees fresh data (SW uses network-first anyway)
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

@app.route("/api/debug")
def api_debug():
    # Minimal, safe debug info (no secrets)
    data = {
        "ok": True,
        "server_time": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(timespec="seconds"),
        "has_lessons_mapped": os.path.exists(os.path.join(app.root_path, "lessons_mapped.json")),
    }
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)