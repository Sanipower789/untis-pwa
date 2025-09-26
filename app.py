import os, json
from datetime import datetime, timedelta, date
from flask import Flask, jsonify, render_template,jsonify, request
from untis_client import fetch_week
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, date

app = Flask(__name__)

# --- serve lessons_mapped.json from repo root (no caching) ---
from flask import send_from_directory, make_response
import os

@app.route("/lessons_mapped.json")
def lessons_mapped():
    resp = make_response(send_from_directory(app.root_path, "lessons_mapped.json"))
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/timetable")  
def api_timetable():  
    # optional ?weekStart=YYYY-MM-DD  
    qs = request.args.get("weekStart")  
    if qs:  
        try:  
            ws = datetime.strptime(qs, "%Y-%m-%d").date()  
        except ValueError:  
            return jsonify({"ok": False, "error": "bad weekStart"})  
    else:  
        today = datetime.now(ZoneInfo("Europe/Berlin")).date()
        ws = today - timedelta(days=today.weekday())  # Monday of THIS week (DE time)

    lessons = fetch_week(ws)  
  
    resp = jsonify({  
        "ok": True,  
        "weekStart": str(ws),  
        "lessons": lessons  
    })  
    resp.headers["Cache-Control"] = "no-store, max-age=0"  
    return resp  

@app.route("/api/debug")
def api_debug():
    # ... build debug payload ...
    resp = jsonify({...})
    resp.headers["Cache-Control"] = "no-store, max-age=0"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
