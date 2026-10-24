const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync('web/creator/city-vote/index.html', 'utf8');
const source = fs.readFileSync('web/creator/city-vote/comments.js', 'utf8');
class Element {
  constructor(id = '') {
    this.id = id; this.children = []; this.handlers = {}; this.attributes = {}; this.dataset = {};
    this.value = ''; this.checked = false; this.hidden = false; this.disabled = false;
    this.style = { setProperty(key, value) { this[key] = value; } }; this.clientWidth = 1000;
    this.classList = { toggle() {} };
  }
  set textContent(value) { this._text = value; this.children = []; }
  get textContent() { return this._text || ''; }
  set innerHTML(_) { throw Error('User input must not reach innerHTML'); }
  get firstElementChild() { return this.children[0]; }
  append(...children) { children.forEach(child => child.parent = this); this.children.push(...children); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(key, callback) { this.handlers[key] = callback; }
  async fire(key, event = {}) { return this.handlers[key]?.({ preventDefault() {}, ...event }); }
  click() { this.clicked = true; return this.fire('click'); }
  focus() { this.focused = true; }
  getBoundingClientRect() { return { width: 240 }; }
  querySelector(selector) { if (selector === 'span') return this.children[0]; return this.selected; }
  querySelectorAll(selector) { return selector === '[data-comment-id]' ? this.children.filter(node => node.dataset.commentId) : this.controls || []; }
}
function comment(id, content = '一起去运动吧') {
  return { id: String(id), content, nickname: '测试同好', city: 'shenzhen', createdAt: '2026-10-23T15:00:00Z' };
}
function boot(options = {}) {
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(ids.length, new Set(ids).size, 'unique DOM ids');
  const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
  elements['danmaku-band'].clientWidth = options.width || 1000;
  const form = elements['comment-form']; form.selected = new Element();
  form.controls = ['comment-content', 'comment-nickname', 'comment-consent', 'comment-submit', 'comment-private'].map(id => elements[id]);
  form.reset = () => { form.controls.forEach(node => { node.value = ''; node.checked = false; }); form.selected.value = ''; };
  elements['comment-submit'].append(new Element()); elements['danmaku-toggle'].append(new Element());
  const timers = new Map(), calls = [], listeners = {}, storage = new Map();
  let timerId = 0, requestId = 0, clock = Date.now();
  class TestDate extends Date { static now() { return clock; } }
  const motion = { matches: Boolean(options.reduced), addEventListener(type, fn) { this.changed = fn; } };
  let observer;
  class Observer { constructor(callback) { observer = callback; } observe() {} }
  const document = {
    getElementById(id) { assert.ok(elements[id], `DOM id exists: ${id}`); return elements[id]; },
    createElement() { return new Element(); }, visibilityState: 'visible',
    addEventListener(type, fn) { listeners[type] = fn; }
  };
  const context = {
    document, window: { LeanCampaignSession: { ensureIdentity: options.identity || (() => Promise.resolve()), randomId: () => `request-${++requestId}` }, IntersectionObserver: Observer },
    IntersectionObserver: Observer, matchMedia: () => motion,
    sessionStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    setTimeout(fn, ms) { const id = ++timerId; timers.set(id, { fn, ms }); return id; }, clearTimeout: id => timers.delete(id),
    URLSearchParams, AbortController, Date: TestDate, Intl, console,
    fetch: async (path, init) => {
      calls.push({ path, body: init.body ? JSON.parse(init.body) : null });
      const data = await (options.api || (async () => ({ items: [], total: 0, nextCursor: null })))(path, init);
      return { ok: true, json: async () => data };
    }
  };
  vm.createContext(context); vm.runInContext(source, context);
  return { elements, calls, timers, document, listeners, motion, storage, observe: entries => observer(entries), context,
    driftTick() {
      clock += 10000;
      const timer = [...timers].find(([, value]) => value.ms === 2200);
      assert.ok(timer, 'drift scheduler is running');
      timers.delete(timer[0]); timer[1].fn();
    }
  };
}
const flush = async () => { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); };
const fill = (page, value = '周末一起练肩吧') => { page.elements['comment-content'].value = value; page.elements['comment-consent'].checked = true; };
(async () => {
  const empty = boot(); await flush();
  assert.equal(empty.elements['comment-empty'].hidden, false);
  assert.equal(empty.elements['comment-list'].children.length, 0, 'no seeded fake comments');
  assert.equal(empty.elements['comment-total'].textContent, '0', 'heading count comes from the public response');
  assert.equal(empty.elements['comment-total'].attributes['aria-label'], '0 条留言');
  const ambientPill = empty.elements['danmaku-band'].children[0];
  assert.equal(ambientPill.children.length, 1, 'decorative prompt has no official badge or invented author');
  assert.equal(ambientPill.dataset.commentId, undefined, 'decorative prompt is never a public comment');
  assert.ok(ambientPill.children[0].textContent.length >= 2);
  assert.equal(1260 / parseFloat(ambientPill.style['--drift-duration']), 68, 'desktop drift is moderately faster');
  const mobile = boot({ width: 390 }); await flush();
  assert.equal(650 / parseFloat(mobile.elements['danmaku-band'].children[0].style['--drift-duration']), 56, 'mobile keeps a readable, faster pace');
  const round = [ambientPill.dataset.topicIndex];
  for (let i = 0; i < 47; i++) {
    empty.elements['danmaku-band'].replaceChildren();
    empty.driftTick();
    round.push(empty.elements['danmaku-band'].children[0].dataset.topicIndex);
  }
  assert.equal(new Set(round).size, 48, 'a complete ambient round has no repeated phrase');
  empty.elements['danmaku-band'].replaceChildren(); empty.driftTick();
  assert.notEqual(empty.elements['danmaku-band'].children[0].dataset.topicIndex, round.at(-1), 'round boundary does not immediately repeat');
  assert.equal(empty.calls.filter(call => call.body).length, 0, 'ambient motion never writes fake posts');
  assert.equal(empty.elements['comment-total'].textContent, '0', 'ambient motion never inflates comment counts');
  assert.equal(empty.elements['comment-consent'].checked, false, 'public consent starts unchecked');
  empty.elements['comment-content'].value = '一起运动';
  await empty.elements['comment-form'].fire('submit');
  assert.equal(empty.calls.filter(call => call.body).length, 0, 'no public consent means no POST');
  assert.match(empty.elements['comment-form-message'].textContent, /同意公开展示/);
  await empty.elements['comment-private'].fire('click');
  assert.equal(empty.elements['open-feedback'].clicked, true, 'private route remains separate');

  let failed = true, published = [];
  const postCalls = [];
  const retry = boot({ api: async (path, init) => {
    if (!init.body) return { items: published, total: published.length, nextCursor: null };
    const body = JSON.parse(init.body); postCalls.push(body);
    if (failed) throw Error('网络中断');
    published = [comment(1, body.content)];
    return { ok: true, item: { ...published[0], status: 'visible' }, duplicate: true };
  } }); await flush();
  fill(retry); await retry.elements['comment-form'].fire('submit');
  assert.equal(retry.elements['comment-content'].value, '周末一起练肩吧', 'failed POST retains draft');
  assert.equal(retry.elements['comment-consent'].checked, true);
  assert.equal(retry.elements['comment-submit'].disabled, false);
  failed = false; await retry.elements['comment-form'].fire('submit');
  assert.equal(postCalls[0].requestId, postCalls[1].requestId, 'lost response retry reuses idempotency key');
  assert.equal(retry.elements['comment-list'].children.length, 1);
  assert.equal(retry.elements['comment-content'].value, '');
  assert.equal(retry.elements['comment-consent'].checked, false, 'new comment requires fresh consent');
  assert.equal(retry.elements['comment-options'].open, false, 'successful submission collapses optional fields');
  fill(retry, '训练之后一起吃饭'); await retry.elements['comment-form'].fire('submit');
  assert.notEqual(postCalls[1].requestId, postCalls[2].requestId, 'new post gets independent key');

  const invalid = boot(); await flush();
  fill(invalid, '好'); await invalid.elements['comment-form'].fire('submit');
  fill(invalid, '好'.repeat(241)); await invalid.elements['comment-form'].fire('submit');
  fill(invalid, '🏃'.repeat(241)); await invalid.elements['comment-form'].fire('submit');
  fill(invalid); invalid.elements['comment-nickname'].value = '人'.repeat(21); await invalid.elements['comment-form'].fire('submit');
  assert.equal(invalid.elements['comment-options'].open, true, 'nickname validation opens its disclosure before focusing the field');
  assert.equal(invalid.elements['comment-nickname'].focused, true);
  assert.equal(invalid.calls.filter(call => call.body).length, 0, 'length validation counts Unicode code points');

  let releaseIdentity;
  const identity = new Promise(resolve => releaseIdentity = resolve);
  const gated = boot({ identity: () => identity }); await flush(); fill(gated);
  const pending = gated.elements['comment-form'].fire('submit'); await flush();
  await gated.elements['comment-form'].fire('submit');
  assert.equal(gated.calls.filter(call => call.body).length, 0, 'POST waits for shared visitor identity');
  releaseIdentity(); await pending;
  assert.equal(gated.calls.filter(call => call.body).length, 1, 'double submission is suppressed');

  const malicious = '<img src=x onerror=alert(1)>';
  let exposed = [comment(2, malicious)];
  const safe = boot({ api: async () => ({ items: exposed, total: exposed.length, nextCursor: null }) }); await flush();
  assert.equal(safe.elements['comment-list'].children[0].children[1].textContent, malicious, 'untrusted content stays text');
  const activePill = new Element(); activePill.dataset.commentId = '2'; safe.elements['danmaku-band'].append(activePill);
  exposed = []; await safe.elements['comment-refresh'].fire('click');
  assert.equal(safe.elements['comment-list'].children.length, 0, 'moderated comments leave wall');
  assert.equal(safe.elements['danmaku-band'].children.includes(activePill), false, 'moderated comments leave active danmaku');

  const sparse = boot({ api: async () => ({ items: [comment(3)], total: 1, nextCursor: null }) }); await flush();
  for (let i = 0; i < 8; i++) sparse.driftTick();
  const visiblePills = sparse.elements['danmaku-band'].children;
  assert.equal(visiblePills.length, 6, 'lane scheduler retains its six-pill cap');
  assert.equal(visiblePills.filter(node => node.dataset.commentId === '3').length, 1, 'one real comment never repeats concurrently');
  const visibleTopics = visiblePills.filter(node => node.dataset.topicIndex !== undefined).map(node => node.dataset.topicIndex);
  assert.equal(visibleTopics.length, new Set(visibleTopics).size, 'ambient prompts do not repeat concurrently either');
  const realPill = visiblePills.find(node => node.dataset.commentId === '3');
  assert.equal(realPill.children[0].textContent, '留言 · 深圳', 'real comments retain their source');
  assert.equal(realPill.children[1].textContent, '一起去运动吧', 'real comment text is unchanged');
  visiblePills.find(node => node.dataset.commentId === '3').remove();
  visiblePills.find(node => node.dataset.topicIndex !== undefined).remove();
  for (let i = 0; i < 3; i++) sparse.driftTick();
  assert.equal(sparse.elements['danmaku-band'].children.filter(node => node.dataset.commentId === '3').length, 1, 'comment may return after its prior animation has left');

  const hidden = boot({ api: async (_, init) => init.body ? { ok: true, item: { ...comment(9), status: 'hidden' }, duplicate: true } : { items: [], total: 0, nextCursor: null } });
  await flush(); fill(hidden); await hidden.elements['comment-form'].fire('submit');
  assert.match(hidden.elements['comment-form-message'].textContent, /已被团队收起/);
  assert.equal(hidden.elements['comment-list'].children.length, 0, 'hidden duplicate never optimistically republished');

  let pageOne = Array.from({ length: 20 }, (_, i) => comment(100 - i));
  let pageTwo = Array.from({ length: 20 }, (_, i) => comment(80 - i));
  const paged = boot({ api: async path => ({ items: path.includes('cursor=') ? pageTwo : pageOne, total: 40, nextCursor: path.includes('cursor=') ? null : 'older' }) }); await flush();
  await paged.elements['comment-more'].fire('click');
  assert.equal(paged.elements['comment-list'].children.length, 40);
  pageTwo = pageTwo.filter(item => item.id !== '80');
  await paged.elements['comment-refresh'].fire('click');
  assert.equal(paged.elements['comment-list'].children.length, 39, 'refresh includes loaded older page moderation');
  assert.ok(paged.timers.size < 5, 'bounded timers');
  paged.document.visibilityState = 'hidden'; paged.listeners.visibilitychange();
  assert.equal(paged.elements['danmaku-band'].children.length, 0);
  assert.equal([...paged.timers.values()].filter(timer => [30000, 2200].includes(timer.ms)).length, 0, 'hidden page stops polling and motion');
  paged.document.visibilityState = 'visible';
  paged.observe([{ target: paged.elements['vote-hero'], isIntersecting: false }, { target: paged.elements.conversation, isIntersecting: true }]);
  assert.equal(paged.elements['danmaku-band'].children.length, 0, 'offscreen photo stops motion');
  assert.equal([...paged.timers.values()].filter(timer => timer.ms === 30000).length, 1, 'visible comments keep bounded refresh');

  const reduced = boot({ reduced: true }); await flush();
  assert.equal(reduced.elements['danmaku-band'].children.length, 0);
  assert.equal(reduced.elements['danmaku-toggle'].disabled, true);
  assert.equal(reduced.elements['danmaku-toggle'].attributes['aria-pressed'], 'false');
  await empty.elements['danmaku-toggle'].fire('click');
  assert.equal(empty.elements['danmaku-band'].children.length, 0);
  assert.equal(empty.storage.get('leanclub-comment-motion'), 'off');
  assert.match(html, /意见箱 · 仅团队查看/);
  console.log('Public comments UI: consent, Unicode bounds, identity, idempotency, safe text, moderation, pagination, motion and visibility checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
