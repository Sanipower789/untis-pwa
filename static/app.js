/* ====== Pure dictionary mapping front-end ======
   - Fetch /api/mappings (rooms + courses)
   - Fetch /api/timetable (live week)
   - Map:
       subject_display = courses[subject_original] || subject
       room_display    = rooms[room] || room
   - Week-agnostic. No IDs. No snapshots needed.
================================================= */

// ---- dev safety net: show runtime errors on the page ----
(function () {
  const show = (title, detail) => {
    const pre = document.createElement("pre");
    pre.id = "fatal-overlay";
    pre.style.cssText = `
      position:fixed;left:8px;right:8px;bottom:8px;z-index:99999;
      background:#1b1c20;border:1px solid #3a3f4a;border-radius:10px;
      color:#ffb4b4;padding:10px;max-height:40vh;overflow:auto;
      font:12px/1.35 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;`;
    pre.textContent = `${title}\n${detail || ""}`;
    document.body.appendChild(pre);
  };
  window.__showFatal = show;
  window.addEventListener("error", (e) => show("JS-Fehler:", e.message + "\n" + (e.error?.stack || "")));
  window.addEventListener("unhandledrejection", (e) => show("Promise-Fehler:", String(e.reason)));
})();

/* --- PWA install gate (kept) --- */
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

/* --- LocalStorage --- */
const LS_COURSES = "myCourses";
const LS_NAME    = "myName";
const getCourses = () => JSON.parse(localStorage.getItem(LS_COURSES) || "[]");
const setCourses = (arr) => localStorage.setItem(LS_COURSES, JSON.stringify(arr || []));
const getName    = () => localStorage.getItem(LS_NAME) || "";
const setName    = (v) => localStorage.setItem(LS_NAME, v || "");

/* --- Helpers --- */
const WEEKDAYS = ["Mo","Di","Mi","Do","Fr"];
const parseHM = t => { const [h,m] = String(t).split(":").map(Number); return h*60+m; };
const fmtHM   = mins => `${String(Math.floor(mins/60)).padStart(2,"0")}:${String(mins%60).padStart(2,"0")}`;
const dayIdxISO = iso => { const g=new Date(iso).getDay(); return g===0?7:g; };
const _norm = (x) => (x ?? "").toString().trim().replace(/\s+/g, " ").toLowerCase();

/* --- Mapping dicts (filled from /api/mappings) --- */
let COURSE_MAP = {};
let ROOM_MAP   = {};
let MAPS_READY = false;

async function loadMappings() {
  if (MAPS_READY) return;
  const res = await fetch(`/api/mappings?v=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to load /api/mappings");
  const j = await res.json();
  COURSE_MAP = j.courses || {};
  ROOM_MAP   = j.rooms   || {};
  MAPS_READY = true;
}

/* --- Mapping functions (simple & deterministic) --- */
function mapSubject(lesson) {
  const orig = lesson.subject_original ?? lesson.subject ?? "";
  const key  = _norm(orig);
  // important: allow empty string mappings if you ever add them
  if (Object.prototype.hasOwnProperty.call(COURSE_MAP, key)) {
    return COURSE_MAP[key];
  }
  return lesson.subject ?? "—";
}

function mapRoom(lesson) {
  const live = lesson.room ?? "";
  const key  = _norm(live);
  // important: allow "" to pass through (don’t fallback to live)
  if (Object.prototype.hasOwnProperty.call(ROOM_MAP, key)) {
    return ROOM_MAP[key];
  }
  return live;
}

/* --- Course selection UI --- */
function buildCourseSelection(allLessons) {
  const cs       = document.getElementById("course-selection");
  const nameInput= document.getElementById("profile-name");
  const box      = document.getElementById("courses");
  const editBtn  = document.getElementById("edit-courses");
  const saveBtn  = document.getElementById("save-courses");
  if (!cs || !nameInput || !box || !saveBtn || !editBtn) return;

  nameInput.value = getName();

  // Build from mapped subject names
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

/* --- Grid render --- */
function buildGrid(lessons) {
  const container = document.getElementById("timetable");
  container.innerHTML = "";

  // Mon–Fri only + collect time boundaries
  const tset = new Set();
  const valid = [];
  for (const l of lessons) {
    const d = dayIdxISO(l.date);
    if (d >= 1 && d <= 5) {
      valid.push(l);
      tset.add(parseHM(l.start));
      tset.add(parseHM(l.end));
    }
  }

  const times = [...tset].sort((a, b) => a - b);
  if (times.length < 2) {
    container.innerHTML = `
      <div class="empty-week">⏳ Bald verfügbar</div>`;
    return;
  }

  // figure out which weekdays actually have lessons
  const daysWithLessons = new Set(valid.map(l => dayIdxISO(l.date)));

  const ROW_NORMAL = 72;
  const ROW_BREAK  = 36;
  const rowHeights = [];
  for (let i = 0; i < times.length - 1; i++) {
    const duration = times[i + 1] - times[i];
    rowHeights.push(duration <= 30 ? ROW_BREAK : ROW_NORMAL);
  }

  const grid = document.createElement("div");
  grid.className = "grid";
  const headerH = 44;
  grid.style.gridTemplateRows = [headerH + "px", ...rowHeights.map(h => h + "px")].join(" ");

  // Header row
  const corner = document.createElement("div");
  corner.className = "hdr corner";
  corner.textContent = "Zeit";
  grid.appendChild(corner);
  for (let d = 1; d <= 5; d++) {
    const h = document.createElement("div");
    h.className = "hdr day";
    h.textContent = WEEKDAYS[d - 1];
    grid.appendChild(h);
  }

  // Time rows + slots (skip slots entirely for empty days)
  for (let i = 0; i < times.length - 1; i++) {
    const startM = times[i];
    const endM   = times[i + 1];

    const timeCell = document.createElement("div");
    timeCell.className = "timecell";
    timeCell.textContent = `${fmtHM(startM)}–${fmtHM(endM)}`;
    timeCell.style.gridColumn = "1";
    timeCell.style.gridRow = String(i + 2);
    grid.appendChild(timeCell);

    for (let d = 1; d <= 5; d++) {
      if (!daysWithLessons.has(d)) continue; // <-- no cells under empty day

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

    const subj = mapSubject(l);
    const room = mapRoom(l);

    card.innerHTML = `
      <div class="lesson-title">${subj}</div>
      <div class="lesson-meta">
        ${l.teacher ? `<span>· ${l.teacher}</span>` : ""}
        ${room ? `<span>· ${room}</span>` : ""}
      </div>
      ${badge ? `<div class="badge">${badge}</div>` : ""}
      ${l.note ? `<div class="note">${l.note}</div>` : ""}
    `;
    grid.appendChild(card);
  });

  // Clean placeholder for empty weekdays (no grid underneath)
  for (let d = 1; d <= 5; d++) {
  if (daysWithLessons.has(d)) continue;
  const placeholder = document.createElement("div");
  placeholder.className = "placeholder-day";
  placeholder.style.gridColumn = String(d + 1);
  placeholder.style.gridRow = `2 / -1`;
  placeholder.innerHTML = `
    <div class="ph-card" role="status" aria-label="Daten folgen">
      <div class="ph-ico" aria-hidden="true">⏳</div>
      <div class="ph-txt">Bald verfügbar</div>
    </div>`;
  grid.appendChild(placeholder); // ✅ add it to the grid
  }
  container.appendChild(grid);
}

/* --- Fetch + refresh --- */
async function loadTimetable(force = false) {
  try {
    await loadMappings();

    const res = await fetch(`/api/timetable?ts=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`/api/timetable ${res.status}`);
    const data = await res.json();
    let lessons = Array.isArray(data.lessons) ? data.lessons : [];

    const cs = document.getElementById("course-selection");
    if (cs && !cs.dataset.init) {
      buildCourseSelection(lessons);
      cs.dataset.init = "1";
    }

    const selected = new Set(getCourses());
    if (selected.size > 0) {
      lessons = lessons.filter((l) => selected.has(mapSubject(l)));
    }

    lessons.sort((a, b) => {
      if (a.date !== b.date) return a.date.localeCompare(b.date);
      if (a.start !== b.start) return a.start.localeCompare(b.start);
      return mapSubject(a).localeCompare(mapSubject(b), "de");
    });

    buildGrid(lessons);
  } catch (err) {
    // show friendly fallback instead of blank screen
    const container = document.getElementById("timetable");
    if (container) {
      container.innerHTML = `
        <div class="empty-week">
          ⏳ Keine Daten geladen (offline oder Fehler).
        </div>`;
    }
    if (window.__showFatal) window.__showFatal("Ladefehler", String(err));
    console.error(err);
  }
}

// ---- service worker auto-update glue ----
if ("serviceWorker" in navigator) {
  (async () => {
    try {
      const reg = await navigator.serviceWorker.register("/sw.js");

      // check now and every hour
      reg.update();
      setInterval(() => reg.update(), 60 * 60 * 1000);

      // if a SW is already waiting → activate it
      if (reg.waiting) reg.waiting.postMessage({ type: "SKIP_WAITING" });

      // when a new SW is found, tell it to skip waiting
      reg.addEventListener("updatefound", () => {
        const sw = reg.installing;
        if (!sw) return;
        sw.addEventListener("statechange", () => {
          if (sw.state === "installed" && navigator.serviceWorker.controller) {
            sw.postMessage({ type: "SKIP_WAITING" });
          }
        });
      });

      // when controller changes → reload once to pick up fresh code
      let hasReloaded = false;
      navigator.serviceWorker.addEventListener("controllerchange", () => {
        if (hasReloaded) return;
        hasReloaded = true;
        window.location.reload();
      });
    } catch (e) {
      console.warn("SW registration failed:", e);
    }
  })();
}

document.addEventListener("DOMContentLoaded", ()=>{
  loadTimetable();
  setInterval(()=>loadTimetable(true), 5*60*1000);
  document.addEventListener("visibilitychange", ()=>{ if(!document.hidden) loadTimetable(true); });
});