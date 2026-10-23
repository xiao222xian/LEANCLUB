// Exercise the actual identity/API portion of site-portal.js with synthetic sessions only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/creator/site-portal.js', 'utf8');
const apiSource = source.slice(0, source.indexOf('  const ready =')) + '\n  const memberDialog = { open:false }; function renderLogin() {} window.client = {api,setToken,signed:()=>!!token};\n})();';
function client(fetchImpl, signed = true) {
  const storage = new Map(signed ? [['leanclub.member.session.v1', 'synthetic-session-one']] : []), events = [];
  const context = { window: { dispatchEvent: event => events.push(event.type) }, document: { querySelectorAll: () => [] },
    sessionStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    CustomEvent: class { constructor(type) { this.type = type; } }, AbortController, setTimeout, clearTimeout, fetch: fetchImpl };
  vm.createContext(context); vm.runInContext(apiSource, context);
  return { ...context.window.client, storage, events };
}
const response = (data, code = 0, status = 200) => ({ ok: status === 200, status, json: async () => ({ data, code, msg: 'synthetic response' }) });
(async () => {
  let request;
  const success = client(async (url, options) => { request = { url, options }; return response({ list: [] }); });
  await success.api('/leanclub/orders/page?pageNo=1&pageSize=20&status=all', { auth: true });
  assert.equal(request.options.credentials, 'omit'); assert.equal(request.options.cache, 'no-store');
  assert.equal(request.options.headers.Authorization, 'Bearer synthetic-session-one'); assert.doesNotMatch(request.url, /token|synthetic-session/);
  let unauthenticatedCalls = 0;
  const anonymous = client(async () => { unauthenticatedCalls++; }, false);
  await assert.rejects(anonymous.api('/leanclub/orders/page', { auth: true }), error => error.code === 401);
  assert.equal(unauthenticatedCalls, 0);

  let started, abortObserved = false;
  const pendingStarted = new Promise(resolve => { started = resolve; });
  const aborted = client((_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => { abortObserved = true; const error = new Error('aborted'); error.name = 'AbortError'; reject(error); }); started();
  }));
  const pending = aborted.api('/leanclub/orders/get?id=' + '2'.repeat(32), { auth: true });
  await pendingStarted; aborted.setToken('');
  await assert.rejects(pending, error => error.code === 'IDENTITY_CHANGED');
  assert.equal(abortObserved, true); assert.equal(aborted.storage.size, 0); assert.deepEqual(aborted.events, ['leanclub:identity']);

  let finish;
  const late = client(() => new Promise(resolve => { finish = resolve; }));
  const olderAccount = late.api('/leanclub/orders/page', { auth: true });
  late.setToken('synthetic-session-two'); finish(response({ privateOrder: true }));
  await assert.rejects(olderAccount, error => error.code === 'IDENTITY_CHANGED');

  const expired = client(async () => response(null, 401));
  await assert.rejects(expired.api('/leanclub/orders/page', { auth: true }));
  assert.equal(expired.signed(), false); assert.equal(expired.storage.size, 0); assert.deepEqual(expired.events, ['leanclub:identity']);
  console.log('PASS: actual member API client uses Bearer/no cookies/no-store, rejects anonymous private reads, aborts identity-bound requests, rejects late account results and clears expired sessions.');
})().catch(error => { console.error(error); process.exitCode = 1; });
