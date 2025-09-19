import os, json
from datetime import datetime, timedelta, date
from flask import Flask, jsonify, render_template
from untis_client import fetch_week

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/timetable")
def api_timetable():
    today = date.today()
    week_start = today - timedelta(days=today.weekday())

    lessons = None

    # Prüfen, ob lessons_mapped.json existiert
    if os.path.exists("lessons_mapped.json"):
        try:
            with open("lessons_mapped.json", "r", encoding="utf-8") as f:
                lessons = json.load(f)
        except Exception as e:
            print("⚠️ Fehler beim Laden von lessons_mapped.json:", e)

    # Fallback: live von Untis
    if not lessons:
        lessons = fetch_week(week_start)

    return jsonify({"weekStart": str(week_start), "lessons": lessons})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
