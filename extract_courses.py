# extract_courses.py
from datetime import date
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

load_dotenv(dotenv_path=BASE_DIR / ".env")

from untis_client import fetch_schoolyear_subjects, available_grades

SUBJECTS_GRADE_PATH = {
    "EF": DATA_DIR / "subjects_raw_ef.txt",
    "Q1": DATA_DIR / "subjects_raw_q1.txt",
    "Q2": DATA_DIR / "subjects_raw_q2.txt",
}


def schoolyear_label(today=None):
    today = today or date.today()
    start_year = today.year if today.month >= 8 else today.year - 1
    return f"{start_year}/{start_year + 1}"


def write_catalog(path, subjects):
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# schoolyear: {schoolyear_label()}\n")
        for subject in sorted(subjects, key=str.casefold):
            f.write(subject + "\n")

def main():
    grades = available_grades() or ["EF"]

    per_grade_subjects: dict[str, set[str]] = {g: set() for g in grades}

    for grade in grades:
        try:
            subs = set(fetch_schoolyear_subjects(grade))
        except Exception as exc:
            print(f"[WARN] {grade}: subject catalog fetch failed ({exc})")
            continue
        per_grade_subjects[grade] = subs

    # write per-grade subject lists
    for grade, subs in per_grade_subjects.items():
        if not subs:
            continue
        path = SUBJECTS_GRADE_PATH.get(grade) or DATA_DIR / f"subjects_raw_{grade.lower()}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_catalog(path, subs)
        print(f"Wrote {path} ({len(subs)} subjects)")

if __name__ == "__main__":
    main()
