// Run the actual homepage controller against a small DOM/API fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync('web/creator/home-school-search.js', 'utf8');
class Element {
  constructor(tag = 'div', className = '', text = '') {
    this.tagName = tag.toUpperCase(); this.className = className; this.textContent = text;
    this.children = []; this.listeners = {}; this.attributes = {}; this.style = {}; this.dataset = {};
    this.hidden = false; this.value = '';
  }
  append(...children) { children.forEach(child => { child.parent = this; this.children.push(child); }); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  emit(type, options = {}) {
    const event = { target: this, prevented: false, preventDefault() { this.prevented = true; }, ...options };
    for (const handler of this.listeners[type] || []) handler(event);
    return event;
  }
  contains(target) { return target === this || this.children.some(child => child.contains(target)); }
  getBoundingClientRect() { return this.rect || { height: Math.min(350, parseFloat(this.style.maxHeight) || 440) }; }
  scrollIntoView() { this.scrolled = true; }
  set innerHTML(_) { throw new Error('School text must not be interpreted as HTML'); }
}
const tick = () => new Promise(resolve => setImmediate(resolve));
function setup() {
  const form = new Element('form'), input = new Element('input'), status = new Element('p'), body = new Element('body');
  form.rect = { left: 24, top: 350, bottom: 398, width: 342 }; form.append(input); body.append(form, status);
  const requests = [], navigations = [], timers = new Map(); let timerId = 0;
  const document = new Element();
  document.body = body; document.querySelector = selector => ({ '.hero-search': form, '#hero-school': input, '.hero-search-status': status })[selector];
  const window = new Element(); window.scrollX = 0; window.scrollY = 0; window.innerHeight = 844;
  window.LeanClubPortal = {
    node: (tag, className, text) => new Element(tag, className, text),
    api: path => new Promise((resolve, reject) => requests.push({ path, resolve, reject }))
  };
  const context = { document, window, URLSearchParams, location: { assign: value => navigations.push(value) },
    setTimeout: callback => { const id = ++timerId; timers.set(id, callback); return id; }, clearTimeout: id => timers.delete(id) };
  vm.runInNewContext(source, context);
  const popup = body.children.at(-1), [heading, results, message, all] = popup.children;
  return { form, input, status, body, document, window, requests, navigations, popup, heading, results, message, all,
    flushTimer() { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(callback => callback()); } };
}
const schools = [
  { id: 2031, name: '深圳大学', province: '广东省', city: '深圳市', level: '本科', clubCount: 0 },
  { id: 2072, name: '深圳技术大学', province: '广东省', city: '深圳市', level: '本科', clubCount: 1 }
];
test('home suggestions use the same directory API and selected ID opens school detail', async () => {
  const h = setup(); h.input.value = '深圳'; h.input.emit('focus');
  assert.equal(h.requests.length, 1);
  const url = new URL(h.requests[0].path, 'http://fixture');
  assert.equal(url.pathname, '/leanclub/school/list'); assert.equal(url.searchParams.get('keyword'), '深圳');
  assert.equal(url.searchParams.get('pageSize'), '6');
  h.requests[0].resolve({ list: schools, total: 8 }); await tick();
  assert.equal(h.results.children.length, 2);
  assert.equal(h.results.children[0].children[2].textContent, '学校目录');
  assert.equal(h.results.children[1].children[2].textContent, '俱乐部已开通');
  h.results.children[0].emit('click');
  assert.deepEqual(h.navigations, ['/schools?schoolId=2031']);
});
test('search form opens the complete matching directory without a second chooser', () => {
  const h = setup(); h.input.value = '上海 & 北京'; h.form.emit('submit');
  const url = new URL(h.navigations[0], 'http://fixture');
  assert.equal(url.pathname, '/schools'); assert.equal(url.searchParams.get('keyword'), '上海 & 北京');
});
test('up/down and Enter use the active option; Escape closes without navigation', async () => {
  const h = setup(); h.input.emit('focus'); h.requests[0].resolve({ list: schools, total: 2 }); await tick();
  h.input.emit('keydown', { key: 'ArrowUp' });
  assert.equal(h.input.attributes['aria-activedescendant'], 'hero-school-option-1');
  h.input.emit('keydown', { key: 'Enter' }); assert.deepEqual(h.navigations, ['/schools?schoolId=2072']);
  const other = setup(); other.input.emit('focus'); other.input.emit('keydown', { key: 'Escape' });
  assert.equal(other.popup.hidden, true); assert.deepEqual(other.navigations, []);
});
test('late responses cannot reopen a closed suggestion list', async () => {
  const h = setup(); h.input.emit('focus'); h.document.emit('pointerdown', { target: h.body });
  h.requests[0].resolve({ list: schools, total: 2 }); await tick();
  assert.equal(h.popup.hidden, true); assert.equal(h.results.children.length, 0);
});
test('rapid query changes clear stale options and ignore out-of-order responses', async () => {
  const h = setup(); h.input.value = '深圳'; h.input.emit('focus');
  h.requests[0].resolve({ list: schools, total: 2 }); await tick();
  h.input.emit('keydown', { key: 'ArrowDown' });
  h.input.value = '广州'; h.input.emit('input');
  assert.equal(h.results.children.length, 0); assert.equal(h.input.attributes['aria-activedescendant'], undefined);
  h.flushTimer();
  h.input.value = '北京'; h.input.emit('input'); h.flushTimer();
  h.requests[2].resolve({ list: [{ id: 1, name: '北京大学' }], total: 1 }); await tick();
  h.requests[1].resolve({ list: [{ id: 2, name: '广州大学' }], total: 1 }); await tick();
  assert.equal(h.results.children[0].children[1].children[0].textContent, '北京大学');
});
test('Chinese composition avoids intermediate queries and accidental form navigation', async () => {
  const h = setup(); h.input.emit('compositionstart'); h.input.value = 'shen'; h.input.emit('input'); h.flushTimer();
  assert.equal(h.input.emit('keydown', { key: 'Enter', isComposing: true }).prevented, true);
  h.form.emit('submit'); assert.equal(h.requests.length, 0); assert.equal(h.navigations.length, 0);
  h.input.value = '深圳'; h.input.emit('compositionend'); h.flushTimer();
  assert.equal(h.requests.length, 1); assert.equal(new URL(h.requests[0].path, 'http://fixture').searchParams.get('keyword'), '深圳');
});
test('empty results and API failures are distinguishable; retry fetches fresh results', async () => {
  const h = setup(); h.input.value = '未知学校'; h.input.emit('focus'); h.requests[0].resolve({ list: [], total: 0 }); await tick();
  assert.equal(h.message.hidden, false); assert.match(h.message.children[0].textContent, /暂未找到/);
  assert.equal(h.message.children[1].hidden, true);
  h.input.value = '深圳'; h.input.emit('input'); h.flushTimer(); h.requests[1].reject(new Error('offline')); await tick();
  assert.equal(h.message.children[1].hidden, false); assert.match(h.status.textContent, /加载失败/);
  h.message.children[1].emit('click'); h.requests[2].resolve({ list: schools, total: 2 }); await tick();
  assert.equal(h.message.hidden, true); assert.equal(h.results.children.length, 2);
});
test('school strings are text-only and malformed identifiers are omitted', async () => {
  const h = setup(); h.input.emit('focus');
  h.requests[0].resolve({ list: [{ id: 5, name: '<img src=x onerror=alert(1)>' }, { id: '../bad', name: 'wrong' }], total: 2 }); await tick();
  assert.equal(h.results.children.length, 1); assert.equal(h.results.children[0].children[1].children[0].textContent, '<img src=x onerror=alert(1)>');
});
test('a small viewport places suggestions above the input and anchors to its width', () => {
  const h = setup(); h.window.innerHeight = 450; h.input.emit('focus');
  assert.equal(h.popup.dataset.placement, 'above'); assert.equal(h.popup.style.width, '342px');
  assert.ok(parseFloat(h.popup.style.top) >= 0); assert.ok(parseFloat(h.popup.style.maxHeight) < 350);
});
