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

  scheduleProfileSync();

};

const getName    = () => localStorage.getItem(LS_NAME) || "";

const setName    = (v) => {

  localStorage.setItem(LS_NAME, v || "");

  scheduleProfileSync();

};



let scheduleProfileSync = () => {};



/* --- Helpers --- */

const WEEKDAYS = ["Mo","Di","Mi","Do","Fr"];

const parseHM = t => { const [h,m] = String(t).split(":").map(Number); return h*60+m; };

const fmtHM   = mins => `${String(Math.floor(mins/60)).padStart(2,"0")}:${String(mins%60).padStart(2,"0")}`;

const dayIdxISO = iso => { const g=new Date(iso).getDay(); return g===0?7:g; };

const escapeHtml = (s) => String(s).replace(/[&<>"']/g, c=>({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const _norm = (x) => (x ?? "").toString().trim().replace(/\s+/g, " ").toLowerCase();



const PERIOD_SCHEDULE = {

  1: { start: "07:55", end: "08:55" },

  2: { start: "09:10", end: "10:10" },

  3: { start: "10:20", end: "11:20" },

  4: { start: "11:45", end: "12:45" },

  5: { start: "12:55", end: "13:55" },

  6: { start: "13:55", end: "14:25" },

  7: { start: "14:25", end: "15:25" },

  8: { start: "15:35", end: "16:35" }

};

const PERIOD_NUMBERS = Object.keys(PERIOD_SCHEDULE).map(n => Number(n)).sort((a,b)=>a-b);

const periodStartMinutes = (p) => { const info = PERIOD_SCHEDULE[p]; return info ? parseHM(info.start) : null; };

const periodEndMinutes   = (p) => { const info = PERIOD_SCHEDULE[p]; return info ? parseHM(info.end) : null; };

const STATUS_LABELS = { entfaellt: 'Entfällt', vertretung: 'Vertretung', aenderung: 'Änderung', klausur: 'Klausur' };



const formatDate = (iso) => {

  if (!iso) return '';

  const d = new Date(`${iso}T00:00:00`);

  if (Number.isNaN(d.getTime())) return iso;

  return d.toLocaleDateString('de-DE');

};

const toISODate = (dateObj) => {

  const y = dateObj.getFullYear();

  const m = String(dateObj.getMonth() + 1).padStart(2, "0");

  const d = String(dateObj.getDate()).padStart(2, "0");

  return `${y}-${m}-${d}`;

};



const formatTimeRange = (start, end) => {

  if (!start && !end) return '';

  if (!start) return end;

  if (!end) return start;

  return `${start}  ${end}`;

};

const formatPeriodRange = (start, end) => {

  if (!Number.isFinite(start) || !PERIOD_SCHEDULE[start]) return "";

  const s = PERIOD_SCHEDULE[start];

  end = Number.isFinite(end) && PERIOD_SCHEDULE[end] ? end : start;

  const e = PERIOD_SCHEDULE[end];

  const label = start === end ? `${start}. Stunde` : `${start}. - ${end}. Stunde`;

  return `${label} (${s.start} - ${e.end} Uhr)`;

};

const VACATION_FETCH_TTL = 60 * 1000;
let VACATIONS = [];
let VACATIONS_FETCHED_AT = 0;

function normaliseVacation(v){
  if (!v || typeof v !== 'object') return null;
  const out = {
    id: Number.parseInt(v.id, 10) || null,
    title: String(v.title || '').trim(),
    start_date: String(v.start_date || '').trim(),
    end_date: String(v.end_date || '').trim() || String(v.start_date || '').trim()
  };
  if (!out.title || !out.start_date) return null;
  if (!out.end_date) out.end_date = out.start_date;
  if (out.end_date < out.start_date){
    const tmp = out.start_date; out.start_date = out.end_date; out.end_date = tmp;
  }
  return out;
}

async function loadVacations(force = false){
  const now = Date.now();
  if (!force && VACATIONS_FETCHED_AT && (now - VACATIONS_FETCHED_AT) < VACATION_FETCH_TTL){
    return VACATIONS;
  }
  try {
    const res = await fetch(`/api/vacations?ts=${now}`, { cache: 'no-store' });
    if (!res.ok) throw new Error(res.statusText || 'vacations fetch failed');
    const data = await res.json();
    if (data && Array.isArray(data.vacations)){
      VACATIONS = data.vacations.map(normaliseVacation).filter(Boolean);
      VACATIONS.sort((a, b) => {
        const dateDiff = (a.start_date || '').localeCompare(b.start_date || '');
        if (dateDiff !== 0) return dateDiff;
        return (a.title || '').localeCompare(b.title || '', 'de');
      });
      VACATIONS_FETCHED_AT = now;
    }
  } catch (err) {
    console.warn('Vacations fetch failed:', err);
  }
  return VACATIONS;
}

function vacationsOnDate(iso){
  if (!iso) return [];
  return VACATIONS.filter(v => v.start_date <= iso && iso <= v.end_date);
}



/* Strong canonical normaliser (umlauts, (), dashes, tags, spaces) */

const normKey = (s) => {

  if (!s) return "";

  s = String(s)

        .trim()

        .replaceAll("ä","a").replaceAll("ö","o").replaceAll("ü","u")

        .replaceAll("Ä","a").replaceAll("Ö","o").replaceAll("Ü","u")

        .toLowerCase()

        .replace(/\s+/g, " ")

        .replace(/[()]/g, " ")

        .replace(/[-]+/g, " ")

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

function normaliseKlausur(item) {

  if (!item || typeof item !== "object") return null;

  const clone = { ...item };

  const startRaw = Number.parseInt(clone.periodStart ?? clone.period, 10);

  const endRaw   = Number.parseInt(clone.periodEnd ?? clone.period, 10);

  let periodStart = Number.isFinite(startRaw) ? startRaw : 1;

  let periodEnd   = Number.isFinite(endRaw) ? endRaw : periodStart;

  if (periodEnd < periodStart) periodEnd = periodStart;

  delete clone.period; // legacy

  clone.periodStart = periodStart;

  clone.periodEnd = periodEnd;

  clone.name = (clone.name || "").trim();

  clone.subject = (clone.subject || "").trim();

  clone.id = clone.id || uid();

  return clone;

}



const KlausurenStore = {

  load() {

    try {

      const raw = JSON.parse(localStorage.getItem(LS_KLAUS) || "[]");

      if (!Array.isArray(raw)) return [];

      return raw.map(normaliseKlausur).filter(Boolean);

    } catch {

      return [];

    }

  },

  save(list){

    const serialisable = Array.isArray(list) ? list.map(normaliseKlausur).filter(Boolean) : [];

    localStorage.setItem(LS_KLAUS, JSON.stringify(serialisable));

    scheduleProfileSync();

  },

  add(k)    {

    const norm = normaliseKlausur(k);

    if (!norm) return;

    const arr = this.load();

    arr.push(norm);

    this.save(arr);

  },

  remove(id){ this.save(this.load().filter(x=>x.id!==id)); },

  find(date, period){

    if (!period) return null;

    return this.load().find(k=>k.date===date && period >= k.periodStart && period <= k.periodEnd) || null;

  },

  overlaps(date, start, end){

    return this.load().find(k => k.date === date && !(end < k.periodStart || start > k.periodEnd)) || null;

  }

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

const selPeriodStart = document.getElementById('klausurPeriodStart');

const selPeriodEnd   = document.getElementById('klausurPeriodEnd');

const btnKlausurReset= document.getElementById('klausurReset');

const listKlausuren  = document.getElementById('klausurList');



const overlayRoot   = document.getElementById('lesson-overlay');

const overlayTitle  = document.getElementById('lesson-overlay-title');

const overlaySubtitle = document.getElementById('lesson-overlay-subtitle');

const overlayMeta   = document.getElementById('lesson-overlay-meta');

const overlayNote   = document.getElementById('lesson-overlay-note');

const overlayClose  = document.getElementById('lesson-overlay-close');



function sidebarShow(){ elSidebar.classList.add('show'); }

function sidebarHide(){ elSidebar.classList.remove('show'); }

btnSidebar?.addEventListener('click', sidebarShow);

btnSidebarClose?.addEventListener('click', sidebarHide);



function showKlausurenPanel() {

  document.querySelectorAll('.sidebar-link').forEach(b=>b.classList.remove('active'));

  navKlausuren?.classList.add('active');

  document.querySelectorAll('.sidebar-panel').forEach(p=>p.style.display='none');

  if (panelKlausuren) panelKlausuren.style.display='block';

  const accordion = document.getElementById('klausurAccordion');

  if (accordion && !accordion.hasAttribute('open')) accordion.setAttribute('open','');

  populateKlausurSubjects(window.__latestLessons || []);

  populateKlausurPeriods();

  renderKlausurList();

}



navKlausuren?.addEventListener('click', showKlausurenPanel);

showKlausurenPanel();





function hideLessonOverlay(){

  if (!overlayRoot) return;

  overlayRoot.setAttribute('aria-hidden','true');

  document.body.style.removeProperty('overflow');

}



function showLessonOverlay(payload){

  if (!overlayRoot) return;

  const { title, subtitle, meta = [], note = '' } = payload || {};

  overlayTitle.textContent = title || '';

  overlaySubtitle.textContent = subtitle || '';

  overlaySubtitle.style.display = subtitle ? '' : 'none';

  overlayMeta.innerHTML = '';

  meta.forEach(({ label, value }) => {

    if (!label || !value) return;

    const dt = document.createElement('dt');

    dt.textContent = label;

    const dd = document.createElement('dd');

    dd.textContent = value;

    overlayMeta.appendChild(dt);

    overlayMeta.appendChild(dd);

  });

  if (note && note.trim()) {

    overlayNote.textContent = note.trim();

    overlayNote.style.display = '';

  } else {

    overlayNote.textContent = '';

    overlayNote.style.display = 'none';

  }

  overlayRoot.setAttribute('aria-hidden','false');

  document.body.style.overflow = 'hidden';

}



overlayRoot?.addEventListener('click', (e) => {

  if (e.target === overlayRoot || e.target.classList.contains('lesson-overlay-backdrop')) hideLessonOverlay();

});

overlayClose?.addEventListener('click', hideLessonOverlay);

document.addEventListener('keydown', (e) => {

  if (e.key === 'Escape') hideLessonOverlay();

});



btnKlausurReset?.addEventListener('click', () => {

  formKlausur.reset();

  populateKlausurPeriods();

});

selPeriodStart?.addEventListener('change', updatePeriodEndOptions);

selPeriodEnd?.addEventListener('change', () => {

  if (!selPeriodStart || !selPeriodEnd) return;

  const startVal = Number.parseInt(selPeriodStart.value, 10);

  const endVal = Number.parseInt(selPeriodEnd.value, 10);

  if (Number.isFinite(startVal) && Number.isFinite(endVal) && endVal < startVal) {

    selPeriodEnd.value = String(startVal);

  }

});



formKlausur?.addEventListener('submit', (e) => {

  e.preventDefault();

  const subject = selSubject.value;

  const name = inpName.value.trim();

  const date = inpDate.value;                   // YYYY-MM-DD

  const start = Number.parseInt(selPeriodStart?.value ?? "", 10);

  const end = Number.parseInt(selPeriodEnd?.value ?? "", 10);



  if (!subject || !name || !date || !Number.isFinite(start) || !Number.isFinite(end)) return;

  if (end < start) { alert('Bitte gib eine gültige Stunden-Spanne an.'); return; }



  const overlap = KlausurenStore.overlaps(date, start, end);

  if (overlap) {

    alert('Für dieses Datum existiert bereits eine Klausur in diesem Zeitraum.');

    return;

  }



  KlausurenStore.add({

    id: uid(),

    subject,

    name,

    date,

    periodStart: start,

    periodEnd: end

  });

  renderKlausurList();

  if (window.__latestLessons) buildGrid(window.__latestLessons, window.__currentWeekStart, window.__selectedCourseKeys);

});



function renderKlausurList() {

  const data = KlausurenStore.load().sort((a,b)=>{

    if (a.date!==b.date) return a.date.localeCompare(b.date);

    return (a.periodStart||0)-(b.periodStart||0);

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

        <small>${escapeHtml(k.subject)} - ${k.date}, ${escapeHtml(formatPeriodRange(k.periodStart, k.periodEnd))}</small>

      </div>

      <button data-id="${k.id}" title="Löschen">Löschen</button>

    `;

    el.querySelector('button').addEventListener('click', () => {

      KlausurenStore.remove(k.id);

      renderKlausurList();

      if (window.__latestLessons) buildGrid(window.__latestLessons, window.__currentWeekStart, window.__selectedCourseKeys);

    });

    listKlausuren.appendChild(el);

  });

}



const Auth = (() => {

  const authButton = document.getElementById("auth-button");

  const modal = document.getElementById("auth-modal");

  const closeBtn = document.getElementById("auth-close");

  const loginForm = document.getElementById("login-form");

  const registerForm = document.getElementById("register-form");

  const accountView = document.getElementById("account-view");

  const accountName = document.getElementById("account-username");

  const logoutButton = document.getElementById("logout-button");

  const tabs = Array.from(document.querySelectorAll(".auth-tab"));

  const tabsContainer = modal ? modal.querySelector(".auth-tabs") : null;

  const loginError = loginForm ? loginForm.querySelector(".auth-error") : null;

  const registerError = registerForm ? registerForm.querySelector(".auth-error") : null;

  const description = document.getElementById("auth-description");



  let currentView = "login";

  let state = { loggedIn: false, username: null };

  let paused = 0;

  let syncTimer = null;

  let lastSynced = "";

  let initDone = false;

  let forceLogin = false;

  const loginWaiters = [];

  function setForceMode(flag) {
    forceLogin = !!flag;
    if (closeBtn) closeBtn.style.display = forceLogin ? "none" : "";
    modal?.classList.toggle("auth-force", forceLogin);
  }

  function resolveLoginWaiters() {
    if (!loginWaiters.length) return;
    const copy = loginWaiters.splice(0);
    copy.forEach(fn => {
      try { fn(); } catch (_) {}
    });
  }



  function setButtonLabel() {

    if (!authButton) return;

    authButton.textContent = state.loggedIn ? (state.username || "Account") : "Anmelden";

  }



  function setAccountInfo() {

    if (accountName) accountName.textContent = state.username || "";

  }



  function showView(view) {

    if (!modal) return;

    const accountMode = state.loggedIn && view === "account";

    if (accountView) accountView.style.display = accountMode ? "grid" : "none";

    if (loginForm) loginForm.style.display = accountMode ? "none" : (view === "login" ? "grid" : "none");

    if (registerForm) registerForm.style.display = accountMode ? "none" : (view === "register" ? "grid" : "none");

    if (tabsContainer) tabsContainer.style.display = accountMode ? "none" : "flex";

    tabs.forEach(tab => tab.classList.toggle("active", tab.dataset.view === view));

    currentView = accountMode ? "login" : view;

  }



  function openModal(view, force = (!state.loggedIn || forceLogin)) {

    if (!modal) return;

    if (force) {

      setForceMode(true);

    } else if (!forceLogin) {

      setForceMode(false);

    }

    modal.setAttribute("aria-hidden", "false");

    document.body.style.overflow = "hidden";

    const target = state.loggedIn ? "account" : (view || currentView || "login");

    showView(target);

  }



  function closeModal() {

    if (!modal || forceLogin) return;

    modal.setAttribute("aria-hidden", "true");

    document.body.style.removeProperty("overflow");

    if (loginError) loginError.textContent = "";

    if (registerError) registerError.textContent = "";

  }



  function pause(fn) {

    paused += 1;

    try {

      return fn();

    } finally {

      paused = Math.max(0, paused - 1);

    }

  }



  function collectProfile() {

    return {

      name: getName(),

      courses: getCourses(),

      klausuren: KlausurenStore.load()

    };

  }



  function markSynced() {

    try {

      lastSynced = JSON.stringify(collectProfile());

    } catch {

      lastSynced = "";

    }

  }



  async function syncNow() {

    if (syncTimer) {

      clearTimeout(syncTimer);

      syncTimer = null;

    }

    if (!state.loggedIn || paused > 0) return;

    let payload;

    try {

      payload = collectProfile();

    } catch (err) {

      console.warn("profile collect failed", err);

      return;

    }

    let serial;

    try {

      serial = JSON.stringify(payload);

    } catch (err) {

      console.warn("profile serialise failed", err);

      return;

    }

    if (serial === lastSynced) return;

    try {

      const res = await fetch("/api/profile", {

        method: "PUT",

        headers: { "Content-Type": "application/json" },

        body: serial

      });

      if (!res.ok) throw new Error(res.statusText || "profile sync failed");

      lastSynced = serial;

    } catch (err) {

      console.warn("Profile sync failed:", err);

    }

  }



  function schedule() {

    if (!state.loggedIn || paused > 0) return;

    if (syncTimer) clearTimeout(syncTimer);

    syncTimer = setTimeout(syncNow, 400);

  }



  function applyProfile(profile) {

    if (!profile || typeof profile !== "object") return;

    let before = "";

    try {

      before = JSON.stringify(collectProfile());

    } catch {}

    pause(() => {
      if (typeof profile.name === "string") setName(profile.name);
      if (Array.isArray(profile.courses)) setCourses(profile.courses);
      if (Array.isArray(profile.klausuren)) KlausurenStore.save(profile.klausuren);
    });
    try {
      const storedCourses = Array.isArray(getCourses()) ? getCourses() : [];
      const { keys } = normaliseCourseSelection(storedCourses);
      window.__selectedCourseKeys = new Set(keys);
    } catch {}
    if (typeof buildCourseSelection === "function") {
      try {
        Promise.resolve(buildCourseSelection(window.__latestLessons || [])).catch(() => {});
      } catch {}
    }
    markSynced();
    if (typeof renderKlausurList === "function") renderKlausurList();
    if (typeof populateKlausurSubjects === "function") populateKlausurSubjects(window.__latestLessons || []);

    if (typeof populateKlausurPeriods === "function") populateKlausurPeriods();

    if (window.__latestLessons) {

      buildGrid(window.__latestLessons, window.__currentWeekStart, window.__selectedCourseKeys);

    }

    let after = "";

    try {

      after = JSON.stringify(collectProfile());

    } catch {}

    if (before !== after && typeof loadTimetable === "function") {

      loadTimetable(true);

    }

  }



  function translateError(code, fallback) {

    const map = {

      invalid_input: "Bitte gib Benutzername und Passwort ein.",

      username_exists: "Dieser Benutzername ist bereits vergeben.",

      invalid_credentials: "Benutzername oder Passwort falsch."

    };

    return map[code] || fallback || "Etwas hat nicht funktioniert.";

  }



  function setFormLoading(form, loading) {

    if (!form) return;

    const btn = form.querySelector("button[type='submit']");

    if (!btn) return;

    if (loading) {

      btn.disabled = true;

      if (!btn.dataset.loadingLabel) btn.dataset.loadingLabel = btn.textContent;

      btn.textContent = "...";

    } else {

      btn.disabled = false;

      if (btn.dataset.loadingLabel) {

        btn.textContent = btn.dataset.loadingLabel;

        delete btn.dataset.loadingLabel;

      }

    }

  }



  async function submitForm(form, url, errorEl) {

    if (!form) return;

    if (errorEl) errorEl.textContent = "";

    const formData = new FormData(form);

    const username = String(formData.get("username") || "").trim();

    const password = String(formData.get("password") || "");

    if (!username || !password) {

      if (errorEl) errorEl.textContent = "Bitte gib Benutzername und Passwort ein.";

      return;

    }

    setFormLoading(form, true);

    try {

      const res = await fetch(url, {

        method: "POST",

        headers: { "Content-Type": "application/json" },

        body: JSON.stringify({ username, password })

      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {

        if (errorEl) errorEl.textContent = translateError(data.error, "Anmeldung fehlgeschlagen.");

        return;

      }

      setState({ loggedIn: !!data.authenticated, username: data.username || username });

      if (data.authenticated && data.profile) {

        applyProfile(data.profile);

      } else {

        markSynced();

      }

      closeModal();

    } catch (err) {

      console.error(err);

      if (errorEl) errorEl.textContent = "Netzwerkfehler. Versuche es erneut.";

    } finally {

      setFormLoading(form, false);

    }

  }



  function setState(next) {

    const changedLogin = state.loggedIn !== next.loggedIn;

    state = {

      loggedIn: !!next.loggedIn,

      username: next.username ? String(next.username).trim() : null

    };

    setButtonLabel();

    setAccountInfo();

    if (state.loggedIn) {

      setForceMode(false);

      if (description) description.textContent = "Deine Einstellungen werden jetzt zwischen deinen Geräten synchronisiert.";

      resolveLoginWaiters();

    } else {

      lastSynced = "";

      if (syncTimer) {

        clearTimeout(syncTimer);

        syncTimer = null;

      }

      setForceMode(true);

      if (description) description.textContent = "Bitte melde dich an, um fortzufahren.";

      if (modal && modal.getAttribute("aria-hidden") === "false") {

        showView("login");

      } else {

        openModal("login", true);

      }

    }

    if (changedLogin && modal && modal.getAttribute("aria-hidden") === "false") {

      showView(state.loggedIn ? "account" : currentView);

    }

  }



  async function refreshStatus() {

    try {

      const res = await fetch("/api/auth/status", { cache: "no-store" });

      if (!res.ok) throw new Error("status " + res.status);

      const data = await res.json();

      setState({ loggedIn: !!data.authenticated, username: data.username || null });

      if (data.authenticated && data.profile) {

        applyProfile(data.profile);

      } else {

        markSynced();

      }

    } catch (err) {

      console.warn("Auth status failed:", err);

    }

  }



  function handleLogout() {

    if (!logoutButton) return;

    logoutButton.disabled = true;

    fetch("/api/auth/logout", { method: "POST" })

      .catch(err => console.warn("Logout failed:", err))

      .finally(() => {

        logoutButton.disabled = false;

        setState({ loggedIn: false, username: null });

        closeModal();

      });

  }



  function handleBackdropClick(e) {

    if (!modal) return;

    if (forceLogin) return;

    if (e.target === modal || e.target.classList.contains("auth-backdrop")) {

      closeModal();

    }

  }



  function ensureAuthenticated() {

    if (state.loggedIn) return Promise.resolve();

    setForceMode(true);

    if (modal?.getAttribute("aria-hidden") === "false") {

      showView("login");

    } else {

      openModal("login", true);

    }

    return new Promise(resolve => {

      loginWaiters.push(resolve);

    });

  }



  return {

    init: async () => {

      if (initDone) return;

      initDone = true;

      setButtonLabel();

      if (authButton) {

        authButton.addEventListener("click", () => openModal(state.loggedIn ? "account" : currentView));

      }

      closeBtn?.addEventListener("click", closeModal);

      modal?.addEventListener("click", handleBackdropClick);

      document.addEventListener("keydown", (ev) => {

        if (ev.key === "Escape" && !forceLogin && modal?.getAttribute("aria-hidden") === "false") {

          closeModal();

        }

      });

      tabs.forEach(tab => {

        tab.addEventListener("click", () => {

          const view = tab.dataset.view || "login";

          currentView = view;

          showView(view);

        });

      });

      loginForm?.addEventListener("submit", (ev) => {

        ev.preventDefault();

        submitForm(loginForm, "/api/auth/login", loginError);

      });

      registerForm?.addEventListener("submit", (ev) => {

        ev.preventDefault();

        submitForm(registerForm, "/api/auth/register", registerError);

      });

      logoutButton?.addEventListener("click", handleLogout);

      await refreshStatus();

    },

    schedule,

    pause,

    isLoggedIn: () => state.loggedIn,

    username: () => state.username || "",

    closeModal,

    ensureAuthenticated

  };

})();



scheduleProfileSync = () => Auth.schedule();



function populateKlausurSubjects(lessons) {

  // uses lessons currently shown (filtered by user's selection)

  const set = new Set();

  lessons.forEach(l => { const m = mapSubject(l); if (m) set.add(m); });

  const items = Array.from(set).sort((a,b)=>a.localeCompare(b,'de'));

  selSubject.innerHTML = items.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join('');

}



function populateKlausurPeriods() {

  if (!selPeriodStart || !selPeriodEnd) return;

  const optionsHtml = PERIOD_NUMBERS

    .map(num => `<option value="${num}">${num}. Stunde</option>`)

    .join('');

  selPeriodStart.innerHTML = optionsHtml;

  selPeriodEnd.innerHTML = optionsHtml;



  const defaultStart = selPeriodStart.value || String(PERIOD_NUMBERS[0] || 1);

  selPeriodStart.value = defaultStart;

  updatePeriodEndOptions();

}



function updatePeriodEndOptions() {

  if (!selPeriodStart || !selPeriodEnd) return;

  const startVal = Number.parseInt(selPeriodStart.value, 10);

  [...selPeriodEnd.options].forEach(opt => {

    const val = Number.parseInt(opt.value, 10);

    opt.disabled = Number.isFinite(startVal) && Number.isFinite(val) && val < startVal;

  });

  const endVal = Number.parseInt(selPeriodEnd.value, 10);

  if (Number.isFinite(startVal) && (!Number.isFinite(endVal) || endVal < startVal)) {

    selPeriodEnd.value = String(startVal);

  }

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



function buildGrid(lessons, weekStart = null, selectedKeys = null) {

  hideLessonOverlay();

  const container = document.getElementById("timetable");

  container.innerHTML = "";



  // snapshot for sidebar dropdowns

  window.__latestLessons = lessons;

  if (typeof weekStart === "string") {

    window.__currentWeekStart = weekStart;

  } else if (typeof window.__currentWeekStart === "string") {

    weekStart = window.__currentWeekStart;

  }

  if (selectedKeys instanceof Set) {

    window.__selectedCourseKeys = new Set(selectedKeys);

  }

  const activeSelected = selectedKeys instanceof Set

    ? selectedKeys

    : (window.__selectedCourseKeys instanceof Set ? window.__selectedCourseKeys : null);



  const weekStartDate = typeof weekStart === "string" ? new Date(`${weekStart}T00:00:00`) : null;

  const weekEndDate = weekStartDate ? new Date(weekStartDate.getTime() + 6 * 24 * 3600 * 1000) : null;

  const isWithinWeek = (dateStr) => {

    if (!weekStartDate) return true;

    const dt = new Date(`${dateStr}T00:00:00`);

    return !(Number.isNaN(dt.getTime())) && dt >= weekStartDate && dt <= weekEndDate;

  };

  const isoByDay = {};

  if (weekStartDate) {

    for (let offset = 0; offset < 7; offset++) {

      const current = new Date(weekStartDate.getTime() + offset * 24 * 3600 * 1000);

      isoByDay[offset + 1] = toISODate(current);

    }

  }



  // MonFri only + collect time boundaries

  const tset = new Set();

  const valid = [];

  const klausurenList = KlausurenStore.load().filter(k => isWithinWeek(k.date));

  const matchedKlausurIds = new Set();

  for (const l of lessons) {

    const d = dayIdxISO(l.date);

    if (d >= 1 && d <= 5) {

      valid.push(l);

      tset.add(parseHM(l.start));

      tset.add(parseHM(l.end));

    }

  }

  PERIOD_NUMBERS.forEach(num => {

    const info = PERIOD_SCHEDULE[num];

    if (!info) return;

    tset.add(parseHM(info.start));

    tset.add(parseHM(info.end));

  });



  const times = [...tset].sort((a, b) => a - b);

  if (times.length < 2) {

    container.innerHTML = `<div class="empty-week">🗓️ Bald verfügbar</div>`;

    return;

  }



  const activeDays = new Set(valid.map(l => dayIdxISO(l.date)));
  const vacationByDay = new Map();
  for (let day = 1; day <= 5; day++) {
    const iso = isoByDay[day];
    if (!iso) continue;
    const list = vacationsOnDate(iso);
    if (list.length) {
      activeDays.add(day);
      vacationByDay.set(day, list);
    }
  }
  klausurenList.forEach(k => {

    const day = dayIdxISO(k.date);

    if (day >= 1 && day <= 5) activeDays.add(day);

  });

  for (let day = 1; day <= 5; day++) {

    const iso = isoByDay[day];

    if (iso && vacationsOnDate(iso).length) activeDays.add(day);

  }



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

    timeCell.textContent = `${fmtHM(startM)}${fmtHM(endM)}`;

    timeCell.style.gridColumn = "1";

    timeCell.style.gridRow = String(i + 2);

    grid.appendChild(timeCell);



    for (let d = 1; d <= 5; d++) {

      if (!activeDays.has(d)) continue;

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



  vacationByDay.forEach((dayVacations, day) => {
    const vacCard = document.createElement("div");
    vacCard.className = "vacation-card";
    vacCard.style.gridColumn = String(day + 1);
    vacCard.style.gridRow = `2 / -1`;
    const entries = dayVacations.map(v => {
      const range = v.start_date === v.end_date
        ? formatDate(v.start_date)
        : `${formatDate(v.start_date)} – ${formatDate(v.end_date)}`;
      return `<div class="vac-item"><strong>${escapeHtml(v.title)}</strong><span>${range}</span></div>`;
    }).join("");
    vacCard.innerHTML = `
      <div class="vac-title">Ferien</div>
      ${entries}
    `;
    grid.appendChild(vacCard);
  });



  // Place lessons (replace with Klausur if matching)

  valid.forEach(l => {

    const day = dayIdxISO(l.date);

    let s = parseHM(l.start);

    let e = parseHM(l.end);

    let r0 = rowIndexFor(s), r1 = rowIndexFor(e);

    let span = Math.max(1, r1 - r0);



    let periodNum = l.period;

    if (!Number.isFinite(periodNum)) {

      periodNum = r0 + 1; // first slot of the day = 1. Stunde

    }



    const klausur = klausurenList.find(k => {

      if (k.date !== l.date) return false;

      const startPeriod = k.periodStart || periodNum;

      const endPeriod = k.periodEnd || startPeriod;

      return Number.isFinite(periodNum) && periodNum >= startPeriod && periodNum <= endPeriod;

    });



    if (klausur && Number.isFinite(periodNum) && periodNum > (klausur.periodStart || periodNum)) {

      return;

    }



    const card = document.createElement("div");

    card.className = `lesson ${klausur ? "klausur" : (l.status || "")}`.trim();

    card.style.gridColumn = String(day + 1);



    const subj = mapSubject(l);

    const room = mapRoom(l);



    if (klausur) {

      if (klausur.id) matchedKlausurIds.add(klausur.id);

      const startPeriod = klausur.periodStart || periodNum;

      const endPeriod = klausur.periodEnd || startPeriod;

      const startMin = periodStartMinutes(startPeriod) ?? s;

      const endMin = periodEndMinutes(endPeriod) ?? e;

      const rowStart = rowIndexFor(startMin);

      const rowEnd = rowIndexFor(endMin);

      const spanK = Math.max(1, rowEnd - rowStart);

      const startTime = fmtHM(startMin);

      const endTime = fmtHM(endMin);

      const datePretty = formatDate(klausur.date || l.date);

      const periodLabel = formatPeriodRange(startPeriod, endPeriod);

      card.style.gridRow = `${rowStart + 2} / span ${spanK}`;

      card.innerHTML = `

        <div class="lesson-title">Klausur</div>

        <div class="lesson-meta">

          <span>${escapeHtml(klausur.name)}</span>

          ${klausur.subject ? `<span>&bull; ${escapeHtml(klausur.subject)}</span>` : ""}

        </div>

      `;

      const meta = [

        { label: "Datum", value: datePretty },

        { label: "Zeitraum", value: `${periodLabel} (${formatTimeRange(startTime, endTime)})` }

      ];

      if (klausur.subject) meta.push({ label: "Fach", value: klausur.subject });

      card.addEventListener("click", () => showLessonOverlay({

        title: klausur.name || "Klausur",

        subtitle: klausur.subject ? `${klausur.subject} · ${datePretty}` : datePretty,

        meta

      }));

    } else {

      const datePretty = formatDate(l.date);

      const timeRange = formatTimeRange(l.start, l.end);

      const badge = (l.status && l.status !== "normal") ? (STATUS_LABELS[l.status] || l.status) : "";

      card.style.gridRow = `${r0 + 2} / span ${span}`;

      card.innerHTML = `

        <div class="lesson-title">${escapeHtml(subj)}</div>

        <div class="lesson-meta">

          ${l.teacher ? `<span>&bull; ${escapeHtml(l.teacher)}</span>` : ""}

          ${room ? `<span>&bull; ${escapeHtml(room)}</span>` : ""}

        </div>

        ${badge ? `<div class="badge">${badge}</div>` : ""}

        ${l.note ? `<div class="note">${escapeHtml(l.note)}</div>` : ""}

      `;

      const meta = [

        { label: "Datum", value: datePretty },

        { label: "Zeit", value: timeRange }

      ];

      if (l.teacher) meta.push({ label: "Lehrkraft", value: l.teacher });

      if (room) meta.push({ label: "Raum", value: room });

      if (badge) meta.push({ label: "Status", value: badge });

      card.addEventListener("click", () => showLessonOverlay({

        title: subj || "Unterricht",

        subtitle: timeRange ? `${datePretty} · ${timeRange}` : datePretty,

        meta,

        note: l.note || ""

      }));

    }



    grid.appendChild(card);

  });

  klausurenList.forEach(k => {

    if (k.id && matchedKlausurIds.has(k.id)) return;

    const day = dayIdxISO(k.date);

    if (day < 1 || day > 5) return;

    const examKey = resolveCourseKey(k.subject) || normKey(k.subject || "");

    if (activeSelected && activeSelected.size > 0 && examKey && !activeSelected.has(examKey)) return;

  const startPeriod = k.periodStart || 1;

  const endPeriod = k.periodEnd || startPeriod;

  const startMin = periodStartMinutes(startPeriod);

  const endMin = periodEndMinutes(endPeriod);

  if (startMin === null || endMin === null) return;

  const rowStart = rowIndexFor(startMin);

  const rowEnd = rowIndexFor(endMin);

  const span = Math.max(1, rowEnd - rowStart);



  const card = document.createElement("div");

  card.className = "lesson klausur";

  card.style.gridColumn = String(day + 1);

  card.style.gridRow = `${rowStart + 2} / span ${span}`;

  card.innerHTML = `

    <div class="lesson-title">Klausur</div>

    <div class="lesson-meta">

      <span>${escapeHtml(k.name || "Klausur")}</span>

      ${k.subject ? `<span>&bull; ${escapeHtml(k.subject)}</span>` : ""}



    </div>

  `;

  const datePretty = formatDate(k.date);

  const startTime = fmtHM(startMin);

  const endTime = fmtHM(endMin);

  const periodLabel = formatPeriodRange(startPeriod, endPeriod);

  const meta = [

    { label: "Datum", value: datePretty },

    { label: "Zeitraum", value: `${periodLabel} (${formatTimeRange(startTime, endTime)})` }

  ];

  if (k.subject) meta.push({ label: "Fach", value: k.subject });

  card.addEventListener("click", () => showLessonOverlay({

    title: k.name || "Klausur",

    subtitle: k.subject ? `${k.subject} · ${datePretty}` : datePretty,

    meta

  }));

  grid.appendChild(card);

});

  // Placeholder for empty weekdays (full column)

  for (let d = 1; d <= 5; d++) {

    if (activeDays.has(d)) continue;

    const placeholder = document.createElement("div");

    placeholder.className = "placeholder-day";

    placeholder.style.gridColumn = String(d + 1);

    placeholder.style.gridRow = `2 / -1`;

    placeholder.innerHTML = `

      <div class="ph-card" role="status" aria-label="Daten folgen">

        <div class="ph-ico">🗓️</div>

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

  if (typeof Auth === "object" && Auth && typeof Auth.isLoggedIn === "function" && !Auth.isLoggedIn()) {

    return;

  }

  try {

    await loadVacations(force);

    await loadMappings();

    await loadCourseOptions();



    const res = await fetch(`/api/timetable?ts=${Date.now()}${force ? "&force=1":""}`, { cache: "no-store" });

    if (!res.ok) throw new Error(`/api/timetable ${res.status}`);

    const data = await res.json();

    let lessons = Array.isArray(data.lessons) ? data.lessons : [];



    const cs = document.getElementById("course-selection");

    if (cs && !cs.dataset.init) {

      await buildCourseSelection(lessons);

      cs.dataset.init = "1";

    }



    const storedValues = Array.isArray(getCourses()) ? getCourses() : [];

    const { keys: selectedKeys, changed } = normaliseCourseSelection(storedValues);

    if (changed) setCourses(selectedKeys);

    const selectedSet = new Set(selectedKeys);

    window.__selectedCourseKeys = new Set(selectedSet);



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



    buildGrid(lessons, typeof data.weekStart === "string" ? data.weekStart : null, window.__selectedCourseKeys);

    populateKlausurSubjects(lessons);

    populateKlausurPeriods();

    renderKlausurList();

  } catch (err) {

    const container = document.getElementById("timetable");

    if (container) {

      container.innerHTML = `

        <div class="empty-week">

          ⚠️ Keine Daten geladen (offline oder Fehler).

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

      if (typeof Auth.isLoggedIn === "function" && !Auth.isLoggedIn()) {

        Auth.ensureAuthenticated();

        return;

      }

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



  (async () => {

    try {

      await Auth.init();

      await Auth.ensureAuthenticated();

    } catch (err) {

      console.warn("Auth initialisation failed:", err);

      return;

    }

    loadTimetable(); // autostart

    setInterval(()=>loadTimetable(true), 5*60*1000);

    document.addEventListener("visibilitychange", ()=>{

      if(!document.hidden) loadTimetable(true);

    });

  })();

});
