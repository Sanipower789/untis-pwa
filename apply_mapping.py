# apply_mapping.py
import json, os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

from untis_client import fetch_week  # live raw lessons

COURSE_MAP_FILE = "course_mapping.txt"
ROOM_MAP_FILE   = "rooms_mapping.txt"
OUT_JSON        = "lessons_mapped.json"

def monday_of_this_week(d=None):
    d = d or datetime.today().date()
    return d - timedelta(days=d.weekday())

def read_mapping_file(path):
    out = {}
    if not os.path.exists(path): 
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"): 
                continue
            if "=" in s:
                left, right = s.split("=", 1)
                out[left.strip()] = right.strip()
    return out

def main():
    week = monday_of_this_week()
    lessons = fetch_week(week)  # raw (as provided by untis_client)

    # Load mappings
    course_map = read_mapping_file(COURSE_MAP_FILE)
    room_map   = read_mapping_file(ROOM_MAP_FILE)

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

    # (optional) sort by date/time for nicer diffs
    mapped.sort(key=lambda x: (x.get("date",""), x.get("start",""), x.get("subject","")))

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(mapped, f, ensure_ascii=False, indent=2)

    print(f"✓ Wrote {OUT_JSON} with {len(mapped)} lessons")
    print("   (Subject & Room mapping applied)")

if __name__ == "__main__":
    main()
