const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const published = fs.readFileSync('web/creator/site-published.js', 'utf8');
const portal = fs.readFileSync('web/creator/site-portal.js', 'utf8');
const hrefSource = portal.slice(portal.indexOf('  function safeHref('), portal.indexOf('  const memberDialog ='));

class Element {
  constructor(tag, className = '', text = '') { this.tagName = tag; this.className = className; this.textContent = text; this.children = []; }
  append(...items) { items.forEach(item => { item.parent = this; this.children.push(item); }); }
  prepend(fragment) { this.children.unshift(...fragment.children); this.children.forEach(item => { item.parent = this; }); }
  remove() { this.parent.children.splice(this.parent.children.indexOf(this), 1); }
  querySelectorAll(selector) { return selector === 'a' ? this.children.filter(item => item.tagName === 'a') : []; }
  set innerHTML(_) { throw new Error('Published labels must be text'); }
}

async function render(navigation, options = {}) {
  const nav = new Element('nav'), button = new Element('button', 'nav-member', '登录 / 注册');
  const fallback = new Element('a', '', '薄肌设计'); fallback.href = '/design/'; nav.append(fallback, button);
  const context = { URL, location: new URL('https://www.leanschool.net/'), window: {},
    document: { body: { dataset: {} }, querySelectorAll: () => [], querySelector: selector => selector === '#home-navigation' ? nav : null,
      createDocumentFragment: () => new Element('fragment') } };
  vm.createContext(context);
  vm.runInContext(hrefSource + '\nwindow.safeHref = safeHref;', context);
  context.window.LeanClubPortal = {
    node: (tag, className, text) => new Element(tag, className, text), safeHref: context.window.safeHref,
    api: () => options.fail ? Promise.reject(new Error('offline')) : Promise.resolve(options.empty ? null : { version: 4, content: { schemaVersion: 1, hero: {}, navigation } }),
  };
  vm.runInContext(published, context);
  await context.window.leanSitePublishedReady;
  return { links: nav.querySelectorAll('a'), nav, button, state: context.document.body.dataset.contentState };
}

test('published navigation retains editorial order, product entry and member button', async () => {
  const result = await render([{ label: '活动', href: '/events' }, { label: '高校', href: '/schools' }]);
  assert.deepEqual(result.links.map(link => [link.textContent, link.href]), [['活动', '/events'], ['高校', '/schools'], ['薄肌设计', '/design/']]);
  assert.equal(result.nav.children.at(-1), result.button);
  assert.equal(result.state, 'published');
});

test('full CMS navigation reserves one of six slots even when design is eighth', async () => {
  const entries = Array.from({ length: 7 }, (_, i) => ({ label: `栏目 ${i}`, href: `https://example.invalid/${i}` }));
  entries.push({ label: '旧名字', href: '/design' });
  const result = await render(entries);
  assert.equal(result.links.length, 6);
  assert.deepEqual(result.links.slice(0, 5).map(link => link.textContent), entries.slice(0, 5).map(item => item.label));
  assert.equal(result.links[5].href, '/design/');
  assert.equal(result.links[5].textContent, '薄肌设计');
});

test('same-origin product routes are deduplicated and keep the first CMS position', async () => {
  const result = await render([{ label: '设计', href: 'https://www.leanschool.net/design?from=nav' },
    { label: '活动', href: '/events' }, { label: '重复', href: '/design/' }, { label: '重复活动', href: '/events' }]);
  assert.deepEqual(result.links.map(link => link.href), ['/design/', '/events']);
});

test('invalid navigation fields are ignored; published labels never become HTML', async () => {
  const result = await render([null, {}, { label: 'unsafe', href: 'javascript:alert(1)' },
    { label: 'private', href: '/admin-api/system/user' }, { label: '<img src=x>', href: '/schools' }]);
  assert.deepEqual(result.links.map(link => link.href), ['/schools', '/design/']);
  assert.equal(result.links[0].textContent, '<img src=x>');
});

test('empty or unavailable published snapshots preserve the built-in entry', async () => {
  for (const options of [{ empty: true }, { fail: true }]) {
    const result = await render([], options);
    assert.equal(result.links.length, 1);
    assert.equal(result.links[0].href, '/design/');
    assert.equal(result.nav.children.at(-1), result.button);
  }
});
