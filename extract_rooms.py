# extract_rooms.py
import json, os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()  # so UNTIS_* from .env are available

# --- try to use your existing client (live fetch if needed)
from untis_client import fetch_week  # returns raw lessons (unmapped)

COURSE_MAP_FILE = "course_mapping.txt"
MAPPED_JSON     = "lessons_mapped.json"
ROOM_MAP_FILE   = "rooms_mapping.txt"
SUGGESTED_LIST  = "rooms_suggested.txt"

def monday_of_this_week(d=None):
    d = d or datetime.today().date()
    return d - timedelta(days=d.weekday())

def read_mapping_file(path):
    """Reads simple 'left = right' lines into a dict. Ignores comments/empty."""
    out = {}
    if not os.path.exists(path): return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"): continue
            if "=" in s:
                left, right = s.split("=", 1)
                out[left.strip()] = right.strip()
    return out

def apply_course_map(lessons, course_map):
    """Return lessons with 'subject' replaced via course_map (fallback to original)."""
    out = []
    for x in lessons:
        subj = x.get("subject") or x.get("subject_original") or ""
        mapped = course_map.get(subj, subj)
        y = dict(x)
        y["subject"] = mapped
        out.append(y)
    return out

def load_lessons_preferring_mapped():
    # If you already have lessons_mapped.json, use it (fast + already formatted subjects)
    if os.path.exists(MAPPED_JSON):
        with open(MAPPED_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    # else live fetch then apply course mapping so subjects are formatted
    week = monday_of_this_week()
    raw = fetch_week(week)
    course_map = read_mapping_file(COURSE_MAP_FILE)
    return apply_course_map(raw, course_map)

def main():
    lessons = load_lessons_preferring_mapped()

    # Build set of "<Lesson> · <Room>" and unique room list
    pairs = set()
    rooms = set()
    for l in lessons:
        subj = (l.get("subject") or "").strip()
        room = (l.get("room") or "").strip()
        if not room:
            continue
        pairs.add(f"{subj} · {room}" if subj else room)
        rooms.add(room)

    # 1) Write the helper list with context (lesson before room)
    with open(SUGGESTED_LIST, "w", encoding="utf-8") as f:
        for p in sorted(pairs, key=lambda s: s.lower()):
            f.write(p + "\n")
    print(f"✓ Wrote {SUGGESTED_LIST} ({len(pairs)} lines)")

    # 2) Create a starter room_mapping.txt if it doesn't exist yet
    if not os.path.exists(ROOM_MAP_FILE):
        with open(ROOM_MAP_FILE, "x", encoding="utf-8") as f:
            for r in sorted(rooms, key=lambda s: s.lower()):
                f.write(f"{r} = {r}\n")
        print(f"✓ Created {ROOM_MAP_FILE} (starter). Edit RIGHT side to your normalized room names.")
    else:
        print(f"• {ROOM_MAP_FILE} already exists — edit it to map rooms.")

if __name__ == "__main__":
    main()
