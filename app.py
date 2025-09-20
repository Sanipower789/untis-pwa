import os, json
from datetime import datetime, timedelta, date
from flask import Flask, jsonify, render_template
from untis_client import fetch_week
from flask import Flask, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
import json, os

app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY", "super-secret-key")

USERS_FILE = "users.json"

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f)

@app.route("/api/register", methods=["POST"])
def register():
    data = request.json
    username, password = data.get("username"), data.get("password")

    users = load_users()
    if username in users:
        return jsonify({"error": "User exists"}), 400

    users[username] = {
        "password": generate_password_hash(password),
        "courses": [],
        "name": ""
    }
    save_users(users)
    return jsonify({"status": "ok"})

@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    username, password = data.get("username"), data.get("password")

    users = load_users()
    user = users.get(username)
    if not user or not check_password_hash(user["password"], password):
        return jsonify({"error": "Invalid credentials"}), 401

    session["user"] = username
    return jsonify({"status": "ok", "user": username})

@app.route("/api/logout", methods=["POST"])
def logout():
    session.pop("user", None)
    return jsonify({"status": "ok"})

@app.route("/api/me")
def me():
    return jsonify({"user": session.get("user")})

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
