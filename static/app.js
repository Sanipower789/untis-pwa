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
      if (btn) {
        btn.style.display = "inline-block";
        btn.onclick = async () => { try { await deferred.prompt(); } catch {} };
      }
    });
    window.addEventListener("visibilitychange", () => {
      if (!document.hidden && isStandalone()) gate.remove();
    });
  } else {
    gate.remove();
  }
})();

/* ====== LocalStorage ====== */
const LS_COURSES = "myCourses";
const LS_NAME    = "myName";
const getCourses = () => JSON.parse(localStorage.getItem(LS_COURSES) || "[]");
const setCourses = (arr) => localStorage.setItem(LS_COURSES, JSON.stringify(arr || []));
const getName    = () => localStorage.getItem(LS_NAME) || "";
const setName    = (v) => localStorage.setItem(LS_NAME, v || "");

/* ====== Helpers ====== */
const WEEKDAYS = ["Mo","Di","Mi","Do","Fr"];
const parseHM = t => { const [h,m] = String(t).split(":").map(Number); return h*60+m; };
const fmtHM = mins => `${String(Math.floor(mins/60)).padStart(2,"0")}:${String(mins%60).padStart(2,"0")}`;
const dayIdxISO = iso => { const g=new Date(iso).getDay(); return g===0?7:g; };
const _norm = (x) => (x ?? "").toString().trim().replace(/\s+/g," ").toLowerCase();
const _key = (L) => `${L.date}|${L.start}|${_norm(L.subject_original ?? "")}`;

/* ====== Mapping tables built from lessons_mapped.json ====== */
const CourseMap = new Map(); // original subject -> pretty subject
const RoomMap   = new Map(); // original room    -> pretty room (best-effort)
const MapById   = new Map(); // lesson id        -> { subject, room }
const MapByKey  = new Map(); // date|start|origSubject -> { subject, room }
let mapsReady = false;

async function loadMaps() {
  if (mapsReady) return;
  try {
    const res = await fetch(`/lessons_mapped.json?v=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error("failed to load lessons_mapped.json");
    const arr = await res.json();

    for (const L of arr) {
      const subjPretty = (L.subject ?? "").toString().trim();
      const roomPretty = (L.room ?? "").toString().trim();
      const subjOrig   = _norm(L.subject_original ?? "");

      // 1) exact id index
      if (L.id) MapById.set(String(L.id), { subject: subjPretty, room: roomPretty });

      // 2) composite key index
      const k = _key(L);
      if (k && !MapByKey.has(k)) MapByKey.set(k, { subject: subjPretty, room: roomPretty });

      // subject original -> pretty
      if (subjOrig && subjPretty && !CourseMap.has(subjOrig)) CourseMap.set(subjOrig, subjPretty);

      // room best-effort map (may be identical already; ok)
      const roomOrigField = _norm(L.room_original ?? L.room ?? "");
      if (roomOrigField && roomPretty && !RoomMap.has(roomOrigField)) RoomMap.set(roomOrigField, roomPretty);
    }
  } catch (e) {
    console.warn("Mapping maps not loaded; proceeding with live labels only.", e);
  } finally {
    mapsReady = true;
  }
}

const mapSubject = (l) => {
  const id = String(l.id ?? "");
  if (id && MapById.has(id)) {
    const v = MapById.get(id).subject;
    if (v) return v;
  }
  const k = _key(l);
  if (k && MapByKey.has(k)) {
    const v = MapByKey.get(k).subject;
    if (v) return v;
  }
  const key = _norm(l.subject_original ?? "");
  return CourseMap.get(key) || (l.subject ?? "—");
};

const mapRoom = (l) => {
  const id = String(l.id ?? "");
  if (id && MapById.has(id)) {
    const v = MapById.get(id).room;
    if (v) return v;
  }
  const k = _key(l);
  if (k && MapByKey.has(k)) {
    const v = MapByKey.get(k).room;
    if (v) return v;
  }
  const key = _norm(l.room ?? "");
  return RoomMap.get(key) || (l.room ?? "");
};

/* ====== Course selection ====== */
function buildCourseSelection(allLessons) {
  const cs       = document.getElementById("course-selection");
  const nameInput= document.getElementById("profile-name");
  const box      = document.getElementById("courses");
  const editBtn  = document.getElementById("edit-courses");
  const saveBtn  = document.getElementById("save-courses");
  if (!cs || !nameInput || !box || !saveBtn || !editBtn) return;

  nameInput.value = getName();

  const subjects = [...new Set(allLessons.map(mapSubject).filter(Boolean))]
    .sort((a,b)=>a.localeCompare(b,"de"));

  const saved = new Set(getCourses());
  box.innerHTML = "";
  subjects.forEach(sub => {
    const id = "sub_" + sub.replace(/\W+/g,"_");
    const label = document.createElement("label");
    label.className = "chk";
    label.innerHTML =
      `<input type="checkbox" id="${id}" value="${sub}" ${saved.has(sub)?"checked":""}> <span>${sub}</span>`;
    box.appendChild(label);
  });

  saveBtn.onclick = () => {
    const selected = [...box.querySelectorAll("input:checked")]
      .map(i => i.value)
      .slice(0,12);
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

  if (getCourses().length === 0) {
    cs.style.display = "block";
    editBtn.style.display = "none";
  } else {
    cs.style.display = "none";
    editBtn.style.display = "inline-block";
  }
}

/* ====== Grid render ====== */
function buildGrid(lessons) {
  const container = document.getElementById("timetable");
  container.innerHTML = "";

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

    const title = mapSubject(l);
    const room  = mapRoom(l);

    card.innerHTML = `
      <div class="lesson-title">${title}</div>
      <div class="lesson-meta">
        ${l.teacher ? `<span>· ${l.teacher}</span>` : ""}
        ${room ? `<span>· ${room}</span>` : ""}
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
  await loadMaps();

  const res = await fetch(`/api/timetable?ts=${Date.now()}`, { cache: "no-store" });
  const data = await res.json();
  let lessons = data.lessons || [];

  const cs = document.getElementById("course-selection");
  if (cs && !cs.dataset.init){
    buildCourseSelection(lessons);
    cs.dataset.init = "1";
  }

  const selected = new Set(getCourses());
  if (selected.size > 0) {
    lessons = lessons.filter(l => selected.has(mapSubject(l)));
  }

  lessons.sort((a,b)=>{
    if (a.date!==b.date)   return a.date.localeCompare(b.date);
    if (a.start!==b.start) return a.start.localeCompare(b.start);
    return mapSubject(a).localeCompare(mapSubject(b), "de");
  });

  buildGrid(lessons);
}

document.addEventListener("DOMContentLoaded", ()=>{
  loadTimetable();
  setInterval(()=>loadTimetable(true), 5*60*1000);
  document.addEventListener("visibilitychange", ()=>{ if(!document.hidden) loadTimetable(true); });
});