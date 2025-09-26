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
const LS_COURSES = "myCourses";      // store SELECTED COURSE NAMES (pretty)
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

/* ---- display helpers (prefer already-pretty fields; ignore *_original unless needed) */
const subjText = (l) =>
  l.subject ?? l.subject_display ?? l.subject_pretty ?? l.subject_original ?? "—";

const roomText = (l) =>
  l.room ?? l.room_display ?? l.room_pretty ?? l.room_name ?? l.room_original ?? "";

/* ====== Kurs-Auswahl ====== */
function buildCourseSelection(allLessons) {
  const cs       = document.getElementById("course-selection");
  const nameInput= document.getElementById("profile-name");
  const box      = document.getElementById("courses");
  const editBtn  = document.getElementById("edit-courses");
  const saveBtn  = document.getElementById("save-courses");
  if (!cs || !nameInput || !box || !saveBtn || !editBtn) return;

  // Name preset
  nameInput.value = getName();

  // Build unique subject list from PRETTY names
  const subjects = [...new Set(allLessons.map(subjText).filter(Boolean))]
    .sort((a,b)=>a.localeCompare(b,"de"));

  const saved = new Set(getCourses()); // saved pretty names (new scheme)

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
      .slice(0,12); // cap at 12
    setCourses(selected);                      // store PRETTY names
    setName(nameInput.value.trim());
    cs.style.display = "none";
    editBtn.style.display = "inline-block";
    loadTimetable(true);
  };

  editBtn.onclick = () => {
    cs.style.display = "block";
    editBtn.style.display = "none";
  };

  // First run -> show selection if nothing stored
  if (getCourses().length === 0) {
    cs.style.display = "block";
    editBtn.style.display = "none";
  } else {
    cs.style.display = "none";
    editBtn.style.display = "inline-block";
  }
}

/* ====== Grid render (Untis-style) ====== */
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

    const title = subjText(l);
    const room  = roomText(l);

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
  const res = await fetch(`/api/timetable?ts=${Date.now()}`, { cache: "no-store" });
  const data = await res.json();
  let lessons = data.lessons || [];

  // init course selection once (using PRETTY names)
  const cs = document.getElementById("course-selection");
  if (cs && !cs.dataset.init){
    buildCourseSelection(lessons);
    cs.dataset.init = "1";
  }

  // Filter by selected PRETTY names (backwards-friendly: also accept subject_original)
  const selected = new Set(getCourses());
  if (selected.size > 0) {
    lessons = lessons.filter(l => selected.has(subjText(l)) || selected.has(l.subject_original));
  }

  lessons.sort((a,b)=>{
    if (a.date!==b.date)  return a.date.localeCompare(b.date);
    if (a.start!==b.start) return a.start.localeCompare(b.start);
    return subjText(a).localeCompare(subjText(b), "de");
  });

  buildGrid(lessons);
}

// init
document.addEventListener("DOMContentLoaded", ()=>{
  loadTimetable();
  // alle 5 Minuten refresh
  setInterval(()=>loadTimetable(true), 5*60*1000);
  // bei Rückkehr in den Tab sofort aktualisieren
  document.addEventListener("visibilitychange", ()=>{ if(!document.hidden) loadTimetable(true); });
});