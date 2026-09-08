"""Render log collection and repair reservations. Never imports the web app.

The collector makes no model calls. Its CLI is used by the scheduled fixer;
check/claim/finish keep polling separate from consuming a repair attempt.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from dotenv import dotenv_values
import requests


ROOT = Path(__file__).resolve().parents[1]
API = "https://api.render.com/v1"
SERVICE_URL = "https://stundenplan.onrender.com"
MAX_PAGES = 20
WINDOW_SECONDS = 7200
COOLDOWN_SECONDS = 3600
DAILY_ATTEMPTS = 2
DEPLOY_FAILED = {"build_failed", "update_failed", "pre_deploy_failed", "canceled"}
SEVERITIES = {"error", "critical", "alert", "emergency"}


class MonitorError(RuntimeError):
    pass


class MonitorBusy(MonitorError):
    pass


def now_utc():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise MonitorError("Render supplied a timestamp without a timezone")
    return parsed.astimezone(timezone.utc)


def config():
    # Read at command time so credentials added after installation take effect.
    values = {**dotenv_values(ROOT / ".env"), **os.environ}
    return {
        "api_key": str(values.get("RENDER_API_KEY") or "").strip(),
        "service_id": str(values.get("RENDER_SERVICE_ID") or "").strip(),
        "owner_id": str(values.get("RENDER_OWNER_ID") or "").strip(),
        "state_path": Path(values.get("RENDER_MONITOR_STATE") or ROOT / ".render-monitor/state.json"),
        "secrets": [str(v) for k, v in values.items() if v and len(str(v)) >= 6
                    and any(tag in k.upper() for tag in ("TOKEN", "SECRET", "PASSWORD", "PASS", "KEY"))],
    }


def redact(message, cfg):
    text = str(message)
    for secret in sorted(cfg.get("secrets", []), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", text)
    text = re.sub(r'''(?i)(password|token|secret|api[_-]?key|cookie)["']?\s*[:=]\s*(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;]+)''',
                  r"\1=[REDACTED]", text)
    text = re.sub(r"https?://[^\s\"'<>]+", "[URL]", text)
    text = re.sub(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b", "[EMAIL]", text)
    return text[:3000]


def fingerprint(message):
    # Preserve the exception and source path; ignore only variable log metadata.
    access = re.search(r'"(GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS)\s+(\S+)\s+HTTP/[\d.]+"\s+(5\d\d)\b', message)
    if access:
        method, path, status = access.groups()
        message = f"HTTP {method} {path.split('?', 1)[0]} {status}"
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:\d{2})?", "", message)
    text = re.sub(r"\[(?:[a-z0-9_-]{4,})\]", "", text)
    text = re.sub(r", line \d+, in ", ", line <n>, in ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(text.encode()).hexdigest()[:20]


@contextmanager
def state_store(path):
    """An OS lock protects claims across processes and releases on process exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as lock:
        try:
            # Reading the locked byte itself raises PermissionError on Windows.
            if os.fstat(lock.fileno()).st_size == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise MonitorBusy("another monitor command is running") from exc
        try:
            if path.exists():
                try:
                    state = json.loads(path.read_text(encoding="utf-8"))
                except (ValueError, OSError) as exc:
                    raise MonitorError("Monitor state unreadable; repair attempts are disabled") from exc
                if not isinstance(state, dict) or state.get("version") != 1:
                    raise MonitorError("Unknown monitor state; repair attempts are disabled")
            else:
                state = {"version": 1, "incidents": {}, "seen": {}, "attempts": []}
            yield state
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def api_get(cfg, path, params=None):
    try:
        response = requests.get(
            API + path, params=params,
            headers={"Authorization": "Bearer " + cfg["api_key"], "Accept": "application/json"},
            timeout=(5, 20), allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise MonitorError("Render API unavailable: " + type(exc).__name__) from exc
    if response.status_code != 200:
        raise MonitorError(f"Render API HTTP {response.status_code}; no repair was started")
    try:
        return response.json()
    except ValueError as exc:
        raise MonitorError("Render API returned invalid JSON") from exc


def resolve_service(cfg, state):
    if cfg["service_id"]:
        if not re.fullmatch(r"srv-[a-z0-9]+", cfg["service_id"]):
            raise MonitorError("Invalid RENDER_SERVICE_ID")
        service = api_get(cfg, "/services/" + cfg["service_id"])
    else:
        cached = state.get("service")
        if cached:
            if cached.get("url") != SERVICE_URL or not cached.get("id") or not cached.get("ownerId"):
                raise MonitorError("Cached service identity is invalid")
            if cfg["owner_id"] and cfg["owner_id"] != cached["ownerId"]:
                raise MonitorError("Configured workspace does not own the service")
            return cached
        matches = []
        cursor = None
        for _ in range(MAX_PAGES):
            params = {"name": "stundenplan", "includePreviews": "false", "limit": 100}
            if cursor:
                params["cursor"] = cursor
            page = api_get(cfg, "/services", params)
            if not isinstance(page, list):
                raise MonitorError("Unexpected Render service list")
            for item in page:
                candidate = item.get("service", {})
                if candidate.get("serviceDetails", {}).get("url", "").rstrip("/") == SERVICE_URL:
                    matches.append(candidate)
            if len(page) < 100:
                break
            next_cursor = page[-1].get("cursor")
            if not next_cursor or next_cursor == cursor:
                raise MonitorError("Render service pagination did not advance")
            cursor = next_cursor
        else:
            raise MonitorError("Too many services; set RENDER_SERVICE_ID explicitly")
        if len(matches) != 1:
            raise MonitorError("Could not uniquely find stundenplan; set RENDER_SERVICE_ID")
        service = matches[0]
    if service.get("serviceDetails", {}).get("url", "").rstrip("/") != SERVICE_URL:
        raise MonitorError("Configured service does not match stundenplan.onrender.com")
    if cfg["owner_id"] and cfg["owner_id"] != service.get("ownerId"):
        raise MonitorError("Configured workspace does not own the service")
    if not service.get("id") or not service.get("ownerId"):
        raise MonitorError("Render service identity is incomplete")
    resolved = {"id": service["id"], "ownerId": service["ownerId"], "url": SERVICE_URL}
    state["service"] = resolved
    return resolved


def fetch_logs(cfg, service, start, end):
    rows = []
    for _ in range(MAX_PAGES):
        # Include warnings and traceback continuation lines. Repetition and severity
        # are assessed locally, since a stack trace may span multiple log levels.
        page = api_get(cfg, "/logs", {
            "ownerId": service["ownerId"], "resource": service["id"],
            "startTime": start, "endTime": end, "direction": "forward", "limit": 100,
        })
        if not isinstance(page, dict) or not isinstance(page.get("logs"), list) or not isinstance(page.get("hasMore"), bool):
            raise MonitorError("Unexpected Render log response; cursor not advanced")
        for row in page["logs"]:
            if not isinstance(row, dict) or not all(k in row for k in ("id", "timestamp", "message", "labels")):
                raise MonitorError("Malformed Render log entry; cursor not advanced")
            parse_time(row["timestamp"])
            rows.append(row)
        if not page["hasMore"]:
            return rows, None
        next_start, next_end = page.get("nextStartTime"), page.get("nextEndTime")
        if not next_start or not next_end or (next_start, next_end) == (start, end):
            raise MonitorError("Render log pagination did not advance")
        parse_time(next_start)
        parse_time(next_end)
        start, end = next_start, next_end
    return rows, {"start": start, "end": end}


def classify(row, message):
    labels = {item["name"]: item["value"] for item in row["labels"]}
    level = labels.get("level", "info").lower()
    lower = message.lower()
    if any(term in lower for term in ("database disk image is malformed", "database corruption", "refusing to replace")):
        return "data_safety"
    if re.search(r"\b(?:NameError|TypeError|ValueError|KeyError|AttributeError|ImportError|ModuleNotFoundError|SyntaxError|IndentationError|RuntimeError|OperationalError):", message):
        return "error"
    if "exited with status" in lower and not re.search(r"exited with status 0\b", lower):
        return "critical"
    status = str(labels.get("statusCode", ""))
    if level in {"critical", "alert", "emergency"}:
        return "critical"
    if level in SEVERITIES or status.startswith("5") or re.search(r'"\s+5\d\d\s', message):
        return "error"
    if level == "warning" and any(term in lower for term in ("failed", "timeout", "timed out", "database is busy", "locked")):
        return "warning"
    return None


def ingest(state, rows, cfg, now):
    stamp = now.timestamp()
    context = {}
    for row in rows:
        identity = str(row["id"])
        if identity in state["seen"]:
            continue
        state["seen"][identity] = stamp
        message = redact(row["message"], cfg)
        labels = {item["name"]: item["value"] for item in row["labels"]}
        event_time = parse_time(row["timestamp"]).timestamp()
        instance = labels.get("instance", "unknown")
        preceding = [r for r in context.get(instance, []) if event_time - 60 <= r[0] <= event_time]
        kind = classify(row, message)
        context[instance] = (preceding + [(event_time, message)])[-16:]
        if kind is None:
            continue
        key = fingerprint(message)
        record = state["incidents"].setdefault(key, {
            "fingerprint": key, "occurrences": [], "samples": [], "kind": kind,
            "last_attempt": 0, "suppress_until": 0,
        })
        record["occurrences"].append(event_time)
        record["last_seen"] = max(event_time, record.get("last_seen", 0))
        record["samples"] = (record["samples"] + [message])[-3:]
        record["context"] = [r[1] for r in preceding if any(t in r[1] for t in ("Traceback", "File ", "Error", "Exception", "ERROR"))][-8:]
    state["seen"] = {k: v for k, v in state["seen"].items() if v > stamp - 86400}
    state["incidents"] = {k: v for k, v in state["incidents"].items()
                          if max(v.get("last_seen", 0), v.get("last_attempt", 0)) > stamp - 7 * 86400}
    for record in state["incidents"].values():
        record["occurrences"] = [t for t in record["occurrences"] if t > stamp - WINDOW_SECONDS][-100:]


def candidates(state, now):
    result = []
    stamp = now.timestamp()
    for record in state["incidents"].values():
        if stamp < record["suppress_until"] or stamp - record["last_attempt"] < COOLDOWN_SECONDS:
            continue
        fresh = [t for t in record["occurrences"] if stamp - WINDOW_SECONDS < t <= stamp
                 and t > record["last_attempt"]]
        threshold = 1 if record["kind"] in ("critical", "data_safety") else 5 if record["kind"] == "warning" else 2
        if len(fresh) >= threshold:
            result.append({"fingerprint": record["fingerprint"], "kind": record["kind"],
                           "count": len(fresh), "samples": record["samples"],
                           "context": record.get("context", []), "last_seen": record["last_seen"]})
    return sorted(result, key=lambda r: (r["kind"] in ("critical", "data_safety"), r["last_seen"]), reverse=True)


def budget_reached(state, now):
    attempts = [t for t in state["attempts"] if t > now.timestamp() - 86400]
    return len(attempts) >= DAILY_ATTEMPTS or bool(attempts and now.timestamp() - max(attempts) < COOLDOWN_SECONDS)


def collect(cfg=None, now=None):
    cfg, now = cfg or config(), now or now_utc()
    if not cfg["api_key"]:
        return {"status": "configuration_required", "action_required": False, "missing": ["RENDER_API_KEY"]}
    with state_store(cfg["state_path"]) as state:
        service = resolve_service(cfg, state)
        if state.get("page"):
            start, end = state["page"]["start"], state["page"]["end"]
            window_end = state["window_end"]
        else:
            previous = parse_time(state["last_poll"]) if state.get("last_poll") else now - timedelta(hours=2)
            start = iso(max(previous - timedelta(seconds=30), now - timedelta(days=6)))
            end = window_end = iso(now)
        rows, page = fetch_logs(cfg, service, start, end)
        ingest(state, rows, cfg, now)
        state["page"], state["window_end"] = page, window_end
        if page is None:
            state["last_poll"] = window_end
        actionable = candidates(state, now)
        if state.get("active"):
            status = "repair_in_progress"
        elif state.get("paused"):
            status = "paused"
        elif actionable and budget_reached(state, now):
            status = "budget_reached"
        else:
            status = "action_required" if actionable else "no_action"
        return {"status": status, "action_required": status == "action_required",
                "service_id": service["id"], "log_count": len(rows), "has_backlog": page is not None,
                "incidents": actionable[:1] if status == "action_required" else [],
                "active": state.get("active")}


def claim(key, cfg=None, now=None):
    cfg, now = cfg or config(), now or now_utc()
    with state_store(cfg["state_path"]) as state:
        if state.get("paused") or state.get("active"):
            return {"status": "blocked", "reason": "paused_or_repair_in_progress"}
        attempts = [t for t in state["attempts"] if t > now.timestamp() - 86400]
        if budget_reached(state, now):
            return {"status": "budget_reached"}
        incident = next((r for r in candidates(state, now) if r["fingerprint"] == key), None)
        if incident is None:
            return {"status": "no_action"}
        identifier = uuid.uuid4().hex
        state["attempts"] = attempts + [now.timestamp()]
        state["incidents"][key]["last_attempt"] = now.timestamp()
        state["active"] = {"id": identifier, "fingerprint": key, "started": iso(now), "incident": incident}
        return {"status": "claimed", **state["active"]}


def finish(identifier, outcome, commit=None, cfg=None, now=None):
    cfg, now = cfg or config(), now or now_utc()
    if outcome not in ("fixed", "transient", "blocked", "failed"):
        raise MonitorError("Invalid outcome")
    if commit and not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise MonitorError("Expected a full commit SHA")
    if outcome == "fixed" and not commit:
        raise MonitorError("A verified deployed commit is required")
    if outcome == "fixed" and deployment(commit, cfg).get("status") != "healthy":
        raise MonitorError("The exact repair commit is not deployed and healthy; reservation retained")
    with state_store(cfg["state_path"]) as state:
        active = state.get("active")
        if not active or active["id"] != identifier:
            raise MonitorError("Repair reservation does not match")
        record = state["incidents"][active["fingerprint"]]
        record["suppress_until"] = now.timestamp() + (86400 if outcome in ("blocked", "failed") else COOLDOWN_SECONDS)
        # Pre-fix observations must not trigger another repair after cooldown.
        record["occurrences"] = []
        state["last_result"] = {**active, "outcome": outcome, "commit": commit, "finished": iso(now)}
        state["active"] = None
        if outcome == "failed":
            state["paused"] = True
        return {"status": "recorded", "outcome": outcome, "paused": state.get("paused", False)}


def deployment(commit, cfg=None):
    cfg = cfg or config()
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise MonitorError("Expected a full commit SHA")
    if not cfg["api_key"]:
        return {"status": "configuration_required", "missing": ["RENDER_API_KEY"]}
    with state_store(cfg["state_path"]) as state:
        service = resolve_service(cfg, state)
    rows = api_get(cfg, f"/services/{service['id']}/deploys", {"limit": 20})
    if not isinstance(rows, list):
        raise MonitorError("Unexpected Render deployment list")
    deploys = [r["deploy"] for r in rows]
    matches = [r for r in deploys if r.get("commit", {}).get("id") == commit]
    if not matches:
        return {"status": "pending", "reason": "commit_not_deployed"}
    selected = matches[0]
    status = selected.get("status")
    if status in DEPLOY_FAILED:
        return {"status": "failed", "deploy_id": selected["id"], "deploy_status": status}
    if status != "live":
        return {"status": "pending", "deploy_status": status}
    current = next((r for r in deploys if r.get("status") == "live"), None)
    if current is None or current.get("id") != selected.get("id"):
        return {"status": "superseded", "reason": "another_commit_is_live"}
    for path in ("/", "/api/vacations"):
        try:
            response = requests.get(SERVICE_URL + path, timeout=(5, 20), allow_redirects=False)
            if response.status_code != 200:
                return {"status": "unhealthy", "path": path, "http_status": response.status_code}
            if path.startswith("/api/"):
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("ok") is not True or not isinstance(payload.get("vacations"), list):
                    return {"status": "unhealthy", "path": path, "reason": "invalid_payload"}
        except (requests.RequestException, ValueError):
            return {"status": "unhealthy", "path": path, "reason": "request_failed"}
    return {"status": "healthy", "commit": commit, "deploy_id": selected["id"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    commands.add_parser("status")
    commands.add_parser("claim").add_argument("fingerprint")
    done = commands.add_parser("finish")
    done.add_argument("claim_id")
    done.add_argument("outcome", choices=("fixed", "transient", "blocked", "failed"))
    done.add_argument("--commit")
    commands.add_parser("deployment").add_argument("commit")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            result = collect()
        elif args.command == "claim":
            result = claim(args.fingerprint)
        elif args.command == "finish":
            result = finish(args.claim_id, args.outcome, args.commit)
        elif args.command == "deployment":
            result = deployment(args.commit)
        else:
            cfg = config()
            with state_store(cfg["state_path"]) as state:
                result = {"status": "paused" if state.get("paused") else "ready" if cfg["api_key"] else "configuration_required",
                          "active": state.get("active"), "last_result": state.get("last_result")}
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except MonitorBusy:
        print(json.dumps({"status": "busy", "action_required": False}))
        return 0
    except (MonitorError, OSError, ValueError, KeyError, TypeError) as exc:
        # Don't dump HTTP response bodies, credentials or raw exception messages.
        print(json.dumps({"status": "collector_error", "action_required": False,
                          "error": str(exc) if isinstance(exc, MonitorError) else type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
