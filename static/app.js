// static/app.js

// ===== Basics =====
const WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr"];
const LS_COURSES = "myCourses";
const LS_NAME = "myName";

const getCourses = () => JSON.parse(localStorage.getItem(LS_COURSES) || "[]");
const setCourses = (arr) => localStorage.setItem(LS_COURSES, JSON.stringify(arr || []));
const getName = () => localStorage.getItem(LS_NAME) || "";
const setName = (v) => localStorage.setItem(LS_NAME, v || "");

// hh:mm <-> Minuten
function parseHM(t){ const [h,m] = t.split(":").map(Number); return h*60+m; }
function fmtHM(mins){ const h=Math.floor(mins/60), m=mins%60; return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}`; }
function dayIdxISO(iso){ const d=new Date(iso); const g=d.getDay(); return g===0?7:g; }

// ===== Kursauswahl =====
function buildCourseSelection(allLessons){
  const nameInput = document.getElementById("profile-name");
  if (nameInput && !nameInput.dataset.init){
    nameInput.value = getName();
    nameInput.dataset.init="1";
  }

  const box = document.getElementById("courses");
  if (!box) return;
  box.innerHTML = "";

  const subjects = [...new Set(allLessons.map(l => l.subject).filter(Boolean))]
    .sort((a,b)=>a.localeCompare(b,"de"));
  const saved = new Set(getCourses());

  subjects.forEach(sub=>{
    const id = "sub_"+sub.replace(/\W+/g,"_");
    const label = document.createElement("label");
    label.className = "chk";
    label.innerHTML = `<input type="checkbox" id="${id}" value="${sub}" ${saved.has(sub)?"checked":""}> <span>${sub}</span>`;
    box.appendChild(label);
  });

  const saveBtn = document.getElementById("save-courses");
  const editBtn = document.getElementById("edit-courses");

  if (saveBtn && !saveBtn.dataset.bound){
    saveBtn.dataset.bound = "1";
    saveBtn.onclick = ()=>{
      const selected = [...box.querySelectorAll("input:checked")].map(i=>i.value).slice(0,12);
      setCourses(selected);
      setName(nameInput.value.trim());
      document.getElementById("course-selection").style.display = "none";
      if (editBtn) editBtn.style.display = "inline-block";
      loadTimetable(true);
    };
  }
  if (editBtn && !editBtn.dataset.bound){
    editBtn.dataset.bound = "1";
    editBtn.onclick = ()=>{
      document.getElementById("course-selection").style.display = "block";
      editBtn.style.display = "none";
    };
  }
}

// ===== Grid helpers (mit dynamischen Zeilenhöhen) =====

// times = z.B. [475, 515, 520, 620, ...] (Minuten ab 00:00)
function computeRowHeights(times){
  // Regel: Pausen (<= 15 Minuten) -> 36px; sonst 72px
  const out = [];
  for (let i=0; i<times.length-1; i++){
    const dur = times[i+1] - times[i];
    out.push(dur <= 15 ? 36 : 72);
  }
  return out;
}
function indexOfTime(mins, times){
  // exakter Treffer
  for (let i=0;i<times.length;i++) if (times[i]===mins) return i;
  // falls nicht exakt: nächst-kleineren Slot
  const idx = times.findIndex(t=>t>mins);
  return Math.max(0, idx-1);
}

// ===== Stundenplan rendern =====
function buildGrid(lessons){
  const container = document.getElementById("timetable");
  container.innerHTML = "";

  // Nur Mo–Fr
  const valid = [];
  const tset = new Set();
  lessons.forEach(l=>{
    const d = dayIdxISO(l.date);
    if (d>=1 && d<=5){
      valid.push(l);
      tset.add(parseHM(l.start));
      tset.add(parseHM(l.end));
    }
  });

  const times = [...tset].sort((a,b)=>a-b);
  if (times.length < 2){
    container.innerHTML = "<p class='muted'>Keine Einträge.</p>";
    return;
  }

  // Dynamische Zeilenhöhen erzeugen und ins Grid schreiben
  const rowHeights = computeRowHeights(times); // Länge = times.length-1
  const grid = document.createElement("div");
  grid.className = "grid";
  // 1 Header-Reihe (48px) + alle Zeilen laut rowHeights
  grid.style.gridTemplateRows = `48px ${rowHeights.map(h=>h+"px").join(" ")}`;

  // Header-Spalten
  const corner = document.createElement("div");
  corner.className = "hdr corner";
  corner.textContent = "Zeit";
  grid.appendChild(corner);
  for (let d=1; d<=5; d++){
    const h = document.createElement("div");
    h.className = "hdr day";
    h.textContent = WEEKDAYS[d-1];
    h.style.gridColumn = String(d+1);
    h.style.gridRow = "1";
    grid.appendChild(h);
  }

  // Zeitspalte + Slots
  for (let i=0; i<times.length-1; i++){
    const timeCell = document.createElement("div");
    timeCell.className = "timecell";
    timeCell.textContent = `${fmtHM(times[i])}–${fmtHM(times[i+1])}`;
    timeCell.style.gridColumn = "1";
    timeCell.style.gridRow = String(i+2);
    grid.appendChild(timeCell);

    for (let d=1; d<=5; d++){
      const slot = document.createElement("div");
      slot.className = "slot";
      slot.style.gridColumn = String(d+1);
      slot.style.gridRow = String(i+2);
      grid.appendChild(slot);
    }
  }

  // Lessons platzieren (jetzt passt r1 - r0 exakt, weil die Row-Höhen „echt“ sind)
  valid.forEach(l=>{
    const day = dayIdxISO(l.date);
    const s = parseHM(l.start), e=parseHM(l.end);
    const r0 = indexOfTime(s, times);
    const r1 = indexOfTime(e, times);
    const span = Math.max(1, (r1 - r0) || 1);

    const card = document.createElement("div");
    card.className = `lesson ${l.status}`;
    card.style.gridColumn = String(day + 1);
    card.style.gridRow = `${r0 + 2} / span ${span}`;

    const badge =
      l.status === "entfaellt" ? "🟥 Entfällt" :
      l.status === "vertretung" ? "⚠️ Vertretung" :
      l.status === "aenderung" ? "🟦 Änderung" : "";

    // Zeit NICHT anzeigen (du wolltest nur links die Zeiten haben)
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

// ===== Fetch & Orchestrate =====
async function loadTimetable(force=false){
  const res = await fetch("/api/timetable", {cache: "no-store"});
  const data = await res.json();
  let lessons = data.lessons || [];

  if (!document.getElementById("course-selection").dataset.init){
    buildCourseSelection(lessons);
    document.getElementById("course-selection").dataset.init = "1";
  }

  const saved = getCourses();
  if (saved.length>0){
    const cs = document.getElementById("course-selection");
    const editBtn = document.getElementById("edit-courses");
    if (cs) cs.style.display = "none";
    if (editBtn) editBtn.style.display = "inline-block";
    lessons = lessons.filter(l => saved.includes(l.subject));
  }

  // sort stabil
  lessons.sort((a,b)=>{
    if (a.date!==b.date) return a.date.localeCompare(b.date);
    if (a.start!==b.start) return a.start.localeCompare(b.start);
    return (a.subject||"").localeCompare(b.subject||"");
  });

  buildGrid(lessons);
}

// ===== Init & Auto-Refresh =====
document.addEventListener("DOMContentLoaded", ()=>{
  loadTimetable();
  // optional: alle 5 Minuten neu laden (Server pollt eh periodisch)
  setInterval(()=>loadTimetable(), 5*60*1000);
});
