# extract_courses.py
import json
from untis_client import fetch_week  # deine Funktion zum Laden des Plans
from datetime import date, timedelta

def main():
    # Zeitraum -> eine Woche reicht, sonst kannst du erweitern
    start = date.today()
    end = start + timedelta(days=14)

    lessons = fetch_week(start)

    # Alle einzigartigen Kursnamen sammeln
    courses = sorted(set(l["subject"] for l in lessons if l["subject"]))

    # Datei schreiben mit "Kursname = "
    with open("course_mapping.txt", "w", encoding="utf-8") as f:
        for c in courses:
            f.write(f"{c} = \n")

    print("Alle Kurse in course_mapping.txt gespeichert!")

if __name__ == "__main__":
    main()
