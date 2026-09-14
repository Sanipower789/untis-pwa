"""Personal homework validation and timetable-based deadlines (no I/O at import)."""

from datetime import date, datetime, timedelta
import re

DEFAULT_SETTINGS = {
    "markersEnabled": True, "markerColor": "#14b8a6", "markerStyle": "badge",
    "reminders": False, "reminderDays": 1, "reminderTime": "18:00",
}
MAX_TASKS = 100


def settings(value):
    source = value if isinstance(value, dict) else {}
    result = dict(DEFAULT_SETTINGS)
    for key in ("markersEnabled", "reminders"):
        if isinstance(source.get(key), bool):
            result[key] = source[key]
    if re.fullmatch(r"#[0-9a-fA-F]{6}", str(source.get("markerColor", ""))):
        result["markerColor"] = source["markerColor"].lower()
    if source.get("markerStyle") in ("badge", "dot", "outline"):
        result["markerStyle"] = source["markerStyle"]
    try:
        result["reminderDays"] = max(0, min(7, int(source.get("reminderDays", 1))))
    except (TypeError, ValueError, OverflowError):
        pass
    if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(source.get("reminderTime", ""))):
        result["reminderTime"] = source["reminderTime"]
    return result


def tasks(value):
    if not isinstance(value, list):
        return []
    result, ids = [], set()
    for raw in value[:MAX_TASKS]:
        if not isinstance(raw, dict):
            continue
        identifier, course = str(raw.get("id", "")), str(raw.get("course", ""))
        if identifier in ids or not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", identifier):
            continue
        if not re.fullmatch(r"(?:EF|Q1|Q2):[^\r\n]{1,150}", course):
            continue
        text = str(raw.get("text", "")).strip()[:2000]
        if not text or raw.get("mode") not in ("next", "date"):
            continue
        try:
            anchor = datetime.fromisoformat(raw["anchor"])
            if anchor.tzinfo is None:
                continue
            due_date = date.fromisoformat(raw["date"]).isoformat() if raw["mode"] == "date" else ""
            if due_date and not 2000 <= date.fromisoformat(due_date).year <= 2100:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        ids.add(identifier)
        result.append({"id": identifier, "course": course, "text": text,
                       "mode": raw["mode"], "date": due_date, "anchor": anchor.isoformat(),
                       "done": raw.get("done") is True, "remind": raw.get("remind") is not False,
                       "due": raw.get("due") if isinstance(raw.get("due"), dict) else None,
                       "resolution": raw.get("resolution", "pending")})
    return result


def lesson_due(lesson):
    return {key: str(lesson.get(key) or "") for key in ("id", "date", "start", "end", "grade")}


def resolve(task, week_loader, matches, tz):
    """Keep an overdue deadline; only a confirmed cancellation advances it.

    The fixed anchor excludes the lesson in progress when the task was assigned.
    Failed or missing timetable data must not silently move a known deadline.
    """
    if task["done"]:
        return task
    if task["mode"] == "date":
        return {**task, "due": {"date": task["date"], "start": "", "end": "", "id": "", "grade": task["course"].split(":")[0]}, "resolution": "resolved"}
    anchor = datetime.fromisoformat(task["anchor"]).astimezone(tz)
    saved = task.get("due")
    try:
        lower = datetime.fromisoformat(saved["date"] + "T" + saved["start"]).replace(tzinfo=tz) if saved else anchor
        week = lower.date() - timedelta(days=lower.weekday())
        for offset in range(8):
            start = week + timedelta(days=7 * offset)
            lessons = [item for item in week_loader(start) if matches(item)
                       and start.isoformat() <= str(item.get("date", "")) <= (start + timedelta(days=6)).isoformat()]
            if saved and offset == 0:
                previous = [item for item in lessons if
                            (saved.get("id") and str(item.get("id")) == saved["id"])
                            or (item.get("date"), item.get("start")) == (saved["date"], saved["start"])]
                if not previous:
                    return {**task, "resolution": "unavailable"}
                if not any(item.get("status") == "entfaellt" for item in previous):
                    return {**task, "due": lesson_due(previous[0]), "resolution": "resolved"}
            cancelled = {(item.get("date"), item.get("start")) for item in lessons if item.get("status") == "entfaellt"}
            for item in sorted(lessons, key=lambda item: (item.get("date", ""), item.get("start", ""))):
                if (item.get("date"), item.get("start")) in cancelled:
                    continue
                when = datetime.fromisoformat(item["date"] + "T" + item["start"]).replace(tzinfo=tz)
                if when > anchor and (not saved or when >= lower):
                    return {**task, "due": lesson_due(item), "resolution": "resolved"}
        return {**task, "resolution": "pending"}
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return {**task, "resolution": "unavailable"}


def reminder_at(task, preferences, tz):
    if task["done"] or not task["remind"] or task.get("resolution") != "resolved" or not task.get("due"):
        return None
    try:
        due = task["due"]
        deadline = datetime.fromisoformat(due["date"] + "T" + (due.get("start") or "23:59")).replace(tzinfo=tz)
        day = deadline.date() - timedelta(days=preferences["reminderDays"])
        requested = datetime.fromisoformat(day.isoformat() + "T" + preferences["reminderTime"]).replace(tzinfo=tz)
    except (ValueError, KeyError, TypeError, OverflowError):
        return None
    # A same-day reminder configured after class is delivered just before class.
    return min(requested, deadline - timedelta(minutes=15)), deadline
