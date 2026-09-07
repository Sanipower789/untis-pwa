const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../static/sw.js'), 'utf8');

async function offlineRequest(method, pathname) {
  const handlers = {};
  const ctx = vm.createContext({
    URL, Response,
    self: { addEventListener: (name, cb) => { handlers[name] = cb; }, location: { origin: 'https://untis.test' } },
    fetch: async () => { throw new Error('offline'); },
    caches: { match: async () => undefined },
  });
  vm.runInContext(source, ctx);
  let response;
  handlers.fetch({ request: { url: 'https://untis.test' + pathname, method }, respondWith: p => { response = p; } });
  return response;
}

test('offline API reads and writes never report successful empty data', async () => {
  for (const [method, url] of [['GET', '/api/auth/status'], ['GET', '/api/timetable'], ['PUT', '/api/profile'], ['POST', '/api/admin/save'], ['DELETE', '/api/push/subscription']]) {
    const response = await offlineRequest(method, url);
    assert.equal(response.status, 503, `${method} ${url}`);
    assert.equal((await response.json()).ok, false);
  }
});
