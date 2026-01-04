# extract_courses.py
from datetime import date, timedelta
from pathlib import Path
from dotenv import load_dotenv
from untis_client import fetch_week  # deine Funktion zum Laden des Plans

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
COURSE_MAP_PATH = DATA_DIR / "course_mapping.txt"
LEGACY_COURSE_MAP_PATH = BASE_DIR / "course_mapping.txt"

load_dotenv(dotenv_path=BASE_DIR / ".env")


def main():
    # Zeitraum -> eine Woche reicht, sonst kannst du erweitern
    start = date.today()
    end = start + timedelta(days=14)

    lessons = fetch_week(start)

    # Alle einzigartigen Kursnamen sammeln
    courses = sorted(set(l["subject"] for l in lessons if l["subject"]))

    # Datei schreiben mit "Kursname = "
    COURSE_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with COURSE_MAP_PATH.open("w", encoding="utf-8") as f:
        for c in courses:
            f.write(f"{c} = \n")

    print(f"Alle Kurse in {COURSE_MAP_PATH} gespeichert!")

    # optional: keep legacy copy if it existed before
    if LEGACY_COURSE_MAP_PATH.exists():
        try:
            LEGACY_COURSE_MAP_PATH.write_text(COURSE_MAP_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass


if __name__ == "__main__":
    main()
