/* ===== fatal overlay ===== */
(function () {
  const show = (title, detail) => {
    const preOld = document.getElementById("fatal-overlay");
    if (preOld) preOld.remove();
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

/* --- PWA install gate --- */
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

/* --- LocalStorage (profile) --- */
const LS_COURSES = "myCourses";
const LS_NAME    = "myName";
const getCourses = () => JSON.parse(localStorage.getItem(LS_COURSES) || "[]");
const setCourses = (arr) => {
  const unique = Array.isArray(arr) ? Array.from(new Set(arr.filter(v => typeof v === "string" && v.trim()))) : [];
  localStorage.setItem(LS_COURSES, JSON.stringify(unique));
};
const getName    = () => localStorage.getItem(LS_NAME) || "";
const setName    = (v) => localStorage.setItem(LS_NAME, v || "");

/* --- Helpers --- */
const WEEKDAYS = ["Mo","Di","Mi","Do","Fr"];
const parseHM = t => { const [h,m] = String(t).split(":").map(Number); return h*60+m; };
const fmtHM   = mins => `${String(Math.floor(mins/60)).padStart(2,"0")}:${String(mins%60).padStart(2,"0")}`;
const dayIdxISO = iso => { const g=new Date(iso).getDay(); return g===0?7:g; };
const escapeHtml = (s) => String(s).replace(/[&<>"']/g, c=>({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const _norm = (x) => (x ?? "").toString().trim().replace(/\s+/g, " ").toLowerCase();

/* Strong canonical normaliser (umlauts, (), dashes, tags, spaces) */
const normKey = (s) => {
  if (!s) return "";
  s = String(s)
        .trim()
        .replaceAll("ä","a").replaceAll("ö","o").replaceAll("ü","u")
        .replaceAll("Ä","a").replaceAll("Ö","o").replaceAll("Ü","u")
        .toLowerCase()
        .replace(/\s+/g, " ")
        .replace(/\(.*?\)/g, " ")
        .replace(/\s*-\s*.*$/g, " ")
        .replace(/\b(gk|lk|ag)\b/g, " ");
  return s.replace(/\s+/g, " ").trim();
};

/* --- Mapping dicts for timetable rendering (from /api/mappings) --- */
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

/* Lookup: strong norm -> soft norm -> raw */
function lookup(map, raw) {
  const nk = normKey(raw);
  if (Object.prototype.hasOwnProperty.call(map, nk)) return map[nk];
  const sk = _norm(raw);
  if (Object.prototype.hasOwnProperty.call(map, sk)) return map[sk];
  const rk = String(raw ?? "").trim();
  if (Object.prototype.hasOwnProperty.call(map, rk)) return map[rk];
  return undefined;
}

/* Mapping helpers for rendering */
function mapRoom(lesson) {
  const live = lesson.room ?? "";
  const val = lookup(ROOM_MAP, live);
  if (val !== undefined && val !== null) return val; // empty string hides room
  return live;
}

// Subject display with mapping; fall back to original/live names when mapping empty/missing
function mapSubject(lesson) {
  const orig = lesson.subject_original ?? lesson.subject ?? "";
  const live = lesson.subject ?? "";
  const val = lookup(COURSE_MAP, orig);
  if (val !== undefined && val !== null) {
    if (val !== "") return val;
    return orig || live || "";
  }
  return live || orig || "";
}

/* ================== KLAUSUREN (Local) ================== */
const LS_KLAUS = "klausuren_v1";
const KlausurenStore = {
  load() { try { return JSON.parse(localStorage.getItem(LS_KLAUS) || "[]"); } catch { return []; } },
  save(list){ localStorage.setItem(LS_KLAUS, JSON.stringify(list)); },
  add(k)    { const l=this.load(); l.push(k); this.save(l); },
  remove(id){ this.save(this.load().filter(x=>x.id!==id)); },
  find(date, period){ return this.load().find(k=>k.date===date && k.period===period) || null; }
};
const uid = () => Math.random().toString(36).slice(2,10);

/* ============ COURSE OPTIONS from course_mapping.txt ONLY ============ */
let COURSE_OPTIONS = [];
let COURSE_LABEL_BY_KEY = new Map();
let COURSE_KEY_BY_LABEL = new Map(); // keyed by _norm(label)

function registerCourseOptions(list) {
  COURSE_OPTIONS = Array.isArray(list) ? list.slice() : [];
  COURSE_LABEL_BY_KEY = new Map();
  COURSE_KEY_BY_LABEL = new Map();
  COURSE_OPTIONS.forEach(opt => {
    const key = (opt?.key ?? "").trim();
    const label = (opt?.label ?? "").trim() || key;
    if (!key) return;
    COURSE_LABEL_BY_KEY.set(key, label);
    const normLabel = _norm(label);
    if (normLabel && !COURSE_KEY_BY_LABEL.has(normLabel)) {
      COURSE_KEY_BY_LABEL.set(normLabel, key);
    }
  });
}

async function loadCourseOptionsFromTxt() {
  const tryUrls = ["/static/course_mapping.txt", "/course_mapping.txt"];
  const byKey = new Map();
  for (const url of tryUrls) {
    try {
      const res = await fetch(`${url}?v=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) continue;
      const txt = await res.text();
      txt.split(/\r?\n/).forEach(line => {
        const s = line.trim();
        if (!s || s.startsWith("#")) return;
        const i = s.indexOf("=");
        if (i === -1) return;
        const lhs = s.slice(0, i).trim();
        const rhs = s.slice(i + 1).trim();
        if (!lhs) return;
        const key = normKey(lhs);
        if (!key) return;
        const label = rhs || lhs;
        byKey.set(key, label);
      });
      break;
    } catch (_) { /* try next */ }
  }
  return Array.from(byKey.entries())
    .map(([key, label]) => ({ key, label }))
    .sort((a, b) => a.label.localeCompare(b.label, 'de'));
}

async function loadCourseOptions() {
  if (COURSE_OPTIONS.length) return COURSE_OPTIONS;
  try {
    const res = await fetch(`/api/courses?v=${Date.now()}`, { cache: 'no-store' });
    if (res.ok) {
      const j = await res.json();
      if (j && Array.isArray(j.courses) && j.courses.length) {
        const opts = j.courses
          .map(c => ({ key: String(c.key || "").trim(), label: String(c.label || "").trim() || String(c.key || "") }))
          .filter(opt => opt.key);
        if (opts.length) {
          opts.sort((a,b)=>a.label.localeCompare(b.label,'de'));
          registerCourseOptions(opts);
          return COURSE_OPTIONS;
        }
      }
    }
  } catch (_) { /* ignore, fallback below */ }

  const fallback = await loadCourseOptionsFromTxt();
  registerCourseOptions(fallback);
  return COURSE_OPTIONS;
}

function resolveCourseKey(value) {
  if (!value) return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (COURSE_LABEL_BY_KEY.has(trimmed)) return trimmed;
  const normLabel = _norm(trimmed);
  if (normLabel && COURSE_KEY_BY_LABEL.has(normLabel)) return COURSE_KEY_BY_LABEL.get(normLabel);
  const nk = normKey(trimmed);
  if (nk && COURSE_LABEL_BY_KEY.has(nk)) return nk;
  return null;
}

function normaliseCourseSelection(values) {
  const out = [];
  const seen = new Set();
  let changed = false;
  values.forEach(v => {
    const key = resolveCourseKey(typeof v === "string" ? v : "");
    if (!key) { changed = true; return; }
    if (seen.has(key)) {
      if (key === v) changed = true;
      return;
    }
    seen.add(key);
    out.push(key);
    if (key !== v) changed = true;
  });
  return { keys: out, changed };
}

/* ===== Sidebar (Klausuren) ===== */
const elSidebar      = document.getElementById('sidebar');
const btnSidebar     = document.getElementById('btnSidebar');
const btnSidebarClose= document.getElementById('sidebarClose');
const navKlausuren   = document.getElementById('navKlausuren');
const panelKlausuren = document.getElementById('panelKlausuren');
const formKlausur    = document.getElementById('klausurForm');
const selSubject     = document.getElementById('klausurSubject');
const inpName        = document.getElementById('klausurName');
const inpDate        = document.getElementById('klausurDate');
const selPeriod      = document.getElementById('klausurPeriod');
const btnKlausurReset= document.getElementById('klausurReset');
const listKlausuren  = document.getElementById('klausurList');

function sidebarShow(){ elSidebar.classList.add('show'); }
function sidebarHide(){ elSidebar.classList.remove('show'); }
btnSidebar?.addEventListener('click', sidebarShow);
btnSidebarClose?.addEventListener('click', sidebarHide);

navKlausuren?.addEventListener('click', () => {
  document.querySelectorAll('.sidebar-link').forEach(b=>b.classList.remove('active'));
  navKlausuren.classList.add('active');
  document.querySelectorAll('.sidebar-panel').forEach(p=>p.style.display='none');
  panelKlausuren.style.display='block';
  populateKlausurSubjects(window.__latestLessons || []);
  populateKlausurPeriods(window.__latestLessons || []);
  renderKlausurList();
});

btnKlausurReset?.addEventListener('click', () => { formKlausur.reset(); });

formKlausur?.addEventListener('submit', (e) => {
  e.preventDefault();
  const item = {
    id: uid(),
    subject: selSubject.value,
    name: inpName.value.trim(),
    date: inpDate.value,                   // YYYY-MM-DD
    period: parseInt(selPeriod.value, 10)  // 1..N
  };
  if (!item.subject || !item.name || !item.date || !item.period) return;
  const dup = KlausurenStore.find(item.date, item.period);
  if (dup) { alert("Für dieses Datum und diese Stunde existiert bereits eine Klausur."); return; }
  KlausurenStore.add(item);
  renderKlausurList();
  if (window.__latestLessons) buildGrid(window.__latestLessons);
});

function renderKlausurList() {
  const data = KlausurenStore.load().sort((a,b)=>{
    if (a.date!==b.date) return a.date.localeCompare(b.date);
    return a.period-b.period;
  });
  listKlausuren.innerHTML = '';
  if (!data.length) {
    listKlausuren.innerHTML = '<div class="muted">Noch keine Klausuren.</div>';
    return;
  }
  data.forEach(k => {
    const el = document.createElement('div');
    el.className = 'klausur-item';
    el.innerHTML = `
      <div>
        <strong>${escapeHtml(k.name)}</strong>
        <small>${escapeHtml(k.subject)} — ${k.date}, ${k.period}. Stunde</small>
      </div>
      <button data-id="${k.id}" title="Löschen">Löschen</button>
    `;
    el.querySelector('button').addEventListener('click', () => {
      KlausurenStore.remove(k.id);
      renderKlausurList();
      if (window.__latestLessons) buildGrid(window.__latestLessons);
    });
    listKlausuren.appendChild(el);
  });
}

function populateKlausurSubjects(lessons) {
  // uses lessons currently shown (filtered by user's selection)
  const set = new Set();
  lessons.forEach(l => { const m = mapSubject(l); if (m) set.add(m); });
  const items = Array.from(set).sort((a,b)=>a.localeCompare(b,'de'));
  selSubject.innerHTML = items.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join('');
}

function populateKlausurPeriods(lessons) {
  let maxP = 0;
  lessons.forEach(l => { if (Number.isFinite(l.period)) maxP = Math.max(maxP, l.period); });
  if (!maxP || maxP > 8) maxP = 8; // cap at 8
  selPeriod.innerHTML = Array.from({length:maxP}, (_,i)=>i+1)
    .map(p => `<option value="${p}">${p}. Stunde</option>`).join('');
}

/* ================ Timetable rendering ================== */
async function buildCourseSelection(allLessons) {
  const cs       = document.getElementById("course-selection");
  const nameInput= document.getElementById("profile-name");
  const box      = document.getElementById("courses");
  const editBtn  = document.getElementById("edit-courses");
  const saveBtn  = document.getElementById("save-courses");
  if (!cs || !nameInput || !box || !saveBtn || !editBtn) return;

  nameInput.value = getName();

  const options = await loadCourseOptions();
  const rawSaved = getCourses();
  const { keys: resolved, changed } = normaliseCourseSelection(Array.isArray(rawSaved) ? rawSaved : []);
  if (changed) setCourses(resolved);
  const saved = new Set(resolved);

  box.innerHTML = "";
  options.forEach(opt => {
    const key = opt.key;
    const labelText = opt.label || opt.key;
    const id = "sub_" + key.replace(/[^a-z0-9]+/gi, "_");
    const label = document.createElement("label");
    label.className = "chk";
    label.innerHTML =
      `<input type="checkbox" id="${id}" value="${escapeHtml(key)}" ${saved.has(key)?"checked":""}> <span>${escapeHtml(labelText)}</span>`;
    box.appendChild(label);
  });

  saveBtn.onclick = () => {
    const selected = [...box.querySelectorAll("input:checked")]
      .map(i => i.value)
      .filter(Boolean);
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

function buildGrid(lessons) {
  const container = document.getElementById("timetable");
  container.innerHTML = "";

  // snapshot for sidebar dropdowns
  window.__latestLessons = lessons;

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
    container.innerHTML = `<div class="empty-week">⏳ Bald verfügbar</div>`;
    return;
  }

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
  grid.style.gridTemplateColumns = "70px repeat(5, minmax(0, 1fr))";

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

  // Time rows + slots (skip slots for empty days)
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
      if (!daysWithLessons.has(d)) continue;
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

  // Place lessons (replace with Klausur if matching)
  valid.forEach(l => {
    const day = dayIdxISO(l.date);
    const s = parseHM(l.start), e = parseHM(l.end);
    const r0 = rowIndexFor(s), r1 = rowIndexFor(e);
    const span = Math.max(1, r1 - r0);

    let periodNum = l.period;
    if (!Number.isFinite(periodNum)) {
      periodNum = r0 + 1; // first slot of the day = 1. Stunde
    }

    const klausur = KlausurenStore.find(l.date, periodNum);

    const card = document.createElement("div");
    card.className = `lesson ${klausur ? "klausur" : (l.status || "")}`.trim();
    card.style.gridColumn = String(day + 1);
    card.style.gridRow = `${r0 + 2} / span ${span}`;

    const badge =
      l.status === "entfaellt" ? "🟥 Entfällt" :
      l.status === "vertretung" ? "⚠️ Vertretung" :
      l.status === "aenderung" ? "🟦 Änderung" : "";

    const subj = mapSubject(l);
    const room = mapRoom(l);

    if (klausur) {
      card.innerHTML = `
        <div class="lesson-title">Klausur</div>
        <div class="lesson-meta">
          <span>${escapeHtml(klausur.name)}</span>
          ${klausur.subject ? `<span>· ${escapeHtml(klausur.subject)}</span>` : ""}
          <span>· ${periodNum}. Std</span>
        </div>
      `;
    } else {
      card.innerHTML = `
        <div class="lesson-title">${escapeHtml(subj)}</div>
        <div class="lesson-meta">
          ${l.teacher ? `<span>· ${escapeHtml(l.teacher)}</span>` : ""}
          ${room ? `<span>· ${escapeHtml(room)}</span>` : ""}
        </div>
        ${badge ? `<div class="badge">${badge}</div>` : ""}
        ${l.note ? `<div class="note">${escapeHtml(l.note)}</div>` : ""}
      `;
    }

    grid.appendChild(card);
  });

  // Placeholder for empty weekdays (full column)
  for (let d = 1; d <= 5; d++) {
    if (daysWithLessons.has(d)) continue;
    const placeholder = document.createElement("div");
    placeholder.className = "placeholder-day";
    placeholder.style.gridColumn = String(d + 1);
    placeholder.style.gridRow = `2 / -1`;
    placeholder.innerHTML = `
      <div class="ph-card" role="status" aria-label="Daten folgen">
        <div class="ph-ico">⏳</div>
        <div class="ph-txt">Bald verfügbar</div>
      </div>
    `;
    grid.appendChild(placeholder);
  }

  const containerEl = document.getElementById("timetable");
  containerEl.appendChild(grid);
}

/* --- Fetch + refresh --- */
async function loadTimetable(force = false) {
  try {
    await loadMappings();
    await loadCourseOptions();

    const res = await fetch(`/api/timetable?ts=${Date.now()}${force ? "&force=1":""}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`/api/timetable ${res.status}`);
    const data = await res.json();
    let lessons = Array.isArray(data.lessons) ? data.lessons : [];

    const cs = document.getElementById("course-selection");
    if (cs && !cs.dataset.init) {
      buildCourseSelection(lessons);
      cs.dataset.init = "1";
    }

    const storedValues = Array.isArray(getCourses()) ? getCourses() : [];
    const { keys: selectedKeys, changed } = normaliseCourseSelection(storedValues);
    if (changed) setCourses(selectedKeys);
    const selectedSet = new Set(selectedKeys);

    if (selectedSet.size > 0) {
      lessons = lessons.filter((l) => {
        const keyOrig = normKey(l.subject_original ?? "");
        const keyLive = normKey(l.subject ?? "");
        return selectedSet.has(keyOrig) || selectedSet.has(keyLive);
      });
    }

    lessons.sort((a, b) => {
      if (a.date !== b.date) return a.date.localeCompare(b.date);
      if (a.start !== b.start) return a.start.localeCompare(b.start);
      return mapSubject(a).localeCompare(mapSubject(b), "de");
    });

    buildGrid(lessons);
    if (elSidebar?.classList.contains('show')) {
      populateKlausurSubjects(lessons);
      populateKlausurPeriods(lessons);
    }
  } catch (err) {
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

/* --- service worker auto-update glue --- */
if ("serviceWorker" in navigator) {
  (async () => {
    try {
      const reg = await navigator.serviceWorker.register("/sw.js");
      reg.update();
      setInterval(() => reg.update(), 60 * 60 * 1000);
      if (reg.waiting) reg.waiting.postMessage({ type: "SKIP_WAITING" });
      reg.addEventListener("updatefound", () => {
        const sw = reg.installing;
        if (!sw) return;
        sw.addEventListener("statechange", () => {
          if (sw.state === "installed" && navigator.serviceWorker.controller) {
            sw.postMessage({ type: "SKIP_WAITING" });
          }
        });
      });
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

/* --- boot --- */
document.addEventListener("DOMContentLoaded", () => {
  const refreshBtn = document.getElementById("refresh-btn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => {
      refreshBtn.disabled = true;
      refreshBtn.textContent = "⏳";
      loadTimetable(true).finally(() => {
        refreshBtn.disabled = false;
        refreshBtn.textContent = "🔄";
      });
    });
  }
  // close sidebar when tapping outside (optional)
  document.addEventListener('click', (e)=>{
    if (!elSidebar.classList.contains('show')) return;
    const within = elSidebar.contains(e.target) || (btnSidebar && btnSidebar.contains(e.target));
    if (!within) sidebarHide();
  });

  loadTimetable(); // autostart
  setInterval(()=>loadTimetable(true), 5*60*1000);
  document.addEventListener("visibilitychange", ()=>{ if(!document.hidden) loadTimetable(true); });
});
