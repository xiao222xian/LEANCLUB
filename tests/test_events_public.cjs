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
const event = { id: '11111111111111111111111111111111', title: '<img src=x onerror=alert(1)>', subtitle: 'Test only', description: 'Plan', city: 'shenzhen', posterCity: 'zhuhai', venue: 'Test venue', address: 'Test address', startsAt: '2030-10-22T10:00:00Z', endsAt: '2030-10-22T14:00:00Z', registrationClosesAt: '2030-10-22T09:00:00Z', priceFen: 12900, status: 'published', remaining: 6, capacity: 10, registrationOpen: true, paymentReady: true };
const orderId = '22222222222222222222222222222222';
function ticket(status = 'paid') { return { accessMode: 'member', event, registration: { name: '测试昵称', phoneMasked: '138****0000', status: 'registered', ticketNumber: 'LC-TESTONLY' }, order: { id: orderId, status, amountFen: 12900, expiresAt: '2030-10-22T09:00:00Z' }, orderToken: 'order-test-token', ticketToken: status === 'paid' ? 'checkin-token' : null, qrDataUrl: 'data:image/png;base64,AAAA', canCancel: ['paid', 'pending_payment'].includes(status) }; }
function boot(page, api, options = {}) {
  const html = fs.readFileSync(`${root}/web/creator/${page}/index.html`, 'utf8');
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]); assert.equal(ids.length, new Set(ids).size, 'no duplicate DOM ids');
  const elements = Object.fromEntries(ids.map(id => [id, new Element(id)])); const assigned = [];
  assert.ok(html.indexOf('src="/site-portal.js') < html.indexOf(`src="/${page}/app.js`), 'identity client must load before page app');
  const location = { hash: options.hash ?? '#ticket=order-test-token', search: options.search || '', href: 'http://localhost/events', assign(value) { assigned.push(value); } };
  const listeners = {}, timers = new Map(); let timerId = 0, signedIn = options.signedIn ?? true, identity = 0, loginRequests = 0, pendingLogin;
  const context = { window: { addEventListener(type, fn) { listeners[type] = fn; } }, document: { getElementById(id) { assert.ok(elements[id], `DOM id exists: ${id}`); return elements[id]; }, createElement: () => new Element(), addEventListener() {}, visibilityState: 'visible', body: { classList: { add() {}, remove() {} } }, fonts: { ready: options.fontReady || Promise.resolve() } }, URL, URLSearchParams, location, history: { replaceState() {} }, setTimeout: fn => { const id = ++timerId; timers.set(id, fn); return id; }, clearTimeout: id => timers.delete(id), Intl, Date, console };
  let counter = 0;
  context.window.LeanEvents = { api, randomId: () => `request-${++counter}`, date: value => value, poster: e => `/city-vote/assets/alan-v1/${e.posterCity || e.city}.webp`, dialog: element => ({ open: () => element.showModal(), close: () => element.close() }), ticketUrl: value => `/ticket#ticket=${encodeURIComponent(value)}`, memberOrderUrl: value => { assert.match(value, /^[a-f0-9]{32}$/); return `/ticket?order=${value}`; }, text: (tag, value, className) => { const node = new Element(); node.textContent = value; node.className = className; return node; }, cityNames: { shenzhen: '深圳', zhuhai: '珠海', guangzhou: '广州' }, money: value => `¥${value / 100}` };
  context.window.LeanClubPortal = { identityVersion: () => identity, isSignedIn: () => signedIn, requireMember: callback => { loginRequests++; pendingLogin = callback; }, api: (path, input) => { assert.equal(input.auth, true); return api(path, input.body); } };
  const changeIdentity = value => { signedIn = value; identity++; listeners['leanclub:identity']?.(); };
  vm.createContext(context); vm.runInContext(fs.readFileSync(`${root}/web/creator/${page}/app.js`, 'utf8'), context);
  return { elements, assigned, context, timers, changeIdentity, loginRequests: () => loginRequests, finishLogin: () => { changeIdentity(true); pendingLogin?.(); } };
}
async function flush() { for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve)); }
(async () => {
  for (const state of ['pending_payment', 'paid', 'closing', 'expired', 'cancelled', 'refund_pending', 'refunded']) {
    const response = ticket(state); const { elements } = boot('ticket', async () => response); await flush();
    assert.equal(elements['ticket-qr-area'].hidden, state !== 'paid', `${state} QR gating`);
    assert.equal(elements['save-ticket'].hidden, state !== 'paid', `${state} save gating`);
    if (state !== 'paid') assert.equal(elements['ticket-qr'].src, '', `${state} must remove QR src`);
    assert.equal(elements['ticket-price'].textContent, '¥129');
  }
  for (const invalid of [{ event: { ...event, status: 'cancelled' } }, { registration: { ...ticket().registration, status: 'cancelled' } }, { qrDataUrl: 'https://external.example/qr' }, { order: null }]) {
    const { elements } = boot('ticket', async () => ({ ...ticket(), ...invalid })); await flush(); assert.equal(elements['ticket-qr-area'].hidden, true, 'invalid ticket must not display QR');
  }
  // Activity cancellation and historic admission must never mask ongoing financial processing.
  for (const [state, label] of [['closing', '正在确认订单关闭'], ['refund_pending', '退款处理中'], ['refunded', '已退款']]) {
    const response = { ...ticket(state), event: { ...event, status: 'cancelled' }, registration: { ...ticket().registration, status: 'checked_in', checkedInAt: '2030-10-22T10:00:00Z' }, qrDataUrl: null, canCancel: false };
    const page = boot('ticket', async () => response, { search: `?order=${orderId}` }); await flush();
    assert.equal(page.elements['ticket-state'].textContent, label); assert.equal(page.elements['ticket-headline'].textContent, label);
    assert.equal(page.elements['open-cancel'].hidden, true); assert.equal(page.elements['ticket-qr'].src, '');
    assert.equal(page.timers.size, state === 'refunded' ? 0 : 1, 'only unresolved states keep polling');
    if (state !== 'refunded') assert.doesNotMatch(page.elements['order-state-copy'].textContent, /已退款|退款成功|名额.*释放/);
    const list = boot('events', async path => path.includes('/orders/page') ? { list: [response], total: 1 } : { events: [event] }); await flush();
    assert.equal(list.elements['mine-list'].children[0].children[2].children[0].textContent, label, 'account list prioritizes the same financial status');
  }
  let latestTicket = ticket();
  const transitions = boot('ticket', async () => latestTicket, { search: `?order=${orderId}` }); await flush();
  for (const state of ['closing', 'refund_pending', 'refunded']) {
    latestTicket = { ...ticket(state), qrDataUrl: null }; await transitions.elements['refresh-ticket'].fire('click');
    assert.equal(transitions.elements['ticket-qr'].src, '', `${state} refresh clears the prior paid QR`);
    assert.equal(transitions.elements['save-ticket'].hidden, true);
  }
  latestTicket = { ...ticket(), qrDataUrl: null }; await transitions.elements['refresh-ticket'].fire('click');
  assert.equal(transitions.elements['ticket-qr'].src, '', 'paid alone never restores a missing QR');
  const overdue = boot('ticket', async () => ({ ...ticket('pending_payment'), order: { ...ticket('pending_payment').order, expiresAt: '2000-01-01T00:00:00Z' } })); await flush();
  assert.equal(overdue.elements['ticket-state'].textContent, '待支付', 'browser time does not claim a server-unconfirmed order is closed');
  for (const state of ['closing', 'refund_pending']) {
    const held = boot('events', async path => path.includes('/orders/page') ? { list: [ticket(state)], total: 1 } : { events: [{ ...event, remaining: 0, closingCount: 1, registrationOpen: false }] }); await flush();
    assert.equal(held.elements['open-register'].disabled, false, 'occupied order remains accessible when the event is full');
    await held.elements['open-register'].fire('click'); assert.equal(held.assigned[0], `/ticket?order=${orderId}`, 'unresolved order is reopened instead of creating another order');
  }
  assert.doesNotMatch(fs.readFileSync(`${root}/web/creator/ticket/index.html`, 'utf8'), /取消后将释放/);
  const used = boot('ticket', async () => ({ ...ticket(), registration: { ...ticket().registration, status: 'checked_in', checkedInAt: '2030-10-22T10:00:00Z' } })); await flush(); assert.equal(used.elements['ticket-state'].textContent, '已入场'); assert.equal(used.elements['checkin-time'].hidden, false);
  const unavailable = boot('events', async path => path.includes('/orders/page') ? { list: [], total: 0 } : { events: [{ ...event, paymentReady: false, registrationOpen: false }] }); await flush(); assert.equal(unavailable.elements['open-register'].disabled, true); assert.equal(unavailable.elements['open-register'].textContent, '报名即将开放');
  const calls = []; let fail = true;
  const flow = boot('events', async (path, body) => { if (path.includes('/orders/page')) return { list: [], total: 0 }; if (path.endsWith('/register')) { assert.match(path, /^\/leanclub\/events\//); calls.push(body); if (fail) throw Error('test network failure'); return ticket('pending_payment'); } return { events: [event] }; }); await flush();
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
  fail = false; await flow.elements['register-form'].fire('submit'); assert.equal(flow.assigned[0], `/ticket?order=${orderId}`, 'member order id route contains no ticket secret');
  const reprice = boot('events', async path => {
    if (path.includes('/orders/page')) return { list: [], total: 0 };
    if (path.endsWith('/register')) { const error = Error('价格已调整，请重新确认'); error.status = 409; throw error; }
    if (path.endsWith('/' + event.id)) return { event: { ...event, priceFen: 22900 } };
    return { events: [event] };
  }); await flush(); await reprice.elements['open-register'].fire('click');
  reprice.elements['register-name'].value = '费用验收'; reprice.elements['register-phone'].value = '13800000000'; reprice.elements['register-consent'].checked = true;
  await reprice.elements['register-form'].fire('submit'); assert.equal(reprice.elements['refresh-registration-price'].hidden, false); assert.equal(reprice.elements['register-name'].value, '费用验收');
  await reprice.elements['refresh-registration-price'].fire('click'); assert.equal(reprice.elements['register-price'].textContent, '¥229'); assert.equal(reprice.elements['register-consent'].checked, false, 'changed price requires renewed explicit confirmation'); assert.equal(reprice.elements['register-name'].value, '费用验收');
  await reprice.elements['close-register'].fire('click'); await reprice.elements['open-register'].fire('click');
  assert.equal(reprice.elements['register-consent'].checked, false, 'reopening cannot restore consent for an old price');
  const cancellation = boot('ticket', async path => path.endsWith('/cancel') ? ticket('refund_pending') : ticket()); await flush(); await cancellation.elements['confirm-cancel'].fire('click'); assert.equal(cancellation.elements['ticket-qr-area'].hidden, true); assert.match(cancellation.elements['ticket-message'].textContent, /退款申请/); assert.doesNotMatch(cancellation.elements['ticket-message'].textContent, /退款成功|已退款/);
  // New registrations require a real member session; the anonymous endpoint is never a fallback.
  const anonymousCalls = [];
  const anonymous = boot('events', async path => { anonymousCalls.push(path); return { events: [event] }; }, { signedIn: false });
  await flush(); await anonymous.elements['open-register'].fire('click');
  assert.equal(anonymous.loginRequests(), 1); assert.equal(anonymous.elements['register-dialog'].open, false);
  await anonymous.elements['register-form'].fire('submit');
  assert.equal(anonymousCalls.some(path => path.endsWith('/register')), false);
  await anonymous.elements['open-mine'].fire('click'); assert.equal(anonymous.elements['mine-sign-in'].hidden, false);
  assert.equal(anonymousCalls.some(path => path.endsWith('/mine') || path.includes('/orders/')), false, 'no automatic anonymous or unauthenticated private list');

  const listCalls = [];
  const paged = boot('events', async path => {
    listCalls.push(path);
    if (path.includes('/orders/page')) return { list: [ticket()], total: 45 };
    if (path.endsWith('/mine')) return { tickets: [ticket()] };
    return { events: [event] };
  }); await flush();
  assert.equal(paged.elements['mine-list'].children[0].href, `/ticket?order=${orderId}`);
  assert.equal(paged.elements['mine-pagination'].hidden, false); assert.equal(paged.elements['mine-prev'].disabled, true);
  await paged.elements['mine-next'].fire('click'); await flush(); assert.ok(listCalls.some(path => path.includes('pageNo=2&pageSize=20')));
  await paged.elements['mine-legacy'].fire('click'); await flush();
  assert.equal(paged.elements['mine-list'].children[0].href, '/ticket#ticket=order-test-token');
  assert.match(paged.elements['mine-note'].textContent, /不会自动并入账号/);

  let finishRegistration;
  const delayedRegistration = boot('events', async path => {
    if (path.includes('/orders/page')) return { list: [], total: 0 };
    if (path.endsWith('/register')) return new Promise(resolve => { finishRegistration = resolve; });
    return { events: [event] };
  }); await flush(); await delayedRegistration.elements['open-register'].fire('click');
  delayedRegistration.elements['register-name'].value = '隔离测试'; delayedRegistration.elements['register-phone'].value = '13800000000'; delayedRegistration.elements['register-consent'].checked = true;
  const submitting = delayedRegistration.elements['register-form'].fire('submit');
  delayedRegistration.changeIdentity(false);
  assert.equal(delayedRegistration.elements['register-name'].value, ''); assert.equal(delayedRegistration.elements['register-phone'].value, '');
  assert.equal(delayedRegistration.elements['register-dialog'].open, false); assert.equal(delayedRegistration.elements['register-consent'].checked, false);
  finishRegistration(ticket('pending_payment')); await submitting;
  assert.equal(delayedRegistration.assigned.length, 0, 'late prior-account registration result cannot navigate');

  let finishList;
  const delayedList = boot('events', async path => path.includes('/orders/page') ? new Promise(resolve => { finishList = resolve; }) : { events: [event] });
  await flush(); delayedList.changeIdentity(false); finishList({ list: [ticket()], total: 1 }); await flush();
  assert.equal(delayedList.elements['ticket-count'].hidden, true);
  assert.equal(delayedList.elements['mine-list'].children.some(item => item.href), false, 'late prior-account list cannot restore order links');

  const modeCalls = [];
  const memberPage = boot('ticket', async path => { modeCalls.push(path); return ticket(); }, { search: `?order=${orderId}` });
  await flush(); assert.equal(modeCalls[0], `/leanclub/orders/get?id=${orderId}`); assert.equal(memberPage.elements['ticket-qr-area'].hidden, false);
  memberPage.changeIdentity(false);
  assert.equal(memberPage.elements['ticket-qr'].src, ''); assert.equal(memberPage.elements['ticket-name'].textContent, '');
  assert.equal(memberPage.elements['ticket-experience'].hidden, true); assert.equal(memberPage.elements['ticket-sign-in'].hidden, false); assert.equal(memberPage.timers.size, 0);
  const noLoginCalls = [];
  const noLogin = boot('ticket', async path => { noLoginCalls.push(path); return ticket(); }, { search: `?order=${orderId}`, signedIn: false }); await flush();
  assert.equal(noLoginCalls.length, 0); assert.equal(noLogin.elements['ticket-sign-in'].hidden, false);
  const invalidMode = boot('ticket', async () => { throw Error('must not call'); }, { search: '?order=invalid' }); await flush();
  assert.match(invalidMode.elements['loading-message'].textContent, /有效的订单/);

  let finishTicket;
  const delayedTicket = boot('ticket', async () => new Promise(resolve => { finishTicket = resolve; }), { search: `?order=${orderId}` });
  await flush(); delayedTicket.changeIdentity(false); finishTicket(ticket()); await flush();
  assert.equal(delayedTicket.elements['ticket-qr'].src, ''); assert.equal(delayedTicket.elements['ticket-experience'].hidden, true);

  const cancelCalls = []; let cancelFails = true;
  const memberCancel = boot('ticket', async (path, body) => {
    if (path.endsWith('/cancel')) { cancelCalls.push(body); if (cancelFails) throw Error('test uncertain response'); return ticket('refund_pending'); }
    return ticket();
  }, { search: `?order=${orderId}` }); await flush();
  await memberCancel.elements['confirm-cancel'].fire('click'); assert.equal(memberCancel.elements['ticket-qr'].src, '');
  cancelFails = false; await memberCancel.elements['confirm-cancel'].fire('click');
  assert.equal(cancelCalls[0].id, orderId); assert.equal(cancelCalls[0].requestId, cancelCalls[1].requestId); assert.equal(cancelCalls[0].ticketToken, undefined);
  assert.match(memberCancel.elements['ticket-message'].textContent, /退款申请/);

  let finishOldRead, reads = 0;
  const cancelRace = boot('ticket', async path => {
    if (path.endsWith('/cancel')) return ticket('refund_pending');
    if (++reads === 1) return ticket();
    return new Promise(resolve => { finishOldRead = resolve; });
  }, { search: `?order=${orderId}` }); await flush();
  const oldRead = cancelRace.elements['refresh-ticket'].fire('click');
  await cancelRace.elements['confirm-cancel'].fire('click'); finishOldRead(ticket()); await oldRead;
  assert.equal(cancelRace.elements['ticket-qr'].src, ''); assert.equal(cancelRace.elements['ticket-state'].textContent, '退款处理中', 'old paid GET cannot restore QR after cancellation');

  let finishFonts;
  const savingTicket = boot('ticket', async () => ticket(), { search: `?order=${orderId}`, fontReady: new Promise(resolve => { finishFonts = resolve; }) });
  await flush(); const save = savingTicket.elements['save-ticket'].fire('click'); savingTicket.changeIdentity(false); finishFonts(); await save;
  assert.equal(savingTicket.elements['ticket-qr'].src, ''); assert.equal(savingTicket.elements['ticket-message'].textContent, '', 'prior-account image generation stops before loading QR');
  let releaseBootstrap; const fetches = [];
  const commonContext = { window: {}, Intl, Map, AbortController, setTimeout, clearTimeout, Uint8Array, crypto: require('node:crypto').webcrypto, fetch: async path => { fetches.push(path); if (path === '/api/campaign') await new Promise(resolve => { releaseBootstrap = resolve; }); return { ok: true, json: async () => path === '/api/campaign' ? { cities: [{ id: 'shenzhen', posterUrl: '/api/media/custom.webp' }] } : {} }; } };
  vm.createContext(commonContext); vm.runInContext(fs.readFileSync(`${root}/web/creator/events/common.js`, 'utf8'), commonContext);
  const a = commonContext.window.LeanEvents.api('/api/campaign/events/mine'); const b = commonContext.window.LeanEvents.api('/api/campaign/events/test/register', { name: '测试' });
  await flush(); assert.deepEqual(fetches, ['/api/campaign'], 'single identity request precedes parallel API calls'); releaseBootstrap(); await Promise.all([a, b]); assert.equal(fetches.length, 3);
  assert.equal(commonContext.window.LeanEvents.poster({ city: 'shenzhen' }), '/api/media/custom.webp', 'published CMS poster applies');
  console.log('PASS: public UI payment gating (7 states), financial-state priority and transitions, occupied-order recovery, valid/invalid QR, consent, price/retry semantics, member-only registration, account pagination, isolated legacy records, member/legacy ticket routing, logout and late-response cleanup, cancellation idempotency/read race, private image generation cancellation; DOM references and script order validated.');
})().catch(error => { console.error(error); process.exitCode = 1; });
