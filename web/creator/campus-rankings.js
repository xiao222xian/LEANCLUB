(function (root) {
  'use strict';
  const china = region => /中国|香港|澳门|台湾/.test(region);
  function prepare(published, catalog = []) {
    const seed = new Map(catalog.map(s => [s.id, s]));
    const result = published.filter(s => typeof s.id === 'string' && Number.isSafeInteger(s.count) && s.count >= 0).map(s => {
      const meta = seed.get(s.id)?.name === s.name ? seed.get(s.id) : {};
      return { ...meta, ...s, en: s.en ?? meta.en ?? '', region: s.region ?? meta.region ?? '', alias: s.alias ?? meta.alias ?? '' };
    }).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, 'zh-CN') || a.id.localeCompare(b.id));
    let previous = null, rank = 0;
    result.forEach((s, i) => { if (s.count !== previous) rank = i + 1; s.rank = rank; previous = s.count; });
    return result;
  }
  function filter(schools, region, query) {
    const q = query.trim().normalize('NFKC').toLocaleLowerCase();
    return schools.filter(s => (region === 'all' || (region === 'china' ? china(s.region) : s.region && !china(s.region))) && `${s.name} ${s.en} ${s.alias}`.normalize('NFKC').toLocaleLowerCase().includes(q));
  }
  if (typeof module !== 'undefined' && module.exports) { module.exports = { prepare, filter }; return; }
  const $ = s => document.querySelector(s), node = root.LeanClubPortal.node;
  const nf = new Intl.NumberFormat('zh-CN'), params = new URLSearchParams(location.search);
  let schools = [], region = 'all', limit = 25, detailVersion = 0, loadVersion = 0;
  const dialog = $('#rank-dialog'), list = $('#campus-rank-list');
  function switchTab(name, update = true) {
    const ranking = name === 'ranking';
    $('#campus-ranking').hidden = !ranking; $('#ranking').hidden = ranking;
    for (const tab of ['ranking', 'directory']) { const el = $('#tab-' + tab); el.setAttribute('aria-selected', String(tab === name)); el.tabIndex = tab === name ? 0 : -1; }
    if (update) { const url = new URL(location.href); url.searchParams.set('view', name); history.replaceState(null, '', url); }
  }
  for (const name of ['ranking', 'directory']) {
    $('#tab-' + name).addEventListener('click', () => switchTab(name));
    $('#tab-' + name).addEventListener('keydown', e => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) { e.preventDefault(); const next = e.key === 'Home' ? 'ranking' : e.key === 'End' ? 'directory' : name === 'ranking' ? 'directory' : 'ranking'; switchTab(next); $('#tab-' + next).focus(); } });
  }
  switchTab(params.get('schoolId') || params.get('keyword') || params.get('view') === 'directory' ? 'directory' : 'ranking', false);
  function short(s) { return s.name === '北京师范大学—香港浸会大学联合国际学院' ? '北师港浸大 · UIC' : s.name; }
  function card(s) {
    const button = node('button', 'rank-card'); button.type = 'button';
    button.append(node('span', 'rank-card-number', String(s.rank).padStart(2, '0')), node('span', 'campus-kicker', 'CAMPUS / ' + String(s.rank).padStart(2, '0')), node('h3', '', short(s)), node('p', '', s.region || '学校社群'));
    const bottom = node('div', 'rank-card-footer'); bottom.append(node('strong', '', nf.format(s.count)), node('small', '', '社群规模'), node('span', '', '↗')); button.append(bottom);
    button.setAttribute('aria-label', `第${s.rank}名 ${s.name}，社群规模${s.count}，查看资料`); button.addEventListener('click', () => show(s)); return button;
  }
  function render() {
    const selected = filter(schools, region, $('#rank-query').value);
    $('#rank-featured').replaceChildren(...selected.slice(0, 3).map(card));
    $('#rank-featured').hidden = !selected.length;
    list.replaceChildren();
    selected.slice(0, limit).forEach(s => {
      const item = node('div'); item.setAttribute('role', 'listitem');
      const button = node('button', 'rank-row'); button.type = 'button';
      const label = node('span'); label.append(node('strong', 'rank-name', short(s)), node('small', 'rank-sub', [s.region, s.en].filter(Boolean).join(' / ')));
      button.append(node('span', 'rank-position', String(s.rank).padStart(2, '0')), label, node('span', 'rank-count', nf.format(s.count)), node('span', 'rank-arrow', '↗'));
      button.setAttribute('aria-label', `第${s.rank}名 ${s.name}，社群规模${s.count}，查看资料`); button.addEventListener('click', () => show(s)); item.append(button); list.append(item);
    });
    $('#rank-total-count').textContent = nf.format(selected.length);
    $('#rank-status').hidden = !!selected.length; $('#rank-status').textContent = schools.length ? '没有找到这所学校，试试全称或简称。' : '榜单暂未收录社群。';
    $('#rank-more').hidden = selected.length <= limit;
  }
  async function load() {
    const request = ++loadVersion;
    $('#rank-retry').hidden = true; $('#rank-status').hidden = false; $('#rank-status').textContent = '正在读取榜单…';
    try {
      const response = await fetch('/api/club/schools', { cache: 'no-store', signal: AbortSignal.timeout(15000) });
      const data = await response.json();
      if (!response.ok || !Array.isArray(data.published)) throw new Error();
      if (request !== loadVersion) return;
      schools = prepare(data.published, data.schools || []); $('#campus-total').textContent = nf.format(schools.length); render();
      const direct = schools.find(s => s.id === params.get('club')); if (direct) { params.delete('club'); show(direct); }
    } catch (_) { if (request !== loadVersion) return; $('#rank-status').hidden = false; $('#rank-status').textContent = '榜单暂时无法读取，请重试。'; $('#rank-retry').hidden = false; }
  }
  async function show(s) {
    const request = ++detailVersion, detail = $('#rank-detail');
    const title = node('h2', '', s.name); title.id = 'rank-detail-title';
    detail.replaceChildren(node('p', 'campus-kicker', 'YOUR CAMPUS / LEAN SCHOOL'), title, node('p', 'rank-detail-sub', [s.region, s.en].filter(Boolean).join(' / ')));
    const stats = node('div', 'rank-detail-stats');
    for (const [label, value] of [['全榜排名', '#' + s.rank], ['社群规模', nf.format(s.count)]]) { const d = node('div'); d.append(node('small', '', label), node('strong', '', value)); stats.append(d); }
    const content = node('div'); content.append(node('p', 'rank-detail-sub', '正在读取联系资料…')); detail.append(stats, content);
    if (s.schoolId) { const link = node('a', 'rank-detail-link', '查看学校目录与俱乐部 ↗'); link.href = '/schools?schoolId=' + encodeURIComponent(s.schoolId); detail.append(link); }
    if (!dialog.open) dialog.showModal();
    try {
      const response = await fetch('/api/club/profile/' + encodeURIComponent(s.id), { cache: 'no-store', signal: AbortSignal.timeout(12000) });
      const result = await response.json();
      if (!response.ok || !result.club) throw new Error(result.error || '资料暂时无法读取，请稍后重试。');
      if (request !== detailVersion || !dialog.open) return;
      content.replaceChildren();
      const club = result.club;
      if (club.contact) content.append(node('p', 'campus-kicker', '联系社群'), node('p', 'rank-contact', club.contact));
      if (typeof club.qr === 'string' && /^data:image\/(png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(club.qr) && club.qr.length < 3 * 1024 * 1024) { const image = node('img', 'rank-qr'); image.src = club.qr; image.alt = s.name + '社群二维码'; content.append(image); }
      if (!club.contact && !club.qr) content.append(node('p', 'rank-detail-sub', '本校暂未公开联系资料。'));
      content.append(node('p', 'rank-detail-sub', '社群自行提交的资料，联系前请确认最新信息。'));
    } catch (error) { if (request === detailVersion && dialog.open) { content.replaceChildren(node('p', 'rank-detail-sub', error.message)); const retry = node('button', 'light-button', '重新加载'); retry.type = 'button'; retry.addEventListener('click', () => show(s)); content.append(retry); } }
  }
  dialog.querySelector('.rank-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { detailVersion++; });
  dialog.addEventListener('click', e => { if (e.target === dialog) { const r = dialog.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close(); } });
  document.querySelectorAll('[data-rank-region]').forEach(button => button.addEventListener('click', () => { region = button.dataset.rankRegion; limit = 25; document.querySelectorAll('[data-rank-region]').forEach(b => b.setAttribute('aria-pressed', String(b === button))); render(); }));
  $('#rank-query').addEventListener('input', () => { limit = 25; render(); });
  $('#rank-more').addEventListener('click', () => { limit += 25; render(); }); $('#rank-retry').addEventListener('click', load);
  load();
})(typeof window === 'undefined' ? globalThis : window);
