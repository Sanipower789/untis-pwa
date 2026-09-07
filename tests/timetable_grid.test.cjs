const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

// Exercise the real grid renderer without booting auth, push or network requests.
const app = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const gridSource = app.slice(app.indexOf("function buildGrid("), app.indexOf("/* --- Fetch + refresh --- */"));

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

function render(lessons, weekStart) {
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
    getAllKlausuren: () => [],
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
