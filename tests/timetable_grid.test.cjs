const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

// Exercise the real grid renderer without booting auth, push or network requests.
const app = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const gridSource = app.slice(app.indexOf("function isRemoteExamLesson("), app.indexOf("/* --- Fetch + refresh --- */"));

class Element {
  constructor() {
    this.children = [];
    this.style = {};
    this.className = "";
    this.innerHTML = "";
  }
  appendChild(child) { this.children.push(child); }
  addEventListener() {}
}

function render(lessons, weekStart, exams = [], writesExam = false) {
  const container = new Element();
  const context = vm.createContext({
    window: {},
    document: { getElementById: () => container, createElement: () => new Element() },
    hideLessonOverlay() {},
    ColorPrefs: { load: () => ({ theme: {}, subjects: {} }) },
    applyThemeVars() {},
    DEFAULT_THEME: {},
    updateWeekRangeLabel() {},
    toISODate: d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`,
    EXAMS: exams,
    getAllKlausuren: () => exams,
    examMatchesSelection: () => writesExam,
    inferPeriodFromTime: hm => hm === "07:55" ? 1 : 2,
    periodStartMinutes: p => p === 1 ? 475 : 550,
    periodEndMinutes: p => p === 1 ? 535 : 610,
    formatPeriodRange: () => "1-2",
    mapExamRooms: k => ({label: k.room}),
    bestExamKey: k => k.subject,
    _norm: s => s,
    dayIdxISO: d => new Date(`${d}T00:00:00`).getDay(),
    parseHM: hm => Number(hm.split(":")[0]) * 60 + Number(hm.split(":")[1]),
    PERIOD_NUMBERS: [1, 2],
    PERIOD_SCHEDULE: { 1: { start: "07:55", end: "08:55" }, 2: { start: "09:10", end: "10:10" } },
    vacationsOnDate: () => [],
    fmtHM: m => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`,
    WEEKDAYS: ["Mo", "Di", "Mi", "Do", "Fr"],
    mapSubject: l => l.subject,
    mapRoom: l => l.room || "",
    resolveCourseKey: s => s,
    normKey: s => s,
    formatDate: d => d,
    formatTimeRange: (s, e) => `${s} - ${e}`,
    escapeHtml: s => s,
    STATUS_LABELS: { entfaellt: "Entfaellt" },
    clearCardColor() {},
  });
  vm.runInContext(gridSource, context);
  context.buildGrid(lessons, weekStart);
  return container.children[0].children;
}

function lesson(date, status = "normal", subject = "EKEG8") {
  return { date, start: "09:10", end: "10:10", subject, status, grade: "Q1" };
}

const cards = nodes => nodes.filter(n => n.className.startsWith("lesson "));

test("next Monday does not overlay this Monday's cancelled Q1 EKEG8", () => {
  const nodes = render([
    lesson("2026-09-07", "entfaellt"),
    lesson("2026-09-09"),
    lesson("2026-09-14"),
  ], "2026-09-07");
  const monday = cards(nodes).filter(n => n.style.gridColumn === "2");
  assert.equal(monday.length, 1);
  assert.equal(monday[0].className, "lesson entfaellt");
  assert.match(monday[0].innerHTML, /EKEG8/);
  assert.match(monday[0].innerHTML, /Entfaellt/);
  assert.equal(cards(nodes).length, 2);
});

test("cached lessons outside the displayed week never fill empty weekdays", () => {
  const nodes = render([
    lesson("2026-08-31"), lesson("2026-09-06"),
    lesson("2026-09-12"), lesson("2026-09-13"), lesson("2026-09-14"),
  ], "2026-09-07");
  assert.equal(cards(nodes).length, 0);
  assert.equal(nodes.filter(n => n.className === "placeholder-day").length, 5);
});

test("navigating to the next week shows its own normal lesson", () => {
  const nodes = render([
    lesson("2026-09-07", "entfaellt"), lesson("2026-09-14"),
  ], "2026-09-14");
  assert.equal(cards(nodes).length, 1);
  assert.equal(cards(nodes)[0].className, "lesson normal");
});

test("week filtering also works across the year boundary", () => {
  const nodes = render([
    lesson("2026-12-28", "entfaellt"), lesson("2027-01-01"), lesson("2027-01-04"),
  ], "2026-12-28");
  assert.equal(cards(nodes).length, 2);
  assert.equal(cards(nodes)[0].className, "lesson entfaellt");
  assert.equal(cards(nodes)[1].style.gridColumn, "6");
});

const exam = {id: "exam-1116", source: "remote", grade: "Q1", date: "2026-10-02",
  subject: "GEEG8", name: "GEEG8/1116", startTime: "07:55", endTime: "10:10",
  periodStart: 1, periodEnd: 2, rooms: ["AULA"], room: "AULA"};
const cancelled = {...lesson(exam.date, "entfaellt", "GEEG8"), start: "07:55", end: "08:55", room: "A-K22"};
const booking = {...cancelled, status: "normal", room: "Aula"};
const booking2 = {...booking, start: "09:10", end: "10:10"};

test("GEEG8 non-writer sees cancellation, not either Aula exam booking", () => {
  const result = cards(render([cancelled, booking, booking2], "2026-09-28", [exam]));
  assert.equal(result.length, 1);
  assert.equal(result[0].className, "lesson entfaellt");
  assert.doesNotMatch(result[0].innerHTML, /Aula|AULA/);
});

test("GEEG8 writer sees exactly one exam in Aula", () => {
  const result = cards(render([cancelled, booking, booking2], "2026-09-28", [exam], true));
  assert.equal(result.length, 1);
  assert.equal(result[0].className, "lesson klausur");
  assert.match(result[0].innerHTML, /AULA/);
});

test("standalone exam booking is rendered only for writers", () => {
  assert.equal(cards(render([booking, booking2], "2026-09-28", [exam])).length, 0);
  assert.equal(cards(render([booking, booking2], "2026-09-28", [exam], true)).length, 1);
});

test("ordinary parallel lesson remains for non-writers", () => {
  const regular = {...cancelled, status: "normal"};
  const result = cards(render([regular, booking], "2026-09-28", [exam]));
  assert.equal(result.length, 1);
  assert.equal(result[0].className, "lesson normal");
});

test("no inference from unrelated, manual, missing or out-of-time exams", () => {
  for (const exams of [[], [{...exam, grade: "Q2"}], [{...exam, subject: "GE G4"}],
    [{...exam, source: "manual"}], [{...exam, date: "2026-10-01"}],
    [{...exam, startTime: "09:10"}], [{...exam, room: "B35", rooms: ["B35"]}]]) {
    assert.equal(cards(render([booking], "2026-09-28", exams)).length, 1);
  }
});
