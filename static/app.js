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
const LS_COURSES = "myCourses";      // Array<String> (raw Untis names)
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

// === MAPPING (added) =======================================
// Global caches (filled on first use)
let _courseMap = null;   // from course_mapping.txt AND/OR lessons_mapped.json
let _roomMap   = null;   // from rooms_mapped.json AND/OR room_mapping.txt

function _parseTxtMapping(txt) {
  // lines like:  original = Pretty Name
  const map = {};
  txt.split(/\r?\n/).forEach(line => {
    const m = line.match(/^\s*(.*?)\s*=\s*(.*?)\s*$/);
    if (m && m[1]) map[m[1]] = m[2] ?? "";
  });
  return map;
}

async function _fetchJSONOrNull(url) {
  try {
    const r = await fetch(url + "?v=" + Date.now());
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

async function _fetchTextOrNull(url) {
  try {
    const r = await fetch(url + "?v=" + Date.now());
    if (!r.ok) return null;
    return await r.text();
  } catch { return null; }
}

function _mergeMaps(base, extra) {
  if (!base) base = {};
  if (!extra) return base;
  for (const [k,v] of Object.entries(extra)) {
    if (v != null && v !== "") base[k] = v;
  }
  return base;
}

// Load once, from endpoints you exposed in app.py (root files)
async function ensureMappingsLoaded() {
  if (_courseMap !== null && _roomMap !== null) return;

  // Start with empty
  _courseMap = {};
  _roomMap   = {};

  // 1) JSON lessons map (optional)
  const lessonsJson = await _fetchJSONOrNull("/lessons_mapped.json");
  if (lessonsJson && typeof lessonsJson === "object") {
    // supports either {"DG1":"Deutsch GK 1", ...} or {"course":{"DG1":"..."}, "room":{"A-K01":"..."}} etc.
    if (lessonsJson.course) _courseMap = _mergeMaps(_courseMap, lessonsJson.course);
    else _courseMap = _mergeMaps(_courseMap, lessonsJson);

    if (lessonsJson.room)   _roomMap   = _mergeMaps(_roomMap, lessonsJson.room);
  }

  // 2) TXT course mapping (optional)
  const courseTxt = await _fetchTextOrNull("/course_mapping.txt");
  if (courseTxt) _courseMap = _mergeMaps(_courseMap, _parseTxtMapping(courseTxt));

  // 3) JSON rooms map (optional)
  const roomsJson = await _fetchJSONOrNull("/rooms_mapped.json");
  if (roomsJson) _roomMap = _mergeMaps(_roomMap, roomsJson);

  // 4) TXT room mapping (optional)
  const roomTxt = await _fetchTextOrNull("/room_mapping.txt");
  if (roomTxt) _roomMap = _mergeMaps(_roomMap, _parseTxtMapping(roomTxt));
}

function applyMappings(lessons) {
  if (!_courseMap && !_roomMap) return lessons;

  return lessons.map(l => {
    const copy = { ...l };
    if (copy.subject && _courseMap && _courseMap[copy.subject]) {
      copy.subject = _courseMap[copy.subject];
    }
    if (copy.room && _roomMap && _roomMap[copy.room]) {
      copy.room = _roomMap[copy.room];
    }
    return copy;
  });
}
// ============================================================

/* ====== Mapping (from repo ROOT, not /static) ====== */
let COURSE_MAP = {};  // { rawCourse -> niceName }
let ROOM_MAP   = {};  // { rawRoom   -> niceName }

async function loadJson(url) {
  const res = await fetch(url + `?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error("not ok");
  return res.json();
}
async function loadTxtMap(url) {
  const res = await fetch(url + `?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error("not ok");
  const txt = await res.text();
  const map = {};
  txt.split(/\r?\n/).forEach(line => {
    const m = line.match(/^\s*(.+?)\s*=\s*(.+?)\s*$/);
    if (m) map[m[1]] = m[2];
  });
  return map;
}
async function loadMappings() {
  // Courses: prefer JSON, else TXT (from root)
  COURSE_MAP = {};
  try { COURSE_MAP = await loadJson("/lessons_mapped.json"); }
  catch { try { COURSE_MAP = await loadTxtMap("/course_mapping.txt"); } catch {} }

  // Rooms: prefer JSON, else TXT (from root)
  ROOM_MAP = {};
  try { ROOM_MAP = await loadJson("/rooms_mapped.json"); }
  catch { try { ROOM_MAP = await loadTxtMap("/room_mapping.txt"); } catch {} }
}
const niceCourse = (raw) => COURSE_MAP[raw] ?? raw;
const niceRoom   = (raw) => ROOM_MAP[raw] ?? raw;

// direkt NACH der Definition von loadMappings()
window.addEventListener("error", (e)=>console.log("JS error:", e.message));
window.addEventListener("unhandledrejection", (e)=>console.log("Promise rejection:", e.reason));

/* ====== Kurs-Auswahl ====== */
function buildCourseSelection(allLessons) {
  const cs = document.getElementById("course-selection");
  const nameInput = document.getElementById("profile-name");
  const box = document.getElementById("courses");
  const editBtn = document.getElementById("edit-courses");
  const saveBtn = document.getElementById("save-courses");
  if (!cs || !nameInput || !box) return;

  // name preset
  nameInput.value = getName();

  // Liste einmal aufbauen
  box.innerHTML = "";
  const subjects = [...new Set(allLessons.map(l => l.subject).filter(Boolean))]
    .sort((a,b)=>a.localeCompare(b,"de"));
  const saved = new Set(getCourses());

  subjects.forEach(sub => {
    const id = "sub_" + sub.replace(/\W+/g,"_");
    const label = document.createElement("label");
    label.className = "chk";
    // value bleibt RAW, Anzeige ist gemappt:
    label.innerHTML = `<input type="checkbox" id="${id}" value="${sub}" ${saved.has(sub)?"checked":""}> <span>${niceCourse(sub)}</span>`;
    box.appendChild(label);
  });

  saveBtn.onclick = () => {
    const selected = [...box.querySelectorAll("input:checked")].map(i => i.value).slice(0,12); // RAW Namen
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

  // beim ersten Start: Auswahl zeigen, sonst verstecken
  if (getCourses().length === 0) {
    cs.style.display = "block";
    editBtn.style.display = "none";
  } else {
    cs.style.display = "none";
    editBtn.style.display = "inline-block";
  }
}

// ===== Grid render (Untis-style) =====
function buildGrid(lessons) {
  const container = document.getElementById("timetable");
  container.innerHTML = "";

  // Collect times & keep only Mon–Fri
  const tset = new Set();
  const valid = [];
  lessons.forEach(l => {
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

  // Place lessons
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

    // Use mapped display values (but keep RAW for logic elsewhere)
    const displaySubject = niceCourse(l.subject || "—");
    const displayRoom    = l.room ? niceRoom(l.room) : "";

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
  // ensure mappings are present before first paint
  if (!force && (Object.keys(COURSE_MAP).length === 0 && Object.keys(ROOM_MAP).length === 0)) {
    try { await loadMappings(); } catch {}
  }

  const res = await fetch(`/api/timetable?ts=${Date.now()}`, { cache: "no-store" });
  const data = await res.json();
  let lessons = data.lessons || [];
  await ensureMappingsLoaded();           // (added) load mapping files once
  lessons = applyMappings(lessons);       // (added) rewrite subject/room

  // Kursauswahl initialisieren
  const cs = document.getElementById("course-selection");
  if (cs && !cs.dataset.init){
    buildCourseSelection(lessons);
    cs.dataset.init = "1";
  }

  // Filtern (selected are RAW names)
  const selected = getCourses();
  if (selected.length) {
    lessons = lessons.filter(l => selected.includes(l.subject));
  }

  lessons.sort((a,b)=>{
    if (a.date!==b.date) return a.date.localeCompare(b.date);
    if (a.start!==b.start) return a.start.localeCompare(b.start);
    return (a.subject||"").localeCompare(b.subject||"");
  });

  buildGrid(lessons);
}

// init
document.addEventListener("DOMContentLoaded", async ()=>{
  try { await loadMappings(); } catch {}
  loadTimetable();
  // alle 5 Minuten refresh
  setInterval(()=>loadTimetable(true), 5*60*1000);
  // bei Rückkehr in den Tab sofort aktualisieren
  document.addEventListener("visibilitychange", ()=>{ if(!document.hidden) loadTimetable(true); });