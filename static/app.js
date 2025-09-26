/* ====== PWA: „erst installieren, dann nutzen“ ====== */
const isStandalone = () =>
  window.matchMedia?.("(display-mode: standalone)").matches ||
  window.navigator.standalone === true;

(function gateInstall() {
  const gate = document.getElementById("install-gate");
  const btn  = document.getElementById("gate-continue");
  if (!gate) return;

  if (!isStandalone()) {
    gate.style.display = "flex";
    let deferred;
    window.addEventListener("beforeinstallprompt", (e) => {
      e.preventDefault();
      deferred = e;
      btn.style.display = "inline-block";
      btn.onclick = async () => { try { await deferred.prompt(); } catch {} };
    });
    window.addEventListener("visibilitychange", () => {
      if (!document.hidden && isStandalone()) gate.remove();
    });
  } else {
    gate.remove();
  }
})();

/* ====== LocalStorage ====== */
const LS_COURSES = "myCourses";      // Array<String> RAW subject codes (e.g. "DG1")
const LS_NAME    = "myName";         // String
const getCourses = () => JSON.parse(localStorage.getItem(LS_COURSES) || "[]");
const setCourses = (arr) => localStorage.setItem(LS_COURSES, JSON.stringify(arr || []));
const getName    = () => localStorage.getItem(LS_NAME) || "";
const setName    = (v) => localStorage.setItem(LS_NAME, v || "");

/* ====== Helpers ====== */
const WEEKDAYS = ["Mo","Di","Mi","Do","Fr"];
const parseHM = t => { const [h,m] = t.split(":").map(Number); return h*60+m; };
const fmtHM = mins => `${String(Math.floor(mins/60)).padStart(2,"0")}:${String(mins%60).padStart(2,"0")}`;
const dayIdxISO = iso => { const g=new Date(iso).getDay(); return g===0?7:g; };

/* ====== Mapping (single source of truth: lessons_mapped.json) ====== */
/* We’ll try to read:
   - Object form:
       {
         "course": { "DG1": "Deutsch 1", ... },
         "room":   { "A-K15": "A-K15 (Keller 1)", ... }
       }
     or a flat object { "DG1": "Deutsch 1", "A-K15": "A-K15 ..." }

   - Array form (lessons list):
       [{ subject:"DG1", subject_original:"GK DEUTSCH 1", room:"A-K15", ...}, ...]
     -> we build maps from subject_original / room_* if present.
*/

let MAP = { course: {}, room: {} };

async function fetchJSON(url){
  const r = await fetch(url + "?v=" + Date.now(), { cache: "no-store" });
  if (!r.ok) throw new Error(url + " not ok");
  return r.json();
}
async function fetchText(url){
  const r = await fetch(url + "?v=" + Date.now(), { cache: "no-store" });
  if (!r.ok) throw new Error(url + " not ok");
  return r.text();
}
function parseTxtMap(txt){
  const m = {};
  txt.split(/\r?\n/).forEach(line=>{
    const k = line.match(/^\s*(.*?)\s*=\s*(.*?)\s*$/);
    if (k && k[1]) m[k[1]] = k[2] ?? "";
  });
  return m;
}

async function loadMaps() {
  MAP = { course: {}, room: {} };

  try {
    const j = await fetchJSON("/lessons_mapped.json");

    if (Array.isArray(j)) {
      // Build from lesson array
// Build from lesson array — use pretty names exactly as delivered
for (const L of j) {
  if (L.subject) MAP.course[L.subject] = L.subject; // ignore subject_original
  if (L.room)    MAP.room[L.room]       = L.room;    // ignore any *_original
}
    } else if (j && typeof j === "object") {
      // Object form
      if (j.course && typeof j.course === "object") MAP.course = { ...MAP.course, ...j.course };
      if (j.room   && typeof j.room   === "object") MAP.room   = { ...MAP.room,   ...j.room   };

      // flat fallback (keys mixed): treat strings with letters+digits as course-ish and A-*/B-* as rooms — best effort
      if (!j.course && !j.room) {
        for (const [k,v] of Object.entries(j)) {
          if (typeof v === "string") {
            if (/^[A-Z]+[0-9]/.test(k)) MAP.course[k] = v;
            else if (/^[A-Z]-/.test(k)) MAP.room[k] = v;
          }
        }
      }
    }
  } catch (_) {
    // ignore; we’ll try room fallbacks below
  }

  // Optional: room fallback files if lessons_mapped.json had no room map
  if (Object.keys(MAP.room).length === 0) {
    try { Object.assign(MAP.room, await fetchJSON("/rooms_mapped.json")); } catch {}
    if (Object.keys(MAP.room).length === 0) {
      try { Object.assign(MAP.room, parseTxtMap(await fetchText("/room_mapping.txt"))); } catch {}
    }
  }
}

const mapCourse = raw => (raw && MAP.course[raw]) || raw || "—";
const mapRoom   = raw => (raw && MAP.room[raw])   || raw || "";

/* ====== Kurs-Auswahl ====== */
function buildCourseSelection(allLessonsRAW) {
  const cs = document.getElementById("course-selection");
  const nameInput = document.getElementById("profile-name");
  const box = document.getElementById("courses");
  const editBtn = document.getElementById("edit-courses");
  const saveBtn = document.getElementById("save-courses");
  if (!cs || !nameInput || !box) return;

  // name preset
  nameInput.value = getName();

  // Liste einmal aufbauen (use RAW values, display mapped)
  box.innerHTML = "";
  const subjects = [...new Set(allLessonsRAW.map(l => l.subject).filter(Boolean))]
    .sort((a,b)=>mapCourse(a).localeCompare(mapCourse(b), "de"));
  const saved = new Set(getCourses());

  subjects.forEach(sub => {
    const id = "sub_" + sub.replace(/\W+/g,"_");
    const label = document.createElement("label");
    label.className = "chk";
    label.innerHTML = `<input type="checkbox" id="${id}" value="${sub}" ${saved.has(sub)?"checked":""}> <span>${mapCourse(sub)}</span>`;
    box.appendChild(label);
  });

  saveBtn.onclick = () => {
    const selected = [...box.querySelectorAll("input:checked")].map(i => i.value).slice(0,12); // store RAW
    setCourses(selected);
    setName(nameInput.value.trim());
    cs.style.display = "none";
    editBtn.style.display = "inline-block";
    loadTimetable(true);
  };

  editBtn.onclick = () => {
    cs.style.display = "block";
    editBtn.style.display = "none";
  };

  if (getCourses().length === 0) { cs.style.display = "block"; editBtn.style.display = "none"; }
  else                           { cs.style.display = "none";  editBtn.style.display = "inline-block"; }
}

/* ====== Grid render (Untis-style) ====== */
function buildGrid(lessonsRAW) {
  const container = document.getElementById("timetable");
  container.innerHTML = "";

  // Collect times & keep only Mon–Fri
  const tset = new Set();
  const valid = [];
  lessonsRAW.forEach(l => {
    const d = dayIdxISO(l.date);
    if (d >= 1 && d <= 5) {
      valid.push(l);
      tset.add(parseHM(l.start));
      tset.add(parseHM(l.end));
    }
  });
  const times = [...tset].sort((a,b)=>a-b);
  if (times.length < 2) {
    container.innerHTML = "<p class='muted'>Keine Einträge.</p>";
    return;
  }

  // Row heights: short for breaks (<=30 min)
  const ROW_NORMAL = 72;
  const ROW_BREAK  = 36;
  const rowHeights = [];
  for (let i = 0; i < times.length - 1; i++) {
    const duration = times[i+1] - times[i];
    rowHeights.push(duration <= 30 ? ROW_BREAK : ROW_NORMAL);
  }

  const grid = document.createElement("div");
  grid.className = "grid";
  const headerH = 44;
  grid.style.gridTemplateRows = [headerH + "px", ...rowHeights.map(h => h + "px")].join(" ");
  grid.style.gridTemplateColumns = "72px repeat(5, 1fr)";

  // Header row
  const corner = document.createElement("div");
  corner.className = "hdr corner";
  corner.textContent = "Zeit";
  grid.appendChild(corner);
  for (let d = 1; d <= 5; d++) {
    const h = document.createElement("div");
    h.className = "hdr day";
    h.textContent = WEEKDAYS[d-1];
    grid.appendChild(h);
  }

  // Time rows + empty slots
  for (let i = 0; i < times.length - 1; i++) {
    const startM = times[i];
    const endM   = times[i+1];

    const timeCell = document.createElement("div");
    timeCell.className = "timecell";
    timeCell.textContent = `${fmtHM(startM)}–${fmtHM(endM)}`;
    timeCell.style.gridColumn = "1";
    timeCell.style.gridRow = String(i + 2);
    grid.appendChild(timeCell);

    for (let d = 1; d <= 5; d++) {
      const slot = document.createElement("div");
      slot.className = "slot";
      slot.style.gridColumn = String(d + 1);
      slot.style.gridRow = String(i + 2);
      grid.appendChild(slot);
    }
  }

  const rowIndexFor = (mins) => {
    for (let i = 0; i < times.length; i++) if (times[i] === mins) return i;
    const idx = times.findIndex(t => t > mins);
    return Math.max(0, idx - 1);
  };

  // Place lessons (display mapped names)
  valid.forEach(l => {
    const day = dayIdxISO(l.date);
    const s = parseHM(l.start), e = parseHM(l.end);
    const r0 = rowIndexFor(s), r1 = rowIndexFor(e);
    const span = Math.max(1, r1 - r0);

    const card = document.createElement("div");
    card.className = `lesson ${l.status || ""}`.trim();
    card.style.gridColumn = String(day + 1);
    card.style.gridRow = `${r0 + 2} / span ${span}`;

    const badge =
      l.status === "entfaellt" ? "🟥 Entfällt" :
      l.status === "vertretung" ? "⚠️ Vertretung" :
      l.status === "aenderung" ? "🟦 Änderung" : "";

    const displaySubject = mapCourse(l.subject);
    const displayRoom    = mapRoom(l.room);

    card.innerHTML = `
      <div class="lesson-title">${displaySubject}</div>
      <div class="lesson-meta">
        ${l.teacher ? `<span>· ${l.teacher}</span>` : ""}
        ${displayRoom ? `<span>· ${displayRoom}</span>` : ""}
      </div>
      ${badge ? `<div class="badge">${badge}</div>` : ""}
      ${l.note ? `<div class="note">${l.note}</div>` : ""}
    `;
    grid.appendChild(card);
  });

  container.appendChild(grid);
}

/* ====== Fetch + Auto-Refresh ====== */
async function loadTimetable(force=false){
  // make sure maps are available before building list/grid
  if (!force && Object.keys(MAP.course).length === 0 && Object.keys(MAP.room).length === 0) {
    try { await loadMaps(); } catch {}
  }

  const res = await fetch(`/api/timetable?ts=${Date.now()}`, { cache: "no-store" });
  const data = await res.json();
  let lessonsRAW = data.lessons || [];

  // initialize course selection (built from RAW, displayed with mapping)
  const cs = document.getElementById("course-selection");
  if (cs && !cs.dataset.init){
    buildCourseSelection(lessonsRAW);
    cs.dataset.init = "1";
  }

  // filter by RAW selections
  const selected = getCourses();
  if (selected.length) {
    lessonsRAW = lessonsRAW.filter(l => selected.includes(l.subject));
  }

  lessonsRAW.sort((a,b)=>{
    if (a.date!==b.date) return a.date.localeCompare(b.date);
    if (a.start!==b.start) return a.start.localeCompare(b.start);
    return (mapCourse(a.subject)||"").localeCompare(mapCourse(b.subject)||"");
  });

  buildGrid(lessonsRAW);
}

// init
document.addEventListener("DOMContentLoaded", async ()=>{
  try { await loadMaps(); } catch {}
  loadTimetable();
  setInterval(()=>loadTimetable(true), 5*60*1000);
  document.addEventListener("visibilitychange", ()=>{ if(!document.hidden) loadTimetable(true); });
});