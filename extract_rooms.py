# extract_rooms.py
import json
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
from untis_client import fetch_week, available_grades

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

ROOMS_ALL_PATH = DATA_DIR / "rooms_raw_all.txt"

load_dotenv(dotenv_path=BASE_DIR / ".env")  # load Untis creds


def monday_of_this_week(d=None):
    d = d or datetime.today().date()
    return d - timedelta(days=d.weekday())


def main():
    week = monday_of_this_week()
    grades = available_grades() or ["EF"]

    rooms = set()
    pairs = set()

    for grade in grades:
        lessons = fetch_week(week, grade)
        for l in lessons:
            subj = (l.get("subject") or l.get("subject_original") or "").strip()
            room = (l.get("room") or "").strip()
            if not room:
                continue
            rooms.add(room)
            if subj:
                pairs.add(f"{subj} :: {room}")

    ROOMS_ALL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ROOMS_ALL_PATH.open("w", encoding="utf-8") as f:
        for r in sorted(rooms, key=lambda s: s.lower()):
            f.write(r + "\n")
    print(f"Wrote {ROOMS_ALL_PATH} ({len(rooms)} rooms)")

    # optional helper for manual mapping context
    suggested = DATA_DIR / "rooms_suggested.txt"
    with suggested.open("w", encoding="utf-8") as f:
        for p in sorted(pairs, key=lambda s: s.lower()):
            f.write(p + "\n")
    print(f"Wrote {suggested} ({len(pairs)} lesson-room pairs)")


if __name__ == "__main__":
    main()
