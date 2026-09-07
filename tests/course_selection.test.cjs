const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function setup(options = []) {
  const ctx = vm.createContext({
    PROFILE_GRADES: new Set(['EF', 'Q1', 'Q2']), getGrade: () => 'Q1',
    _norm: s => String(s || '').trim().toLowerCase(),
    normKey: s => String(s || '').trim().toLowerCase(),
  });
  vm.runInContext(app.slice(app.indexOf('const gradeFromKey ='), app.indexOf('/* ===== Sidebar (Klausuren) ===== */')), ctx);
  vm.runInContext('let COURSE_MAP_BY_GRADE = { EF: {}, Q1: {}, Q2: {} };' + app.slice(app.indexOf('function lookup('), app.indexOf('function examCourseKeys')), ctx);
  vm.runInContext('globalThis.filterLessonsForProfile = filterLessonsForProfile;', ctx);
  ctx.registerCourseOptions(options);
  return ctx;
}

test('a grade-specific alias never resolves to another grade', () => {
  const ctx = setup([{ key: 'EF:biology', label: 'BI G1', grade: 'EF' }]);
  assert.equal(ctx.resolveCourseKey('BI G1', 'Q1'), null);
  assert.equal(ctx.resolveCourseKey('Q1:BI G1', 'Q1'), null);
});

test('ambiguous labels in the same grade are not silently assigned', () => {
  const ctx = setup([
    { key: 'Q1:biology 1', label: 'Biology', grade: 'Q1' },
    { key: 'Q1:biology 2', label: 'Biology', grade: 'Q1' },
  ]);
  assert.equal(ctx.resolveCourseKey('Biology', 'Q1'), null);
  assert.equal(ctx.resolveCourseKey('Q1:biology 2', 'Q1'), 'Q1:biology 2');
});

test('unavailable or incomplete catalogs never erase saved course keys', () => {
  for (const options of [[], [{ key: 'EF:biology', label: 'BI G1', grade: 'EF' }]]) {
    const ctx = setup(options);
    const values = ['Q1:biology', 'Q2:math'];
    const result = ctx.normaliseCourseSelection(values);
    assert.deepEqual(Array.from(result.keys), values);
    assert.equal(result.changed, false);
  }
});

test('explicit saved grade wins over profile grade during migration', () => {
  const ctx = setup([
    { key: 'Q1:biology', label: 'BI G1', grade: 'Q1' },
    { key: 'Q2:biology', label: 'BI G1', grade: 'Q2' },
  ]);
  assert.equal(ctx.resolveCourseKey('Q2:BI G1', 'Q1'), 'Q2:biology');
});

test('cancelled lessons remain visible even when their course is not selected', () => {
  const ctx = setup([{ key: 'Q1:biology', label: 'Biology', grade: 'Q1' }]);
  const lessons = [
    { grade: 'Q1', subject: 'Biology', status: 'normal' },
    { grade: 'Q1', subject: 'Chemistry', status: 'entfaellt' },
    { grade: 'Q2', subject: 'Chemistry', status: 'entfaellt' },
  ];
  assert.deepEqual(
    ctx.filterLessonsForProfile(lessons, new Set(['Q1:biology'])),
    [lessons[0], lessons[1]],
  );
});
