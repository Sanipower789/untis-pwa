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

window.__timeColumnWidth = Math.min(120, Math.max(40, Math.round(Number(window.__timeColumnWidth || 60))));



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

  return `${start} - ${end}`;

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


/* Exams (remote from WebUntis via backend) */
const EXAM_FETCH_TTL = 30 * 1000;
let EXAMS = [];
let EXAMS_FETCHED_AT = 0;

const toHM = (val) => {
  if (val == null) return "";
  if (typeof val === "string") {
    if (val.includes(":")) return val;
    const s = val.trim();
    if (/^\d{3,4}$/.test(s)) return `${s.padStart(4, "0").slice(0,2)}:${s.padEnd(4, "0").slice(2)}`;
    return s;
  }
  const n = Number(val);
  if (!Number.isFinite(n)) return "";
  const s = String(Math.round(n)).padStart(4, "0");
  return `${s.slice(0,2)}:${s.slice(2)}`;
};

const dateIntToIso = (n) => {
  if (!Number.isFinite(Number(n))) return String(n || "");
  const s = String(Math.round(Number(n))).padStart(8, "0");
  return `${s.slice(0,4)}-${s.slice(4,6)}-${s.slice(6)}`;
};

const inferPeriodFromTime = (hm) => {
  if (!hm) return null;
  const mins = parseHM(hm);
  if (!Number.isFinite(mins)) return null;
  let candidate = null;
  for (const p of PERIOD_NUMBERS) {
    const ps = periodStartMinutes(p);
    const pe = periodEndMinutes(p);
    if (!Number.isFinite(ps) || !Number.isFinite(pe)) continue;
    if (mins >= ps && mins < pe) {
      return p;
    }
    if (mins <= ps && candidate == null) {
      candidate = p;
    }
  }
  return candidate;
};

function normaliseExam(rec){
  if (!rec || typeof rec !== "object") return null;
  const date = rec.date ? String(rec.date) : dateIntToIso(rec.date);
  const start = toHM(rec.start || rec.startTime);
  const end   = toHM(rec.end || rec.endTime);
  const subj  = String(rec.subject || rec.subjectName || "").trim();
  const name  = String(rec.name || rec.title || subj || "Klausur").trim();
  const periodStart = inferPeriodFromTime(start);
  const periodEnd   = inferPeriodFromTime(end) || periodStart;
  return {
    id: rec.id != null ? `exam-${rec.id}` : uid(),
    subject: subj || name,
    name,
    date,
    periodStart,
    periodEnd,
    startTime: start,
    endTime: end,
    source: "remote",
    classes: Array.isArray(rec.classes) ? rec.classes.filter(Boolean) : [],
    teachers: Array.isArray(rec.teachers) ? rec.teachers.filter(Boolean) : []
  };
}

async function loadExams(force = false){
  const now = Date.now();
  if (!force && EXAMS_FETCHED_AT && (now - EXAMS_FETCHED_AT) < EXAM_FETCH_TTL){
    return EXAMS;
  }
  try {
    const res = await fetch(`/api/exams?ts=${now}`, { cache: "no-store" });
    if (!res.ok) throw new Error(res.statusText || "exams fetch failed");
    const data = await res.json();
    if (data && data.ok === false){
      console.warn("Exams fetch returned error:", data.error || data.errorCode || data);
      EXAMS = [];
      EXAMS_FETCHED_AT = now;
      return EXAMS;
    }
    if (data && Array.isArray(data.exams)){
      EXAMS = data.exams.map(normaliseExam).filter(Boolean);
      EXAMS.sort((a, b) => {
        if (a.date !== b.date) return a.date.localeCompare(b.date);
        return (a.periodStart || 0) - (b.periodStart || 0);
      });
      EXAMS_FETCHED_AT = now;
    }
  } catch (err) {
    console.warn("Exams fetch failed:", err);
  }
  return EXAMS;
}



/* Strong canonical normaliser (umlauts, (), dashes, tags, spaces) */

const normKey = (s) => {
  if (!s) return "";
  s = String(s)
        .trim()
        .replaceAll("\u00e4", "a").replaceAll("\u00f6", "o").replaceAll("\u00fc", "u")
        .replaceAll("\u00c4", "a").replaceAll("\u00d6", "o").replaceAll("\u00dc", "u")
        .toLowerCase()
        .replace(/\s+/g, " ")
        .replace(/[()]/g, " ")
        .replace(/[-\u2013\u2014\u2011\u2012\u2212]+/g, " ")
        .replace(/["'\u00b4`]+/g, " ")
        .replace(/\b(gk|lk|ag)\b/g, " ");
  return s.replace(/\s+/g, " ").trim();
};

/* --- Colour preferences --- */
const LS_COLORS = "timetable_colors_v1";
const DEFAULT_THEME = {
  lessonBg: "#23252d",
  lessonBorder: "#3a4050",
  lessonText: "#ffffff",
  grid: "#333842",
  gridBg: "#0f1014",
  brand: "#6a1b9a",
  klausurBg: "#2b3148",
  klausurBorder: "#3e4a7a"
};

const HEX_RE = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i;
const normaliseHex = (value) => {
  const match = HEX_RE.exec(String(value || "").trim());
  if (!match) return null;
  let hex = match[1];
  if (hex.length === 3) hex = hex.split("").map(ch => ch + ch).join("");
  return "#" + hex.toLowerCase();
};

const mergeTheme = (rawTheme) => {
  const out = {};
  const source = typeof rawTheme === "object" && rawTheme ? rawTheme : {};
  Object.keys(DEFAULT_THEME).forEach((key) => {
    const col = normaliseHex(source[key]);
    if (col) out[key] = col;
  });
  return out;
};

const mergeSubjects = (rawSubjects) => {
  const out = {};
  if (!rawSubjects || typeof rawSubjects !== "object") return out;
  Object.entries(rawSubjects).forEach(([key, value]) => {
    const nk = normKey(key);
    const col = normaliseHex(value);
    if (nk && col) out[nk] = col;
  });
  return out;
};

const shadeHex = (hex, percent) => {
  const h = normaliseHex(hex);
  if (!h) return null;
  const num = parseInt(h.slice(1), 16);
  const r = (num >> 16) & 255;
  const g = (num >> 8) & 255;
  const b = num & 255;
  const amt = Math.max(-100, Math.min(100, percent));
  const t = amt < 0 ? 0 : 255;
  const p = Math.abs(amt) / 100;
  const calc = (c) => Math.round((t - c) * p) + c;
  return "#" + [calc(r), calc(g), calc(b)].map(v => v.toString(16).padStart(2, "0")).join("");
};

const textColorFor = (hex) => {
  const h = normaliseHex(hex);
  if (!h) return "#ffffff";
  const num = parseInt(h.slice(1), 16);
  const r = (num >> 16) & 255;
  const g = (num >> 8) & 255;
  const b = num & 255;
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return lum > 0.6 ? "#111111" : "#ffffff";
};

const applyThemeVars = (theme) => {
  const merged = { ...DEFAULT_THEME, ...(theme || {}) };
  const root = document.documentElement;
  root.style.setProperty("--lesson-bg", merged.lessonBg);
  root.style.setProperty("--lesson-border", merged.lessonBorder);
  root.style.setProperty("--lesson-text", merged.lessonText);
  root.style.setProperty("--grid", merged.grid);
  root.style.setProperty("--grid-surface", merged.gridBg);
  root.style.setProperty("--brand", merged.brand);
  root.style.setProperty("--klausur-bg", merged.klausurBg);
  root.style.setProperty("--klausur-border", merged.klausurBorder);
  const metaTheme = document.querySelector('meta[name="theme-color"]');
  if (metaTheme) {
    metaTheme.setAttribute("content", merged.brand || DEFAULT_THEME.brand);
  }
};

const DESIGN_SWATCHES = [
  "#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444",
  "#ec4899", "#6366f1", "#14b8a6", "#8b5cf6", "#f97316",
  "#475569", "#06b6d4", "#22c55e", "#eab308", "#fb7185"
];

const ColorPrefs = {
  load() {
    let raw = {};
    try {
      raw = JSON.parse(localStorage.getItem(LS_COLORS) || "{}") || {};
    } catch (_) {
      raw = {};
    }
    return {
      theme: mergeTheme(raw.theme),
      subjects: mergeSubjects(raw.subjects)
    };
  },
  save(next, opts = {}) {
    const safe = {
      theme: mergeTheme(next?.theme),
      subjects: mergeSubjects(next?.subjects)
    };
    try {
      localStorage.setItem(LS_COLORS, JSON.stringify(safe));
    } catch (_) {}
    applyThemeVars(safe.theme);
    if (!opts.silent) scheduleProfileSync();
    return safe;
  },
  updateTheme(partial, opts = {}) {
    const current = this.load();
    current.theme = { ...current.theme, ...mergeTheme(partial) };
    return this.save(current, opts);
  },
  setSubjectColor(key, color, opts = {}) {
    const nk = normKey(key);
    const col = normaliseHex(color);
    if (!nk || !col) return this.load();
    const current = this.load();
    current.subjects[nk] = col;
    return this.save(current, opts);
  },
  removeSubjectColor(key, opts = {}) {
    const nk = normKey(key);
    const current = this.load();
    if (nk && current.subjects[nk]) {
      delete current.subjects[nk];
      return this.save(current, opts);
    }
    return current;
  },
  reset(opts = {}) {
    return this.save({ theme: {}, subjects: {} }, opts);
  }
};

applyThemeVars(ColorPrefs.load().theme);

const applyCardColor = (cardEl, color) => {
  if (!cardEl || !color) return;
  const border = shadeHex(color, -12) || color;
  const text = textColorFor(color);
  cardEl.style.setProperty("--lesson-bg", color);
  cardEl.style.setProperty("--lesson-border", border);
  cardEl.style.setProperty("--lesson-text", text);
  cardEl.style.setProperty("--klausur-bg", color);
  cardEl.style.setProperty("--klausur-border", border);
};

const clearCardColor = (cardEl) => {
  if (!cardEl) return;
  ["--lesson-bg","--lesson-border","--lesson-text","--klausur-bg","--klausur-border"].forEach(prop => {
    cardEl.style.removeProperty(prop);
  });
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

// Check whether a lesson matches the currently selected courses (using raw, live and mapped names)
function lessonMatchesSelection(lesson, selectedSet) {
  if (!(selectedSet instanceof Set) || selectedSet.size === 0) return true;
  const candidates = [];
  const subjOrig = lesson.subject_original ?? "";
  const subjLive = lesson.subject ?? "";
  candidates.push(normKey(subjOrig));
  candidates.push(normKey(subjLive));
  const resolved = [
    resolveCourseKey(subjOrig),
    resolveCourseKey(subjLive),
    resolveCourseKey(mapSubject(lesson))
  ];
  resolved.forEach(k => { if (k) candidates.push(k); });
  for (const key of candidates) {
    if (key && selectedSet.has(key)) return true;
  }
  return false;
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

function mergeKlausuren(remoteList, localList){
  const byKey = new Map();
  const makeKey = (k) => {
    const subj = normKey(k.subject || k.name || "");
    const date = k.date || "";
    const ps = Number.isFinite(k.periodStart) ? k.periodStart : "";
    const pe = Number.isFinite(k.periodEnd) ? k.periodEnd : ps;
    return `${date}|${ps}|${pe}|${subj}`;
  };
  (remoteList || []).forEach(k => {
    const key = makeKey(k);
    byKey.set(key, { ...k, source: k.source || "remote" });
  });
  (localList || []).forEach(k => {
    const key = makeKey(k);
    if (!byKey.has(key)) byKey.set(key, k);
  });
  return Array.from(byKey.values());
}

function getAllKlausuren(){

  const remote = Array.isArray(EXAMS) ? EXAMS : [];

  const local = KlausurenStore.load();

  return mergeKlausuren(remote, local);

}

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

const navColors      = document.getElementById('navColors');

const panelKlausuren = document.getElementById('panelKlausuren');

const panelColors    = document.getElementById('panelColors');

const formKlausur    = document.getElementById('klausurForm');

const selSubject     = document.getElementById('klausurSubject');

const inpName        = document.getElementById('klausurName');

const inpDate        = document.getElementById('klausurDate');

const selPeriodStart = document.getElementById('klausurPeriodStart');

const selPeriodEnd   = document.getElementById('klausurPeriodEnd');

const btnKlausurReset= document.getElementById('klausurReset');

const listKlausuren  = document.getElementById('klausurList');

const inpColorLesson = document.getElementById('colorLessonBg');

const inpColorGrid   = document.getElementById('colorGridBg');

const inpColorKlausur= document.getElementById('colorKlausurBg');

const inpColorBrand  = document.getElementById('colorBrand');

const btnColorReset  = document.getElementById('colorReset');

const selColorSubject= document.getElementById('colorSubjectSelect');

const inpColorSubject= document.getElementById('colorSubjectValue');

const btnColorSubjectSave = document.getElementById('colorSubjectSave');

const btnColorSubjectClear= document.getElementById('colorSubjectClear');

const listColorSubjects   = document.getElementById('colorSubjectList');

const elPaletteSwatches   = document.getElementById('paletteSwatches');



const overlayRoot   = document.getElementById('lesson-overlay');

const overlayTitle  = document.getElementById('lesson-overlay-title');

const overlaySubtitle = document.getElementById('lesson-overlay-subtitle');

const overlayMeta   = document.getElementById('lesson-overlay-meta');

const overlayNote   = document.getElementById('lesson-overlay-note');

const overlayClose  = document.getElementById('lesson-overlay-close');

const rebuildGridNow = () => {
  if (window.__latestLessons) {
    buildGrid(window.__latestLessons, window.__currentWeekStart, window.__selectedCourseKeys, window.__timeColumnWidth);
  }
};

function sidebarShow(){ elSidebar.classList.add('show'); }

function sidebarHide(){ elSidebar.classList.remove('show'); }

btnSidebar?.addEventListener('click', sidebarShow);

btnSidebarClose?.addEventListener('click', sidebarHide);



function activateSidebarPanel(panelEl, navEl) {

  document.querySelectorAll('.sidebar-link').forEach(b=>b.classList.remove('active'));

  if (navEl) navEl.classList.add('active');

  document.querySelectorAll('.sidebar-panel').forEach(p=>p.style.display='none');

  if (panelEl) panelEl.style.display='block';

}

function getSelectedCourseKeys() {
  try {
    const { keys } = normaliseCourseSelection(Array.isArray(getCourses()) ? getCourses() : []);
    return new Set(keys);
  } catch (_) {
    return new Set();
  }
}



function subjectOptionsForColors(prefs) {

  const map = new Map();

  const prefObj = prefs || ColorPrefs.load();

  getSelectedCourseKeys().forEach(key => {
    if (!key) return;
    map.set(key, COURSE_LABEL_BY_KEY.get(key) || key);
  });

  Object.keys(prefObj.subjects || {}).forEach((key) => {
    if (!map.has(key)) map.set(key, COURSE_LABEL_BY_KEY.get(key) || key);
  });

  return Array.from(map.entries())

    .map(([key, label]) => ({ key, label }))

    .sort((a, b) => a.label.localeCompare(b.label, 'de'));

}



function populateColorSubjectsSelect(currentValue = "", prefs) {

  if (!selColorSubject) return;

  const options = subjectOptionsForColors(prefs);

  if (!options.length) {

    selColorSubject.innerHTML = '<option value="">Keine Faecher verfuegbar</option>';

    return;

  }

  selColorSubject.innerHTML = options.map(opt => `<option value="${escapeHtml(opt.key)}">${escapeHtml(opt.label)}</option>`).join('');

  if (currentValue && options.find(o => o.key === currentValue)) {

    selColorSubject.value = currentValue;

  }

}



function renderSubjectColorList(prefObj) {

  if (!listColorSubjects) return;

  const prefs = prefObj || ColorPrefs.load();

  const selected = getSelectedCourseKeys();

  const entries = Object.entries(prefs.subjects || {}).filter(([key]) => !selected.size || selected.has(key));

  listColorSubjects.innerHTML = '';

  if (!entries.length) {

    listColorSubjects.innerHTML = '<div class="muted">Keine Fachfarben gespeichert.</div>';

    return;

  }

  entries.sort((a, b) => {

    const la = COURSE_LABEL_BY_KEY.get(a[0]) || a[0];

    const lb = COURSE_LABEL_BY_KEY.get(b[0]) || b[0];

    return la.localeCompare(lb, 'de');

  });

  entries.forEach(([key, color]) => {

    const chip = document.createElement('div');

    chip.className = 'color-chip';

    const label = COURSE_LABEL_BY_KEY.get(key) || key;

    chip.innerHTML = `
      <div class="chip-info">
        <span class="chip-swatch" style="background:${escapeHtml(color)}"></span>
        <div>
          <div class="chip-label">${escapeHtml(label)}</div>
          <div class="chip-key">${escapeHtml(key)}</div>
        </div>
      </div>
      <button type="button" data-key="${escapeHtml(key)}">Entfernen</button>
    `;
    const btn = chip.querySelector('button');
    if (btn) {
      btn.addEventListener('click', () => {
        ColorPrefs.removeSubjectColor(key);
        renderSubjectColorList();
        populateColorSubjectsSelect(key);
        rebuildGridNow();
      });
    }
    listColorSubjects.appendChild(chip);
  });

}

function renderPaletteSwatches() {
  if (!elPaletteSwatches) return;
  elPaletteSwatches.innerHTML = "";
  DESIGN_SWATCHES.forEach(color => {
    const sw = document.createElement("button");
    sw.type = "button";
    sw.className = "palette-swatch";
    sw.style.background = color;
    sw.title = color;
    sw.addEventListener("click", () => {
      if (inpColorSubject) inpColorSubject.value = color;
      const key = selColorSubject?.value || "";
      if (key) {
        ColorPrefs.setSubjectColor(key, color);
        syncColorInputs(ColorPrefs.load());
        rebuildGridNow();
      }
    });
    elPaletteSwatches.appendChild(sw);
  });
}

function syncColorInputs(prefObj) {

  const prefs = prefObj || ColorPrefs.load();

  const theme = { ...DEFAULT_THEME, ...(prefs.theme || {}) };

  if (inpColorLesson) inpColorLesson.value = theme.lessonBg;

  if (inpColorGrid) inpColorGrid.value = theme.gridBg;

  if (inpColorKlausur) inpColorKlausur.value = theme.klausurBg;

  if (inpColorBrand) inpColorBrand.value = theme.brand;

  populateColorSubjectsSelect(selColorSubject?.value || "", prefs);

  const selectedKey = selColorSubject?.value || "";

  const selectedColor = (prefs.subjects || {})[selectedKey] || theme.lessonBg;

  if (inpColorSubject) inpColorSubject.value = selectedColor || DEFAULT_THEME.lessonBg;

  renderSubjectColorList(prefs);

  renderPaletteSwatches();

}



function showKlausurenPanel() {

  activateSidebarPanel(panelKlausuren, navKlausuren);

  const accordion = document.getElementById('klausurAccordion');

  if (accordion && !accordion.hasAttribute('open')) accordion.setAttribute('open','');

  populateKlausurSubjects(window.__latestLessons || []);

  populateKlausurPeriods();

  renderKlausurList();

}



function showColorsPanel() {

  activateSidebarPanel(panelColors, navColors);

  syncColorInputs();

}



navKlausuren?.addEventListener('click', showKlausurenPanel);

navColors?.addEventListener('click', showColorsPanel);

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

  if (window.__latestLessons) buildGrid(window.__latestLessons, window.__currentWeekStart, window.__selectedCourseKeys, window.__timeColumnWidth);

});



function renderKlausurList() {

  const data = getAllKlausuren().sort((a,b)=>{

    if (a.date!==b.date) return a.date.localeCompare(b.date);

    return (a.periodStart||0)-(b.periodStart||0);

  });

  listKlausuren.innerHTML = '';

  if (!data.length) {

    listKlausuren.innerHTML = '<div class="muted">Noch keine Klausuren.</div>';

    return;

  }

  data.forEach(k => {

    const isRemote = k.source === "remote";
    const periodLabel = formatPeriodRange(k.periodStart, k.periodEnd) || formatTimeRange(k.startTime, k.endTime);
    const el = document.createElement('div');

    el.className = 'klausur-item';

    el.innerHTML = `

      <div>

        <strong>${escapeHtml(k.name)}</strong>

        <small>${escapeHtml(k.subject || "")} - ${k.date}, ${escapeHtml(periodLabel || "")}</small>

      </div>

      ${isRemote ? `<span class="muted">Schule</span>` : `<button data-id="${k.id}" title="Löschen">Löschen</button>`}

    `;

    if (!isRemote){
      el.querySelector('button').addEventListener('click', () => {

        KlausurenStore.remove(k.id);

        renderKlausurList();

        if (window.__latestLessons) buildGrid(window.__latestLessons, window.__currentWeekStart, window.__selectedCourseKeys, window.__timeColumnWidth);

      });
    }

    listKlausuren.appendChild(el);

  });

}



const handleThemeInput = (inputEl, key) => {
  if (!inputEl) return;
  inputEl.addEventListener('input', () => {
    ColorPrefs.updateTheme({ [key]: inputEl.value });
    syncColorInputs(ColorPrefs.load());
    rebuildGridNow();
  });
};

handleThemeInput(inpColorLesson, "lessonBg");
handleThemeInput(inpColorGrid, "gridBg");
handleThemeInput(inpColorKlausur, "klausurBg");
handleThemeInput(inpColorBrand, "brand");

btnColorReset?.addEventListener('click', () => {
  const prefs = ColorPrefs.reset();
  syncColorInputs(prefs);
  rebuildGridNow();
});

selColorSubject?.addEventListener('change', () => {
  const prefs = ColorPrefs.load();
  const key = selColorSubject.value || "";
  const nextColor = (prefs.subjects || {})[key] || (prefs.theme.lessonBg || DEFAULT_THEME.lessonBg);
  if (inpColorSubject) inpColorSubject.value = nextColor;
});

btnColorSubjectSave?.addEventListener('click', () => {
  if (!selColorSubject || !inpColorSubject) return;
  const key = selColorSubject.value || "";
  const color = inpColorSubject.value;
  if (!key || !color) return;
  ColorPrefs.setSubjectColor(key, color);
  syncColorInputs(ColorPrefs.load());
  rebuildGridNow();
});

btnColorSubjectClear?.addEventListener('click', () => {
  if (!selColorSubject) return;
  const key = selColorSubject.value || "";
  if (!key) return;
  ColorPrefs.removeSubjectColor(key);
  syncColorInputs(ColorPrefs.load());
  rebuildGridNow();
});

// ensure selects mirror stored values even before opening the tab
syncColorInputs(ColorPrefs.load());



const Auth = (() => {

  const authButton = document.getElementById("auth-button");

  const modal = document.getElementById("auth-modal");

  const closeBtn = document.getElementById("auth-close");

  const loginForm = document.getElementById("login-form");

  const registerForm = document.getElementById("register-form");

  const accountView = document.getElementById("account-view");

  const accountName = document.getElementById("account-username");

  const logoutButton = document.getElementById("logout-button");

  const choiceButtons = Array.from(document.querySelectorAll(".auth-choice-btn"));

  const choiceBlock = modal ? modal.querySelector(".auth-choice") : null;

  const loginError = loginForm ? loginForm.querySelector(".auth-error") : null;

  const registerError = registerForm ? registerForm.querySelector(".auth-error") : null;

  const description = document.getElementById("auth-description");



  let currentView = "choice";

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
    const choiceVisible = !accountMode;

    if (accountView) accountView.style.display = accountMode ? "grid" : "none";

    if (choiceBlock) choiceBlock.style.display = choiceVisible ? "grid" : "none";

    if (loginForm) loginForm.style.display = accountMode ? "none" : (view === "login" ? "grid" : "none");

    if (registerForm) registerForm.style.display = accountMode ? "none" : (view === "register" ? "grid" : "none");

    choiceButtons.forEach(btn => {
      const target = btn.dataset.view === "register" ? "register" : "login";
      const active = !accountMode && view === target;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });

    if (view === "login" && registerError) registerError.textContent = "";
    if (view === "register" && loginError) loginError.textContent = "";

    currentView = accountMode ? "account" : view;

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

    const target = state.loggedIn ? "account" : (view || "choice");

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

      klausuren: KlausurenStore.load(),

      colors: ColorPrefs.load()

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
      if (profile.colors && typeof profile.colors === "object") {
        ColorPrefs.save(profile.colors, { silent: true });
      }
      if (typeof profile.name === "string") setName(profile.name);
      if (Array.isArray(profile.courses)) setCourses(profile.courses);
      if (Array.isArray(profile.klausuren)) KlausurenStore.save(profile.klausuren);
    });
    try { syncColorInputs(ColorPrefs.load()); } catch {}
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

      buildGrid(window.__latestLessons, window.__currentWeekStart, window.__selectedCourseKeys, window.__timeColumnWidth);

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

        currentView = "choice";
        showView("choice");

      } else {

        openModal("choice", true);

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

      showView("choice");

    } else {

      openModal("choice", true);

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
      if (!state.loggedIn) {
        showView("choice");
      }

      if (authButton) {
        authButton.addEventListener("click", () => openModal(state.loggedIn ? "account" : "choice"));
      }

      closeBtn?.addEventListener("click", closeModal);

      modal?.addEventListener("click", handleBackdropClick);

      document.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape" && !forceLogin && modal?.getAttribute("aria-hidden") === "false") {
          closeModal();
        }
      });

      choiceButtons.forEach(btn => {
        btn.addEventListener("click", () => {
          const view = btn.dataset.view === "login" ? "login" : "register";
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

  const box      = document.getElementById("courses");

  const editBtn  = document.getElementById("edit-courses");

  const saveBtn  = document.getElementById("save-courses");

  const nameInput= document.getElementById("profile-name");

  if (!cs || !box || !saveBtn || !editBtn) return;



  const storedName = getName();

  if (nameInput) nameInput.value = storedName;



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

    const nameValue = nameInput ? nameInput.value.trim() : storedName;

    setName(nameValue);

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



function buildGrid(lessons, weekStart = null, selectedKeys = null, timeColumnWidth = null) {

  hideLessonOverlay();

  const container = document.getElementById("timetable");

  container.innerHTML = "";



  const colorPrefs = ColorPrefs.load();

  applyThemeVars(colorPrefs.theme);

  const subjectColors = colorPrefs.subjects || {};

  const themeColors = { ...DEFAULT_THEME, ...(colorPrefs.theme || {}) };



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



  if (timeColumnWidth == null) {

    timeColumnWidth = Number(window.__timeColumnWidth) || 60;

  } else {

    timeColumnWidth = Number(timeColumnWidth);

    if (!Number.isFinite(timeColumnWidth) || timeColumnWidth <= 0) {

      timeColumnWidth = Number(window.__timeColumnWidth) || 60;

    }

    window.__timeColumnWidth = timeColumnWidth;

  }



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



  // Mon-Fri only + collect time boundaries

  const tset = new Set();

  const valid = [];

  const klausurenList = getAllKlausuren().filter(k => isWithinWeek(k.date));

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

  grid.style.gridTemplateColumns = `${timeColumnWidth}px repeat(5, minmax(0, 1fr))`;



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

    timeCell.textContent = `${fmtHM(startM)} - ${fmtHM(endM)}`;

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

    const subjKey = resolveCourseKey(subj) || normKey(subj || "");

    const subjColor = subjKey ? subjectColors[subjKey] : null;



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
        subtitle: klausur.subject ? `${klausur.subject} - ${datePretty}` : datePretty,
        meta
      }));
      const klausurColorKey = resolveCourseKey(klausur.subject || "") || subjKey;
      const klausurColor = klausurColorKey ? subjectColors[klausurColorKey] : null;
      if (klausurColor) {
        applyCardColor(card, klausurColor);
      } else {
        clearCardColor(card);
        if (themeColors.klausurBg) applyCardColor(card, themeColors.klausurBg);
      }

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
        subtitle: timeRange ? `${datePretty} - ${timeRange}` : datePretty,
        meta,
        note: l.note || ""
      }));
      if (subjColor) {
        applyCardColor(card, subjColor);
      } else {
        clearCardColor(card);
      }

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
    subtitle: k.subject ? `${k.subject} - ${datePretty}` : datePretty,
    meta
  }));
  const subjColor = examKey ? subjectColors[examKey] : null;
  if (subjColor) {
    applyCardColor(card, subjColor);
  } else {
    clearCardColor(card);
    if (themeColors.klausurBg) applyCardColor(card, themeColors.klausurBg);
  }

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



  vacationByDay.forEach((dayVacations, day) => {

    const vacCard = document.createElement("div");

    vacCard.className = "vacation-card";

    vacCard.style.gridColumn = String(day + 1);

    vacCard.style.gridRow = `2 / -1`;

    vacCard.style.zIndex = "5";

    const entries = dayVacations.map(v => {

      const range = v.start_date === v.end_date

        ? formatDate(v.start_date)

        : `${formatDate(v.start_date)} - ${formatDate(v.end_date)}`;

      return `<div class="vac-item"><strong>${escapeHtml(v.title)}</strong><span>${range}</span></div>`;

    }).join("");

    vacCard.innerHTML = `

      <div class="vac-title">Ferien</div>

      ${entries}

    `;

    grid.appendChild(vacCard);

  });



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
    await loadExams(force);

    await loadMappings();

    await loadCourseOptions();



    const res = await fetch(`/api/timetable?ts=${Date.now()}${force ? "&force=1":""}`, { cache: "no-store" });

    if (!res.ok) throw new Error(`/api/timetable ${res.status}`);

    const data = await res.json();

    let lessons = Array.isArray(data.lessons) ? data.lessons : [];

    if (data && data.settings) {
      const widthRaw = Number(data.settings.timeColumnWidth);
      if (Number.isFinite(widthRaw)) {
        window.__timeColumnWidth = Math.min(120, Math.max(40, Math.round(widthRaw)));
      }
    }

    const timeColumnWidth = Math.min(120, Math.max(40, Math.round(Number(window.__timeColumnWidth) || 60)));
    window.__timeColumnWidth = timeColumnWidth;



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

      lessons = lessons.filter((l) => lessonMatchesSelection(l, selectedSet));

    }



    lessons.sort((a, b) => {

      if (a.date !== b.date) return a.date.localeCompare(b.date);

      if (a.start !== b.start) return a.start.localeCompare(b.start);

      return mapSubject(a).localeCompare(mapSubject(b), "de");

    });



    buildGrid(lessons, typeof data.weekStart === "string" ? data.weekStart : null, window.__selectedCourseKeys, timeColumnWidth);

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

      window.location.reload();

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
      const enforceLogin = typeof isStandalone === "function" && isStandalone();
      if (enforceLogin) {
        await Auth.ensureAuthenticated();
      }

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
