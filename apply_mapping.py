# apply_mapping.py
import os, json
from datetime import date, timedelta
from dotenv import load_dotenv

# --- .env laden ---
load_dotenv()

from untis_client import fetch_week  # holt die Live-Daten (UNMAPPed)

# course_mapping.txt einlesen
def load_mapping(path="course_mapping.txt"):
    mapping = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):  # Kommentare überspringen
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                mapping[k.strip()] = v.strip()
    return mapping

def apply_mapping(lessons, mapping):
    for l in lessons:
        orig = l.get("subject", "")
        if orig in mapping:
            l["subject"] = mapping[orig]
    return lessons

def main():
    # aktuelle Woche laden
    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # Montag
    lessons = fetch_week(week_start)

    # Mapping laden
    mapping = load_mapping("course_mapping.txt")
    lessons_mapped = apply_mapping(lessons, mapping)

    # Datei speichern
    with open("lessons_mapped.json", "w", encoding="utf-8") as f:
        json.dump(lessons_mapped, f, ensure_ascii=False, indent=2)

    print("✅ lessons_mapped.json wurde aktualisiert.")

if __name__ == "__main__":
    main()
