# extract_courses.py
from datetime import date
from pathlib import Path
from dotenv import load_dotenv
from untis_client import fetch_week, available_grades

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

SUBJECTS_ALL_PATH = DATA_DIR / "subjects_raw_all.txt"
SUBJECTS_GRADE_PATH = {
    "EF": DATA_DIR / "subjects_raw_ef.txt",
    "Q1": DATA_DIR / "subjects_raw_q1.txt",
}

load_dotenv(dotenv_path=BASE_DIR / ".env")


def main():
    start = date.today()
    grades = available_grades() or ["EF"]

    all_subjects = set()
    per_grade_subjects: dict[str, set[str]] = {g: set() for g in grades}

    for grade in grades:
        lessons = fetch_week(start, grade)
        subs = per_grade_subjects.setdefault(grade, set())
        for l in lessons:
            subj = (l.get("subject_original") or l.get("subject") or "").strip()
            if not subj:
                continue
            subs.add(subj)
            all_subjects.add(subj)

    # write per-grade subject lists
    for grade, subs in per_grade_subjects.items():
        path = SUBJECTS_GRADE_PATH.get(grade) or DATA_DIR / f"subjects_raw_{grade.lower()}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for s in sorted(subs, key=lambda x: x.lower()):
                f.write(s + "\n")
        print(f"Wrote {path} ({len(subs)} subjects)")

    # combined list
    with SUBJECTS_ALL_PATH.open("w", encoding="utf-8") as f:
        for s in sorted(all_subjects, key=lambda x: x.lower()):
            f.write(s + "\n")
    print(f"Wrote {SUBJECTS_ALL_PATH} ({len(all_subjects)} subjects)")


if __name__ == "__main__":
    main()
