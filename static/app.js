// ===== Constants & helpers =====
const WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr"];
const LS_COURSES = "myCourses";
const LS_NAME = "myName";
const LS_INSTALLED_OK = "pwaInstalledOK";

const getCourses = () => JSON.parse(localStorage.getItem(LS_COURSES) || "[]");
const setCourses = (arr) => localStorage.setItem(LS_COURSES, JSON.stringify(arr || []));
const getName = () => localStorage.getItem(LS_NAME) || "";
const setName = (v) => localStorage.setItem(LS_NAME, v || "");

function parseHM(t) { const [h, m] = t.split(":").map(Number); return h * 60 + m; }
function fmtHM(mins) { const h = Math.floor(mins / 60), m = mins % 60; return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}`; }
function dayIdxISO(iso) { const d = new Date(iso); const g = d.getDay(); return g === 0 ? 7 : g; }
const isStandalone = () =>
  window.matchMedia && window.matchMedia('(display-mode: standalone)').matches ||
  window.navigator.standalone === true;

// ===== INSTALL GATE =====
let deferredPrompt = null;
function showInstallGate() {
  const gate = document.getElementById("install-gate");
  gate.style.display = "flex";
}
function hideInstallGate() {
  const gate = document.getElementById("install-gate");
  gate.style.display = "none";
}
function checkInstalledOrBypass() {
  const url = new URL(window.location.href);
  const devBypass = url.searchParams.get("dev") === "1";
  if (devBypass) localStorage.setItem(LS_INSTALLED_OK, "1");

  if (localStorage.getItem(LS_INSTALLED_OK) === "1" || isStandalone()) {
    hideInstallGate();
    return true;
  }
  showInstallGate();
  return false;
}

// capture beforeinstallprompt (Chrome/Android)
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
  const btn = document.getElementById("btn-install");
  if (btn) btn.style.display = "inline-block";
});

window.addEventListener('appinstalled', () => {
  localStorage.setItem(LS_INSTALLED_OK, "1");
  hideInstallGate();
  // Nach Install neu laden, damit SW/manifest sauber greifen
  setTimeout(() => location.reload(), 500);
});

// Gate buttons
function wireGateButtons() {
  const btnInstall = document.getElementById("btn-install");
  const btnCheck   = document.getElementById("btn-check");
  const btnDev     = document.getElementById("btn-dev");
  if (btnInstall) {
    btnInstall.onclick = async () => {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      try { await deferredPrompt.userChoice; } catch(_) {}
      deferredPrompt = null;
    };
  }
  if (btnCheck) {
    btnCheck.onclick = () => {
      if (isStandalone()) {
        localStorage.setItem(LS_INSTALLED_OK, "1");
        hideInstallGate();
        location.reload();
      } else {
        alert("Noch nicht installiert erkannt. Bitte wirklich zum Home-Bildschirm hinzufügen.");
      }
    };
  }
  // dev bypass link sichtbar machen wenn ?dev im URL vorhanden ist
  if (new URL(location.href).searchParams.get("dev") === "1" && btnDev) {
    btnDev.style.display = "inline-block";
    btnDev.onclick = (e) => {
      e.preventDefault();
      localStorage.setItem(LS_INSTALLED_OK, "1");
      hideInstallGate();
      location.reload();
    };
  }
}

// ===== Course selection =====
function buildCourseSelection(allLessons) {
  const nameInput = document.getElementById("profile-name");
  if (nameInput && !nameInput.dataset.init) {
    nameInput.value = getName();
    nameInput.dataset.init = "1";
  }

  const box = document.getElementById("courses");
  if (!box) return;
  box.innerHTML = "";

  const subjects = [...new Set(allLessons.map(l => l.subject).filter(Boolean))]
    .sort((a,b)=>a.localeCompare(b,"de"));
  const saved = new Set(getCourses());

  subjects.forEach(sub => {
    const id = "sub_" + sub.replace(/\W+/g, "_");
    const label = document.createElement("label");
    label.className = "chk";
    label.innerHTML = `<input type="checkbox" id="${id}" value="${sub}" ${saved.has(sub) ? "checked" : ""}> <span>${sub}</span>`;
    box.appendChild(label);
  });

  const saveBtn = document.getElementById("save-courses");
  const editBtn = document.getElementById("edit-courses");

  if (saveBtn && !saveBtn.dataset.bound) {
    saveBtn.dataset.bound = "1";
    saveBtn.onclick = () => {
      const selected = [...box.querySelectorAll("input:checked")].map(i => i.value).slice(0, 12);
      setCourses(selected);
      setName(nameInput.value.trim());
      document.getElementById("course-selection").style.display = "none";
      if (editBtn) editBtn.style.display = "inline-block";
      loadTimetable(true);
    };
  }

  if (editBtn && !editBtn.dataset.bound) {
    editBtn.dataset.bound = "1";
    editBtn.onclick = () => {
      document.getElementById("course-selection").style.display = "block";
      editBtn.style.display = "none";
      window.scrollTo({top:0, behavior:"smooth"});
    };
  }
}

// ===== Grid render =====
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

  const grid = document.createElement("div");
  grid.className = "grid";

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

  // Zeit- & Slot-Reihen
  for (let i = 0; i < times.length - 1; i++) {
    const startM = times[i], endM = times[i+1];
    const duration = endM - startM; // in Minuten
    const isBreak = duration <= 20; // Heuristik: kurze Intervalle = Pause

    const timeCell = document.createElement("div");
    timeCell.className = "timecell";
    if (isBreak) timeCell.classList.add("breakrow");
    timeCell.textContent = `${fmtHM(startM)}–${fmtHM(endM)}`;
    timeCell.style.gridColumn = "1";
    timeCell.style.gridRow = String(i + 2);
    grid.appendChild(timeCell);

    for (let d = 1; d <= 5; d++) {
      const slot = document.createElement("div");
      slot.className = "slot";
      if (isBreak) slot.classList.add("breakrow");
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

  // Lessons platzieren
  valid.forEach(l => {
    const day = dayIdxISO(l.date);
    const s = parseHM(l.start), e = parseHM(l.end);
    const r0 = rowIndexFor(s), r1 = rowIndexFor(e);
    const span = Math.max(1, r1 - r0);

    const card = document.createElement("div");
    card.className = `lesson ${l.status}`;
    card.style.gridColumn = String(day + 1);
    card.style.gridRow = `${r0 + 2} / span ${span}`;

    const badge =
      l.status === "entfaellt" ? "🟥 Entfällt" :
      l.status === "vertretung" ? "⚠️ Vertretung" :
      l.status === "aenderung" ? "🟦 Änderung" : "";

    // Zeit links ausblenden – nur Raum/Lehrer/Badge/Notiz anzeigen
    card.innerHTML = `
      <div class="lesson-title">${l.subject || "—"}</div>
      <div class="lesson-meta">
        ${l.teacher ? `<span>· ${l.teacher}</span>` : ""}
        ${l.room ? `<span>· ${l.room}</span>` : ""}
      </div>
      ${badge ? `<div class="badge">${badge}</div>` : ""}
      ${l.note ? `<div class="note">${l.note}</div>` : ""}
    `;
    grid.appendChild(card);
  });

  container.appendChild(grid);
}

// ===== Fetch + orchestrate =====
let refreshTimer = null;

async function loadTimetable(force = false) {
  // Abbrechen, wenn Install-Zwang nicht erfüllt
  if (!checkInstalledOrBypass()) return;

  const res = await fetch("/api/timetable");
  const data = await res.json();
  let lessons = data.lessons || [];

  // Auswahl initial aufbauen
  const cs = document.getElementById("course-selection");
  if (!cs.dataset.init) {
    buildCourseSelection(lessons);
    cs.dataset.init = "1";
  }

  // Wenn Kurse gespeichert → Auswahl verstecken
  const saved = getCourses();
  const editBtn = document.getElementById("edit-courses");
  if (saved.length > 0) {
    cs.style.display = "none";
    if (editBtn) editBtn.style.display = "inline-block";
    lessons = lessons.filter(l => saved.includes(l.subject));
  } else {
    cs.style.display = "block";
    if (editBtn) editBtn.style.display = "none";
  }

  // Sortierung
  lessons.sort((a,b) => {
    if (a.date !== b.date) return a.date.localeCompare(b.date);
    if (a.start !== b.start) return a.start.localeCompare(b.start);
    return (a.subject || "").localeCompare(b.subject || "");
  });

  buildGrid(lessons);

  // Auto-Refresh (alle 5 Minuten)
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => loadTimetable(true), 5 * 60 * 1000);
}

// ===== Init =====
document.addEventListener("DOMContentLoaded", () => {
  wireGateButtons();
  loadTimetable();
});
