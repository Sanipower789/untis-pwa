import argparse, json, os
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
EXPORT_DIR = DATA_DIR / "exports"
DATA_DIR.mkdir(exist_ok=True)
EXPORT_DIR.mkdir(exist_ok=True)

load_dotenv(dotenv_path=BASE_DIR / ".env")

from untis_client import fetch_week, fetch_week_all, available_grades

LEGACY_COURSE_MAP_FILE = BASE_DIR / "course_mapping.txt"
LEGACY_ROOM_MAP_FILE   = BASE_DIR / "rooms_mapping.txt"
COURSE_MAP_FILE = DATA_DIR / "course_mapping.txt"
ROOM_MAP_FILE   = DATA_DIR / "rooms_mapping.txt"
OUT_JSON_BASE   = EXPORT_DIR / "lessons_mapped"


def monday_of_this_week(d=None):
    d = d or datetime.today().date()
    return d - timedelta(days=d.weekday())


def read_mapping_file(path: Path, legacy: Path | None = None):
    out = {}
    if not path.exists() and legacy and legacy.exists():
        try:
            path.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "=" in s:
                left, right = s.split("=", 1)
                out[left.strip()] = right.strip()
    return out


def _write_list(path: Path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for v in sorted(values, key=lambda x: x.lower()):
            f.write(v + "\n")


def _collect_from_lessons(lessons):
    subjects = set()
    rooms = set()
    for l in lessons:
        subj = (l.get("subject_original") or l.get("subject") or "").strip()
        room_raw = (l.get("room") or "").strip()
        if subj:
            subjects.add(subj)
        if room_raw:
            parts = []
            for chunk in room_raw.replace(";", ",").split(","):
                c = chunk.strip()
                if c:
                    parts.append(c)
            if parts:
                rooms.update(parts)
            else:
                rooms.add(room_raw)
    return subjects, rooms


def map_lessons(lessons, course_map, room_map):
    mapped = []
    for l in lessons:
        subj_orig = l.get("subject") or l.get("subject_original") or ""
        room_orig = l.get("room") or ""

        subj_new = course_map.get(subj_orig, subj_orig)
        room_new = room_map.get(room_orig, room_orig)

        x = dict(l)
        x["subject_original"] = subj_orig
        x["subject"] = subj_new
        x["room"] = room_new
        mapped.append(x)

    mapped.sort(key=lambda x: (x.get("date", ""), x.get("start", ""), x.get("subject", "")))
    return mapped


def main():
    parser = argparse.ArgumentParser(description="Apply course/room mappings to Untis timetable data.")
    parser.add_argument("--grade", action="append", help="Grade to fetch (e.g. EF or Q1). Default: all configured.")
    parser.add_argument("--week-start", help="ISO date for Monday (YYYY-MM-DD). Default: current week.")
    args = parser.parse_args()

    week = monday_of_this_week()
    if args.week_start:
        try:
            week = datetime.strptime(args.week_start, "%Y-%m-%d").date()
        except ValueError:
            raise SystemExit("week-start must be YYYY-MM-DD")

    configured = set(available_grades())
    grades = [g.strip().upper() for g in args.grade] if args.grade else sorted(configured)
    if not grades:
        raise SystemExit("No grades configured. Check UNTIS env vars.")
    unknown = [g for g in grades if g not in configured]
    if unknown:
        raise SystemExit(f"Unknown grade(s): {', '.join(unknown)}. Available: {', '.join(sorted(configured))}")

    # Load mappings once
    course_map = read_mapping_file(COURSE_MAP_FILE, LEGACY_COURSE_MAP_FILE)
    room_map   = read_mapping_file(ROOM_MAP_FILE, LEGACY_ROOM_MAP_FILE)

    summary = []
    subjects_all = set()
    rooms_all = set()

    for grade in grades:
        try:
            lessons = fetch_week(week, grade)
        except Exception as exc:
            print(f"[WARN] {grade}: fetch failed ({exc})")
            continue
        mapped = map_lessons(lessons, course_map, room_map)
        fname = Path(f"{OUT_JSON_BASE}_{grade.lower()}.json")
        fname.parent.mkdir(parents=True, exist_ok=True)
        with fname.open("w", encoding="utf-8") as f:
            json.dump(mapped, f, ensure_ascii=False, indent=2)

        subs, rms = _collect_from_lessons(lessons)
        subjects_all.update(subs)
        rooms_all.update(rms)

        _write_list(DATA_DIR / f"subjects_raw_{grade.lower()}.txt", subs)
        _write_list(DATA_DIR / f"rooms_raw_{grade.lower()}.txt", rms)

        summary.append((grade, fname, len(mapped)))

        # backwards compatibility for single-grade run
        if len(grades) == 1:
            legacy_fname = Path(f"{OUT_JSON_BASE}.json")
            with legacy_fname.open("w", encoding="utf-8") as f:
                json.dump(mapped, f, ensure_ascii=False, indent=2)

    print("Mapping complete:")
    for grade, fname, count in summary:
        print(f"  {grade}: wrote {fname} ({count} lessons)")
        print(f"       data/subjects_raw_{grade.lower()}.txt / data/rooms_raw_{grade.lower()}.txt")
    if subjects_all:
        _write_list(DATA_DIR / "subjects_raw_all.txt", subjects_all)
    if rooms_all:
        _write_list(DATA_DIR / "rooms_raw_all.txt", rooms_all)


if __name__ == "__main__":
    main()
