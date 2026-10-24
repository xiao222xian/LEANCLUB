/* Homepage suggestions use the same public school dictionary as /schools. */
(() => {
  'use strict';
  const form = document.querySelector('.hero-search');
  const input = document.querySelector('#hero-school');
  const status = document.querySelector('.hero-search-status');
  const portal = window.LeanClubPortal;
  if (!form || !input || !portal) return;
  const { api, node } = portal;
  const popup = node('div', 'home-school-popup');
  popup.hidden = true;
  const heading = node('div', 'home-school-heading');
  const caption = node('span', '', '国内高校目录');
  const total = node('span', 'home-school-total');
  heading.append(caption, total);
  const results = node('div', 'home-school-results');
  results.id = 'hero-school-results';
  results.setAttribute('role', 'listbox');
  results.setAttribute('aria-label', '匹配的学校');
  const message = node('div', 'home-school-message');
  const messageText = node('p');
  const retry = node('button', 'home-school-retry', '重新加载 ↗');
  retry.type = 'button'; retry.hidden = true;
  message.append(messageText, retry);
  const all = node('a', 'home-school-all', '查看完整学校目录 ↗');
  all.href = '/schools';
  const note = node('p', 'home-school-note', '选择学校，查看本校俱乐部与发起入口。');
  popup.append(heading, results, message, all, note);
  // Outside the hero's clipping path so the list stays usable on small screens.
  document.body.append(popup);
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-controls', results.id);
  input.setAttribute('aria-expanded', 'false');
  let timer = null, version = 0, items = [], active = -1, composing = false;
  let loadedQuery = null;
  const query = () => input.value.trim();
  const directoryUrl = () => query() ? '/schools?' + new URLSearchParams({ keyword: query() }) : '/schools';

  function position() {
    if (popup.hidden) return;
    const rect = form.getBoundingClientRect();
    const viewport = window.visualViewport;
    const top = viewport?.offsetTop || 0;
    const bottom = top + (viewport?.height || window.innerHeight);
    if (rect.bottom < top || rect.top > bottom) { close(); return; }
    const below = bottom - rect.bottom - 20;
    const above = rect.top - top - 20;
    const upward = below < 220 && above > below;
    popup.style.left = `${window.scrollX + rect.left}px`;
    popup.style.width = `${rect.width}px`;
    popup.style.maxHeight = `${Math.max(120, Math.min(440, upward ? above : below))}px`;
    popup.style.top = `${window.scrollY + (upward ? rect.top - popup.getBoundingClientRect().height - 8 : rect.bottom + 8)}px`;
    popup.dataset.placement = upward ? 'above' : 'below';
  }
  function open() {
    popup.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    position();
  }
  function close() {
    clearTimeout(timer); version += 1;
    popup.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    active = -1;
  }
  function select(index) {
    const school = items[index];
    if (!school || loadedQuery !== query()) return;
    close();
    location.assign('/schools?schoolId=' + encodeURIComponent(school.id));
  }
  function highlight(index) {
    active = index;
    Array.from(results.children).forEach((option, i) => {
      option.setAttribute('aria-selected', String(i === active));
      if (i === active) {
        input.setAttribute('aria-activedescendant', option.id);
        option.scrollIntoView({ block: 'nearest', behavior: 'instant' });
      }
    });
  }
  function resetResults() {
    items = []; active = -1; loadedQuery = null;
    results.replaceChildren();
    input.removeAttribute('aria-activedescendant');
    total.textContent = '';
  }
  function pending() {
    resetResults();
    message.hidden = false; messageText.textContent = '正在查找你的学校…'; retry.hidden = true;
    results.setAttribute('aria-busy', 'true');
    all.href = directoryUrl();
    all.textContent = query() ? '查看全部搜索结果 ↗' : '查看完整学校目录 ↗';
    status.textContent = '';
    open();
  }
  async function search() {
    clearTimeout(timer);
    const current = ++version, term = query();
    pending();
    try {
      const data = await (window.LeanSchoolDirectory
        ? window.LeanSchoolDirectory.list({ keyword: term, pageNo: 1, pageSize: 6 })
        : api('/leanclub/school/list?' + new URLSearchParams({ keyword: term, pageNo: '1', pageSize: '6' })));
      if (current !== version || popup.hidden || query() !== term) return;
      items = Array.isArray(data?.list) ? data.list.filter(item => item && /^\d+$/.test(String(item.id)) && typeof item.name === 'string') : [];
      loadedQuery = term;
      const count = Number(data?.total) || 0;
      total.textContent = `${count.toLocaleString('zh-CN')} 所`;
      message.hidden = items.length > 0;
      messageText.textContent = '暂未找到这所学校，试试学校全称或城市名称。';
      items.forEach((school, index) => {
        const option = node('button', 'home-school-option');
        option.type = 'button'; option.tabIndex = -1;
        option.id = `hero-school-option-${index}`;
        option.setAttribute('role', 'option'); option.setAttribute('aria-selected', 'false');
        const monogram = node('span', 'home-school-monogram', school.name.slice(0, 1));
        monogram.setAttribute('aria-hidden', 'true');
        const copy = node('span', 'home-school-copy');
        copy.append(node('strong', '', school.name), node('small', '', [...new Set([school.province, school.city, school.level].filter(Boolean))].join(' · ')));
        const badge = node('span', Number(school.clubCount) > 0 ? 'home-school-badge is-open' : 'home-school-badge', Number(school.clubCount) > 0 ? '俱乐部已开通' : '学校目录');
        option.append(monogram, copy, badge);
        option.addEventListener('click', () => select(index));
        results.append(option);
      });
      status.textContent = items.length ? `找到 ${count} 所学校，可选择下方学校查看详情。` : '没有匹配的学校。';
      all.textContent = term ? `查看全部 ${count} 所匹配学校 ↗` : '查看完整学校目录 ↗';
    } catch (error) {
      if (current !== version || popup.hidden) return;
      message.hidden = false; messageText.textContent = '学校目录暂时未连接，请重试。'; retry.hidden = false;
      status.textContent = '学校目录加载失败，可以重试或进入学校目录。';
    } finally {
      if (current === version) { results.setAttribute('aria-busy', 'false'); position(); }
    }
  }
  input.addEventListener('focus', () => { if (popup.hidden) search(); });
  input.addEventListener('click', () => { if (popup.hidden) search(); });
  function schedule() {
    clearTimeout(timer); version += 1;
    pending();
    if (!composing) timer = setTimeout(search, 240);
  }
  input.addEventListener('input', schedule);
  input.addEventListener('compositionstart', () => { composing = true; clearTimeout(timer); });
  input.addEventListener('compositionend', () => { composing = false; schedule(); });
  input.addEventListener('keydown', event => {
    if (composing || event.isComposing || event.keyCode === 229) {
      if (event.key === 'Enter') event.preventDefault();
      return;
    }
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (popup.hidden) { search(); return; }
      if (items.length) highlight(event.key === 'ArrowDown' ? (active + 1) % items.length : (active < 0 ? items.length - 1 : (active - 1 + items.length) % items.length));
    }
    if (event.key === 'Enter' && active >= 0 && !popup.hidden) { event.preventDefault(); select(active); }
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (composing) return;
    if (active >= 0 && !popup.hidden) { select(active); return; }
    const exact = loadedQuery === query() ? items.findIndex(item => item.name === query()) : -1;
    if (exact >= 0) select(exact);
    else { close(); location.assign(directoryUrl()); }
  });
  retry.addEventListener('click', search);
  document.addEventListener('pointerdown', event => { if (!form.contains(event.target) && !popup.contains(event.target)) close(); });
  document.addEventListener('focusin', event => { if (!form.contains(event.target) && !popup.contains(event.target)) close(); });
  window.addEventListener('resize', position);
  window.addEventListener('scroll', position, { passive: true });
  window.visualViewport?.addEventListener('resize', position);
  window.visualViewport?.addEventListener('scroll', position);
})();
