const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

test('course color keys keep the grade prefix used by lesson rendering', () => {
  const ctx = vm.createContext({ normKey: s => s.toLowerCase(), normaliseHex: s => s });
  vm.runInContext(app.slice(app.indexOf('const mergeSubjects ='), app.indexOf('const shadeHex =')) + '; globalThis.merge = mergeSubjects;', ctx);
  const colors = ctx.merge({ 'Q1:biology': '#ff0000', 'q2:biology': '#00ff00' });
  assert.equal(colors['Q1:biology'], '#ff0000');
  assert.equal(colors['Q2:biology'], '#00ff00');
});

test('disable notifications stays disabled despite granted browser permission', async () => {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      events: {}, classList: { toggle() {} }, setAttribute() {}, getAttribute: () => 'true',
      addEventListener(name, cb) { this.events[name] = cb; },
    });
    return elements.get(id);
  };
  const storage = new Map();
  let current;
  let subscribeCalls = 0;
  const subscription = { endpoint: 'https://push.example.test/one', toJSON: () => ({}), unsubscribe: async () => { current = null; return true; } };
  current = subscription;
  const registration = { pushManager: { getSubscription: async () => current, subscribe: async () => { subscribeCalls++; current = subscription; return subscription; } } };
  const ctx = vm.createContext({
    document: { getElementById: element, addEventListener() {} },
    window: { isSecureContext: true, PushManager: {}, Notification: {}, setTimeout: () => 1, clearTimeout() {} },
    navigator: { serviceWorker: { getRegistration: async () => registration, ready: Promise.resolve(registration) } },
    Notification: { permission: 'granted' }, Auth: { username: () => 'tester' },
    localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    fetch: async () => ({ ok: true, json: async () => ({ ok: true, configured: true, publicKey: 'AQ', endpointHashes: [''] }) }),
    atob: s => Buffer.from(s, 'base64').toString('binary'), console,
  });
  const source = app.slice(app.indexOf('const PushNotifications ='), app.indexOf('const Auth ='));
  vm.runInContext(source + '; globalThis.push = PushNotifications;', ctx);
  ctx.push.init();
  ctx.push.setAuthenticated(true);
  await tick();
  await element('push-disable').events.click();
  await ctx.push.refresh();
  assert.equal(subscribeCalls, 0);
  assert.equal(element('push-enable').hidden, false);
  assert.match(element('push-status').textContent, /deaktiviert/);
  await element('push-enable').events.click();
  assert.equal(subscribeCalls, 1);
  assert.equal(element('push-disable').hidden, false);
});

function timetableContext() {
  const pending = new Map();
  const rendered = [];
  const ctx = vm.createContext({
    window: {}, URLSearchParams, console: { error() {} },
    Auth: { isLoggedIn: () => true, username: () => 'tester' },
    loadVacations: async () => {}, loadExams: async () => {}, loadMappings: async () => {}, loadCourseOptions: async () => {},
    defaultWeekStartIso: () => '2026-09-07',
    fetch: url => new Promise(resolve => pending.set(new URL(url, 'http://test').searchParams.get('weekStart'), resolve)),
    document: { getElementById: () => ({ dataset: { init: '1' } }) },
    getCourses: () => ['Q1:biology'], normaliseCourseSelection: values => ({ keys: values }),
    filterLessonsForProfile: lessons => lessons, mapSubject: l => l.subject,
    renderUpdateBanner() {}, buildGrid: (lessons, week) => rendered.push(week),
    populateKlausurSubjects() {}, populateKlausurPeriods() {}, renderKlausurList() {}, updateWeekRangeLabel() {},
  });
  vm.runInContext(app.slice(app.indexOf('/* --- Fetch + refresh --- */'), app.indexOf('/* --- service worker auto-update glue --- */')), ctx);
  return { ctx, pending, rendered };
}

test('slower previous-week response cannot overwrite newer week navigation', async () => {
  const { ctx, pending, rendered } = timetableContext();
  const old = ctx.loadTimetable(false, '2026-09-07');
  await tick();
  const next = ctx.loadTimetable(false, '2026-09-14');
  await tick();
  pending.get('2026-09-14')({ ok: true, json: async () => ({ ok: true, weekStart: '2026-09-14', lessons: [] }) });
  await next;
  pending.get('2026-09-07')({ ok: true, json: async () => ({ ok: true, weekStart: '2026-09-07', lessons: [] }) });
  await old;
  assert.deepEqual(rendered, ['2026-09-14']);
});

test('failed timetable response is not displayed as an empty successful week', async () => {
  const { ctx, pending, rendered } = timetableContext();
  const loading = ctx.loadTimetable(false, '2026-09-07');
  await tick();
  pending.get('2026-09-07')({ ok: true, json: async () => ({ ok: false, weekStart: '2026-09-07', lessons: [] }) });
  await loading;
  assert.deepEqual(rendered, []);
});
