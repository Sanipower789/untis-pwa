import os
from pathlib import Path
from datetime import datetime, timedelta, date
from typing import Optional, Dict, Any
from flask import (
    Flask, request, jsonify, session, redirect, url_for, send_file
)
from werkzeug.security import generate_password_hash, check_password_hash

# --- Flask App ---
app = Flask(__name__)
# sichere Secret-Key-Quelle bevorzugen, ansonsten Fallback (später in Render/Deta als SECRET_KEY setzen)
app.secret_key = os.getenv("SECRET_KEY", "dev-change-me-please-123")

# --- Robust HTML-Serving (templates ODER Projektrouterlaubt) ---
ROOT = Path(__file__).resolve().parent

def _html_path(filename: str) -> Optional[Path]:
    for p in (ROOT / "templates" / filename, ROOT / filename):
        if p.exists():
            return p
    return None

def _serve_html(filename: str):
    p = _html_path(filename)
    if not p:
        return f"{filename} not found (looked in /templates and project root)", 500
    return send_file(p)

# --- Fake-Persistence (RAM) -> später Deta Base ---
USERS: Dict[str, Dict[str, Any]] = {}
USERDATA: Dict[str, Dict[str, Any]] = {}  # z.B. {"courses":[...], "name":"..."}

def current_user() -> Optional[str]:
    return session.get("user")

# --- Untis Import (falls vorhanden) ---
try:
    from untis_client import fetch_week  # deine bestehende Funktion
except Exception:
    fetch_week = None

# ---------- ROUTES: Seiten ----------

@app.get("/")
def home():
    # Wenn nicht eingeloggt -> Login
    if not current_user():
        return redirect(url_for("page_login"))
    # Eingeloggt -> index.html rendern
    return _serve_html("index.html")

@app.get("/login")
def page_login():
    return _serve_html("login.html")

# ---------- ROUTES: Auth-API ----------

@app.post("/api/register")
def api_register():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    if not username or not password:
        return jsonify(ok=False, error="missing_fields"), 400
    if username in USERS:
        return jsonify(ok=False, error="exists"), 409
    USERS[username] = {
        "pw": generate_password_hash(password),
        "created": datetime.utcnow().isoformat()
    }
    USERDATA.setdefault(username, {"courses": [], "name": ""})
    return jsonify(ok=True)

@app.post("/api/login")
def api_login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    u = USERS.get(username)
    if not u or not check_password_hash(u["pw"], password):
        return jsonify(ok=False, error="invalid"), 401
    session["user"] = username
    # Lebensdauer 30 Tage
    session.permanent = True
    app.permanent_session_lifetime = timedelta(days=30)
    return jsonify(ok=True)

@app.post("/api/logout")
def api_logout():
    session.pop("user", None)
    return jsonify(ok=True)

@app.get("/api/me")
def api_me():
    user = current_user()
    return jsonify(ok=True, user=user)

# ---------- ROUTES: User-Daten (Kursauswahl etc.) ----------

@app.get("/api/user/data")
def api_user_data_get():
    user = current_user()
    if not user:
        return jsonify(ok=False, error="unauth"), 401
    return jsonify(ok=True, data=USERDATA.get(user, {"courses": [], "name": ""}))

@app.post("/api/user/data")
def api_user_data_set():
    user = current_user()
    if not user:
        return jsonify(ok=False, error="unauth"), 401
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", ""))[:64]
    courses = data.get("courses") or []
    if not isinstance(courses, list):
        return jsonify(ok=False, error="bad_payload"), 400
    USERDATA[user] = {"name": name, "courses": courses}
    return jsonify(ok=True)

# ---------- ROUTES: Timetable ----------

@app.get("/api/timetable")
def api_timetable():
    # erlaubt auch ungeloggt, falls du es willst -> andernfalls blocken
    today = date.today()
    # Montag der Woche
    week_start = today - timedelta(days=today.weekday())
    if fetch_week is None:
        # Fallback: leere Liste (wenn untis_client fehlt)
        return jsonify(weekStart=str(week_start), lessons=[])
    try:
        lessons = fetch_week(week_start)  # <- deine bestehende Funktion
    except Exception as e:
        return jsonify(ok=False, error=str(e), lessons=[]), 500
    return jsonify(weekStart=str(week_start), lessons=lessons)

# ---------- Debug / Health ----------

@app.get("/api/health")
def api_health():
    return jsonify(ok=True, time=datetime.utcnow().isoformat())

# ---------- Run ----------

if __name__ == "__main__":
    # Lokales Debug
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
