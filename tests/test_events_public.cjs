const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = process.cwd();
class Element {
  constructor(id = '') { this.id = id; this.hidden = false; this.textContent = ''; this.value = ''; this.checked = false; this.handlers = {}; this.attributes = {}; this.children = []; this.open = false; this.classList = { add() {}, remove() {}, toggle() {} }; }
  addEventListener(event, handler) { this.handlers[event] = handler; }
  async fire(event, extra = {}) { return this.handlers[event]?.({ preventDefault() {}, target: this, ...extra }); }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; if (name === 'src') this.src = ''; }
  append(...children) { this.children.push(...children); }
  prepend(...children) { this.children.unshift(...children); }
  replaceChildren(...children) { this.children = children; }
  focus() { this.focused = true; }
  set innerHTML(_) { throw Error('Unsafe HTML insertion'); }
  querySelector() { return new Element(); }
  showModal() { this.open = true; }
  close() { this.open = false; }
}
const event = { id: 'test', title: '<img src=x onerror=alert(1)>', subtitle: 'Test only', description: 'Plan', city: 'shenzhen', posterCity: 'zhuhai', venue: 'Test venue', address: 'Test address', startsAt: '2030-10-22T10:00:00Z', endsAt: '2030-10-22T14:00:00Z', registrationClosesAt: '2030-10-22T09:00:00Z', priceFen: 12900, status: 'published', remaining: 6, capacity: 10, registrationOpen: true, paymentReady: true };
function ticket(status = 'paid') { return { event, registration: { name: '测试昵称', phoneMasked: '138****0000', status: 'registered', ticketNumber: 'LC-TESTONLY' }, order: { id: 'test-order-0001', status, amountFen: 12900, expiresAt: '2030-10-22T09:00:00Z' }, orderToken: 'order-test-token', ticketToken: status === 'paid' ? 'checkin-token' : null, qrDataUrl: 'data:image/png;base64,AAAA', canCancel: ['paid', 'pending_payment'].includes(status) }; }
function boot(page, api) {
  const html = fs.readFileSync(`${root}/web/creator/${page}/index.html`, 'utf8');
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]); assert.equal(ids.length, new Set(ids).size, 'no duplicate DOM ids');
  const elements = Object.fromEntries(ids.map(id => [id, new Element(id)])); const assigned = [];
  const location = { hash: '#ticket=order-test-token', search: '', href: 'http://localhost/events', assign(value) { assigned.push(value); } };
  const context = { window: { addEventListener() {} }, document: { getElementById(id) { assert.ok(elements[id], `DOM id exists: ${id}`); return elements[id]; }, createElement: () => new Element(), addEventListener() {}, visibilityState: 'visible', body: { classList: { add() {}, remove() {} } }, fonts: { ready: Promise.resolve() } }, URL, URLSearchParams, location, history: { replaceState() {} }, setTimeout: () => 1, clearTimeout() {}, Intl, Date, console };
  let counter = 0;
  context.window.LeanEvents = { api, randomId: () => `request-${++counter}`, date: value => value, poster: e => `/city-vote/assets/alan-v1/${e.posterCity || e.city}.webp`, dialog: element => ({ open: () => element.showModal(), close: () => element.close() }), ticketUrl: value => `/ticket#ticket=${encodeURIComponent(value)}`, text: (tag, value, className) => { const node = new Element(); node.textContent = value; node.className = className; return node; }, cityNames: { shenzhen: '深圳', zhuhai: '珠海', guangzhou: '广州' }, money: value => `¥${value / 100}` };
  vm.createContext(context); vm.runInContext(fs.readFileSync(`${root}/web/creator/${page}/app.js`, 'utf8'), context);
  return { elements, assigned, context };
}
async function flush() { for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve)); }
(async () => {
  for (const state of ['pending_payment', 'paid', 'expired', 'cancelled', 'refund_pending', 'refunded']) {
    const response = ticket(state); const { elements } = boot('ticket', async () => response); await flush();
    assert.equal(elements['ticket-qr-area'].hidden, state !== 'paid', `${state} QR gating`);
    assert.equal(elements['save-ticket'].hidden, state !== 'paid', `${state} save gating`);
    if (state !== 'paid') assert.equal(elements['ticket-qr'].src, '', `${state} must remove QR src`);
    assert.equal(elements['ticket-price'].textContent, '¥129');
  }
  for (const invalid of [{ event: { ...event, status: 'cancelled' } }, { registration: { ...ticket().registration, status: 'cancelled' } }, { qrDataUrl: 'https://external.example/qr' }, { order: null }]) {
    const { elements } = boot('ticket', async () => ({ ...ticket(), ...invalid })); await flush(); assert.equal(elements['ticket-qr-area'].hidden, true, 'invalid ticket must not display QR');
  }
  const used = boot('ticket', async () => ({ ...ticket(), registration: { ...ticket().registration, status: 'checked_in', checkedInAt: '2030-10-22T10:00:00Z' } })); await flush(); assert.equal(used.elements['ticket-state'].textContent, '已入场'); assert.equal(used.elements['checkin-time'].hidden, false);
  const unavailable = boot('events', async path => path.endsWith('/mine') ? { tickets: [] } : { events: [{ ...event, paymentReady: false, registrationOpen: false }] }); await flush(); assert.equal(unavailable.elements['open-register'].disabled, true); assert.equal(unavailable.elements['open-register'].textContent, '报名即将开放');
  const calls = []; let fail = true;
  const flow = boot('events', async (path, body) => { if (path.endsWith('/mine')) return { tickets: [] }; if (path.endsWith('/register')) { calls.push(body); if (fail) throw Error('test network failure'); return ticket('pending_payment'); } return { events: [event] }; }); await flush();
  assert.equal(flow.elements['event-title'].textContent, event.title, 'untrusted title uses text');
  await flow.elements['open-register'].fire('click');
  flow.elements['register-name'].value = '测试用户'; flow.elements['register-phone'].value = '13800000000';
  await flow.elements['register-form'].fire('submit'); assert.equal(calls.length, 0, 'consent required');
  flow.elements['register-consent'].checked = true;
  await flow.elements['register-form'].fire('submit'); assert.equal(calls.length, 1); assert.equal(flow.elements['register-name'].value, '测试用户', 'failure retains draft'); assert.equal(flow.elements['register-submit'].disabled, false);
  assert.equal(calls[0].expectedPriceFen, 12900, 'displayed price is sent for server comparison');
  event.priceFen = 22900;
  await flow.elements['register-form'].fire('submit'); assert.equal(calls[0].requestId, calls[1].requestId, 'retry uses same request id');
  assert.equal(calls[1].expectedPriceFen, 12900, 'opened form price remains frozen after event changes');
  event.priceFen = 12900;
  flow.elements['register-phone'].value = '13900000000'; await flow.elements['register-form'].fire('submit'); assert.notEqual(calls[1].requestId, calls[2].requestId, 'edit creates fresh request id');
  fail = false; await flow.elements['register-form'].fire('submit'); assert.equal(flow.assigned[0], '/ticket#ticket=order-test-token', 'pending order token route, no fake paid ticket');
  const reprice = boot('events', async path => {
    if (path.endsWith('/mine')) return { tickets: [] };
    if (path.endsWith('/register')) { const error = Error('价格已调整，请重新确认'); error.status = 409; throw error; }
    if (path.endsWith('/test')) return { event: { ...event, priceFen: 22900 } };
    return { events: [event] };
  }); await flush(); await reprice.elements['open-register'].fire('click');
  reprice.elements['register-name'].value = '费用验收'; reprice.elements['register-phone'].value = '13800000000'; reprice.elements['register-consent'].checked = true;
  await reprice.elements['register-form'].fire('submit'); assert.equal(reprice.elements['refresh-registration-price'].hidden, false); assert.equal(reprice.elements['register-name'].value, '费用验收');
  await reprice.elements['refresh-registration-price'].fire('click'); assert.equal(reprice.elements['register-price'].textContent, '¥229'); assert.equal(reprice.elements['register-consent'].checked, false, 'changed price requires renewed explicit confirmation'); assert.equal(reprice.elements['register-name'].value, '费用验收');
  const cancellation = boot('ticket', async path => path.endsWith('/cancel') ? ticket('refund_pending') : ticket()); await flush(); await cancellation.elements['confirm-cancel'].fire('click'); assert.equal(cancellation.elements['ticket-qr-area'].hidden, true); assert.match(cancellation.elements['ticket-message'].textContent, /退款申请/); assert.doesNotMatch(cancellation.elements['ticket-message'].textContent, /退款成功|已退款/);
  let releaseBootstrap; const fetches = [];
  const commonContext = { window: {}, Intl, Map, AbortController, setTimeout, clearTimeout, Uint8Array, crypto: require('node:crypto').webcrypto, fetch: async path => { fetches.push(path); if (path === '/api/campaign') await new Promise(resolve => { releaseBootstrap = resolve; }); return { ok: true, json: async () => path === '/api/campaign' ? { cities: [{ id: 'shenzhen', posterUrl: '/api/media/custom.webp' }] } : {} }; } };
  vm.createContext(commonContext); vm.runInContext(fs.readFileSync(`${root}/web/creator/events/common.js`, 'utf8'), commonContext);
  const a = commonContext.window.LeanEvents.api('/api/campaign/events/mine'); const b = commonContext.window.LeanEvents.api('/api/campaign/events/test/register', { name: '测试' });
  await flush(); assert.deepEqual(fetches, ['/api/campaign'], 'single identity request precedes parallel API calls'); releaseBootstrap(); await Promise.all([a, b]); assert.equal(fetches.length, 3);
  assert.equal(commonContext.window.LeanEvents.poster({ city: 'shenzhen' }), '/api/media/custom.webp', 'published CMS poster applies');
  console.log('PASS: public UI payment gating (6 states), cancelled/invalid QR, checked-in, payment readiness, plain text rendering, consent, retry idempotency, draft retention, edited request, pending order routing, refund request semantics; DOM references validated.');
})().catch(error => { console.error(error); process.exitCode = 1; });
