from flask import send_from_directory
import os, json
from datetime import date, timedelta
from flask import (
    Flask, request, jsonify, session, redirect, url_for, render_template
)
from werkzeug.security import generate_password_hash, check_password_hash

# ---- Untis Fetch ----
from untis_client import fetch_week

# Flask so konfigurieren, dass Templates auch im Projekt-Root gefunden werden
app = Flask(__name__, static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "dev-only-change-me")

USERS_FILE = os.getenv("USERS_FILE", "users.json")


# ---------- Helpers ----------
def load_users() -> dict:
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_users(users: dict) -> None:
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    os.replace(tmp, USERS_FILE)

def current_user() -> str | None:
    return session.get("user")


# ---------- Login-Gate (Auto-Redirect) ----------
PUBLIC_PATHS = {
    "/", "/login", "/api/login", "/api/register",
    "/favicon.ico",
}
# alles unter /static/ ist öffentlich
def _is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS: 
        return True
    return path.startswith("/static/")

@app.before_request
def require_login():
    # PWA-Service-Worker und Manifest dürfen auch ohne Login kommen
    if _is_public_path(request.path):
        return None
    if current_user() is None:
        # GET -> Redirect auf /login (mit Rücksprungziel)
        if request.method == "GET":
            nxt = request.path or "/"
            if request.query_string:
                nxt += "?" + request.query_string.decode("utf-8")
            return redirect(url_for("page_login", next=nxt))
        # API-Calls -> 401 JSON
        return jsonify({"ok": False, "error": "auth_required"}), 401
    return None


# ---------- Pages ----------
@app.get("/")
def home():
    # serve ./index.html from the repo root
    return send_from_directory(".", "index.html")

@app.get("/login")
def page_login():
    # serve ./login.html from the repo root
    return send_from_directory(".", "login.html")

# ---------- Auth API ----------
@app.post("/api/register")
def api_register():
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"ok": False, "error": "missing_fields"}), 400

    users = load_users()
    if username in users:
        return jsonify({"ok": False, "error": "user_exists"}), 409

    users[username] = {
        "password": generate_password_hash(password),
        "name": "",
        "courses": [],  # Liste von Fächern
    }
    save_users(users)
    return jsonify({"ok": True})

@app.post("/api/login")
def api_login():
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    users = load_users()
    user = users.get(username)
    if not user or not check_password_hash(user.get("password", ""), password):
        return jsonify({"ok": False, "error": "invalid_credentials"}), 401

    session["user"] = username
    return jsonify({"ok": True, "user": username})

@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify({"ok": True})

@app.get("/api/me")
def api_me():
    u = current_user()
    if not u:
        return jsonify({"ok": False, "user": None})
    users = load_users()
    me = users.get(u, {})
    return jsonify({
        "ok": True,
        "user": u,
        "name": me.get("name", ""),
        "courses": me.get("courses", []),
    })


# ---------- Nutzer-Daten (Name + Kurse) ----------
@app.get("/api/user/courses")
def get_user_courses():
    u = current_user()
    users = load_users()
    me = users.get(u, {})
    return jsonify({
        "ok": True,
        "name": me.get("name", ""),
        "courses": me.get("courses", []),
    })

@app.post("/api/user/courses")
def set_user_courses():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    courses = data.get("courses") or []

    u = current_user()
    users = load_users()
    if u not in users:
        return jsonify({"ok": False, "error": "user_missing"}), 400

    users[u]["name"] = name
    # nur Strings zulassen
    users[u]["courses"] = [str(c) for c in courses][:12]
    save_users(users)
    return jsonify({"ok": True})


# ---------- Timetable API ----------
@app.get("/api/timetable")
def api_timetable():
    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # Montag

    lessons = None
    # 1) Versuche lokales Mapping zu liefern
    try:
        if os.path.exists("lessons_mapped.json"):
            with open("lessons_mapped.json", "r", encoding="utf-8") as f:
                lessons = json.load(f)
    except Exception as e:
        print("⚠️ lessons_mapped.json konnte nicht geladen werden:", e)

    # 2) Fallback: Live von Untis
    if not lessons:
        try:
            lessons = fetch_week(week_start)
        except Exception as e:
            print("⚠️ fetch_week Fehlgeschlagen:", e)
            lessons = []

    return jsonify({"weekStart": str(week_start), "lessons": lessons})


# ---------- Main ----------
if __name__ == "__main__":
    # Für lokales Debuggen
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)