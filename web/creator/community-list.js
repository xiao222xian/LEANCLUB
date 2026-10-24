(() => {
  'use strict';
  const { request, node, types } = window.LeanDirectory, $ = s => document.querySelector(s);
  const params = new URLSearchParams(location.search), nf = new Intl.NumberFormat('zh-CN');
  const list = $('#directory-list'), query = $('#directory-query'), filter = $('#directory-filter'), dialog = $('#profile-dialog');
  let items = [], view = params.get('schoolId') || params.get('keyword') ? 'directory' : params.get('view') || 'ranking';
  if (!['ranking', 'all', 'directory'].includes(view)) view = 'ranking';
  let limit = 25, page = 1, generation = 0, detailGeneration = 0, timer, loaded = false, catalogTotal = 0, catalogRows = [];
  query.value = params.get('keyword') || '';
  const normalized = s => String(s || '').normalize('NFKC').toLowerCase();
  const matches = s => normalized([s.name, s.en, s.alias, s.country, s.city, s.schoolName].join(' ')).includes(normalized(query.value.trim()));
  const locationText = s => [...new Set([s.country, s.city].filter(Boolean))].join(' · ');
  function addLink() { const a = $('#directory-add'); a.href = '/join?' + new URLSearchParams({ name: query.value.trim(), kind: view === 'directory' ? 'school' : 'group' }); a.textContent = query.value.trim() ? '添加「' + query.value.trim() + '」 ↗' : '没有找到？添加资料 ↗'; }
  function row(s, ranking = false) {
    const wrapper = node('div'); wrapper.setAttribute('role', 'listitem');
    const b = node('button', 'directory-row'); b.type = 'button';
    const name = node('span'); name.append(node('strong', 'directory-name', s.name), node('small', 'directory-meta', locationText(s) || s.en || types[s.kind]));
    b.append(node('span', 'directory-position', ranking ? s.rank ? String(s.rank).padStart(2, '0') : '—' : s.kind === 'school' ? '校' : s.kind === 'person' ? '人' : '群'), name,
      node('span', ranking ? 'directory-count' : 'directory-type', ranking ? s.count ? nf.format(s.count) : '—' : types[s.kind]), node('span', 'directory-arrow', '↗'));
    b.addEventListener('click', () => s.catalog ? showCatalog(s) : show(s.id)); wrapper.append(b); return wrapper;
  }
  function status(text = '') { $('#directory-status').hidden = !text; $('#directory-status').textContent = text; }
  function render() {
    addLink(); if (!loaded) return;
    let rows = items;
    if (view === 'ranking') {
      rows = rows.filter(x => x.kind === 'group').sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, 'zh-CN'));
      let last = null, rank = 0;
      rows = rows.map((s, i) => { if (s.count !== last) rank = i + 1; last = s.count; return { ...s, rank: s.count > 0 ? rank : null }; });
    } else if (view === 'directory') rows = rows.filter(x => x.kind === 'school');
    rows = rows.filter(matches).filter(s => {
      if (view === 'all') return filter.value === 'all' || s.kind === filter.value;
      const domestic = /中国|香港|澳门|台湾/.test(s.country || '');
      return filter.value === 'all' || (filter.value === 'china' ? domestic : s.country && !domestic);
    });
    if (view === 'directory') {
      // Community schools and the national catalogue share one list without dropping overseas entries.
      const known = new Set(catalogRows.map(s => normalized(s.name) + '|中国|' + normalized(s.city)));
      const extra = rows.filter(s => !known.has(normalized(s.name) + '|' + s.country + '|' + normalized(s.city)));
      rows = [...catalogRows, ...extra];
      $('#directory-count').textContent = `${nf.format(catalogTotal + extra.length)} 所学校`;
      $('#directory-more').hidden = page * 20 >= catalogTotal;
    } else {
      $('#directory-count').textContent = `${nf.format(rows.length)} 份资料`;
      $('#directory-more').hidden = rows.length <= limit;
    }
    list.replaceChildren(...(view === 'directory' ? rows : rows.slice(0, limit)).map(s => row(s, view === 'ranking')));
    status(rows.length ? '' : '还没有找到匹配资料');
  }
  async function loadCatalog(append = false) {
    const g = ++generation; if (!append) { page = 1; catalogRows = []; catalogTotal = 0; }
    status('正在加载…'); $('#directory-retry').hidden = true; $('#directory-more').disabled = true;
    try {
      // Overseas user submissions remain visible even when no national-catalogue match exists.
      const data = filter.value === 'overseas' ? { list: [], total: 0 } : await window.LeanSchoolDirectory.list({ keyword: query.value.trim(), pageNo: page, pageSize: 20 });
      if (g !== generation || view !== 'directory') return;
      catalogTotal = data.total; catalogRows.push(...data.list.map(s => ({ ...s, kind: 'school', country: '中国', catalog: true })));
      render();
    } catch (error) { if (g === generation) { if (append) page--; render(); status(error.message); $('#directory-retry').hidden = false; } }
    finally { if (g === generation) $('#directory-more').disabled = false; }
  }
  function switchView(next, update = true) {
    view = next; generation++; limit = 25;
    document.querySelectorAll('[data-view]').forEach(b => { b.setAttribute('aria-selected', String(b.dataset.view === view)); b.tabIndex = b.dataset.view === view ? 0 : -1; });
    $('#directory-panel').setAttribute('aria-labelledby', 'tab-' + view);
    filter.replaceChildren(...(view === 'all' ? [['all', '全部类型'], ...Object.entries(types)] : [['all', '全部地区'], ['china', '国内'], ['overseas', '海外']]).map(([v, t]) => { const o = node('option', '', t); o.value = v; return o; }));
    $('#directory-note').textContent = view === 'ranking' ? '人数由社群填报' : view === 'directory' ? '广深高校优先' : '';
    if (update) { const url = new URL(location.href); url.searchParams.set('view', view); url.searchParams.delete('schoolId'); url.searchParams.delete('club'); history.replaceState(null, '', url); }
    if (loaded && view === 'directory') loadCatalog(); else render();
  }
  function beginDetail(name, kind, place) {
    const content = $('#profile-content'), h = node('h2', '', name); h.id = 'profile-title';
    content.replaceChildren(node('span', 'profile-type', types[kind] || '资料'), h, node('p', 'profile-place', place));
    if (!dialog.open) dialog.showModal(); return content;
  }
  function linkedGroups(content, groups) {
    for (const group of groups) { const b = node('button', 'profile-group text-action', group.name); b.type = 'button'; b.addEventListener('click', () => show(group.id)); content.append(b); }
  }
  async function show(id) {
    const g = ++detailGeneration, content = beginDetail('正在读取…', '', '');
    try {
      const data = await request('/api/directory/' + encodeURIComponent(id)); if (g !== detailGeneration || !dialog.open) return;
      const p = data.profile; beginDetail(p.name, p.kind, locationText(p));
      if (p.en) content.append(node('p', 'profile-place', p.en));
      if (p.description) content.append(node('p', 'profile-copy', p.description));
      if (p.kind === 'group' && p.count) content.append(node('p', 'profile-place', `${nf.format(p.count)} 人 · 社群填报`));
      if (p.schoolName) content.append(node('p', 'profile-place', p.schoolName));
      if (p.contact || p.qr) {
        const contact = node('div', 'profile-contact'); contact.append(node('small', '', '联系资料'));
        if (p.contact) contact.append(node('p', '', p.contact));
        if (/^data:image\/(png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(p.qr)) { const image = node('img', 'profile-qr'); image.src = p.qr; image.alt = p.qrLabel ? p.qrLabel + '二维码' : '联系二维码'; contact.append(image); }
        if (p.qrLabel) contact.append(node('p', 'profile-place', p.qrLabel)); content.append(contact);
      }
      if (p.kind === 'school') {
        linkedGroups(content, items.filter(x => x.kind === 'group' && (x.schoolRef === id || !x.schoolRef && x.schoolName === p.name && x.country === p.country && x.city === p.city)));
        const a = node('a', 'text-action profile-group', '添加本校团体 ↗'); a.href = '/join?' + new URLSearchParams({ schoolName: p.name, country: p.country, city: p.city, kind: 'group' }); content.append(a);
      }
    } catch (error) { if (g === detailGeneration && dialog.open) { $('#profile-title').textContent = '暂时无法读取'; content.append(node('p', 'error', error.message)); } }
  }
  async function showCatalog(s) {
    const g = ++detailGeneration, content = beginDetail(s.name, 'school', [s.city, s.level].filter(Boolean).join(' · '));
    linkedGroups(content, items.filter(x => x.kind === 'group' && (String(x.schoolId) === String(s.id) || x.schoolName === s.name && /中国/.test(x.country))));
    const a = node('a', 'text-action profile-group', '添加本校团体 ↗'); a.href = '/join?' + new URLSearchParams({ schoolName: s.name, schoolId: s.id, country: '中国', city: s.city || '', kind: 'group' }); content.append(a);
    try {
      const clubs = await window.LeanClubPortal.api('/leanclub/club/list?schoolId=' + encodeURIComponent(s.id));
      if (g !== detailGeneration || !dialog.open) return;
      for (const club of clubs || []) { const d = node('div', 'profile-group'); d.append(node('strong', '', club.name)); if (club.introduction) d.append(node('p', 'profile-place', club.introduction)); if (club.contactValue) d.append(node('p', '', club.contactValue)); content.insertBefore(d, a); }
    } catch { /* School and shared-directory groups remain available without the optional member service. */ }
  }
  async function load() {
    $('#directory-retry').hidden = true; status('正在加载…');
    try {
      items = (await request('/api/directory')).items; loaded = true;
      if (view === 'directory') await loadCatalog(); else render();
      const direct = params.get('club') || params.get('school');
      if (direct) { params.delete('club'); params.delete('school'); show(direct); }
      if (params.get('schoolId')) { const id = params.get('schoolId'); params.delete('schoolId'); const s = await window.LeanClubPortal.api('/leanclub/school/get?id=' + encodeURIComponent(id)); if (s) showCatalog(s); }
    } catch (error) { status(error.message); $('#directory-retry').hidden = false; }
  }
  document.querySelectorAll('[data-view]').forEach((b, index, tabs) => {
    b.addEventListener('click', () => switchView(b.dataset.view));
    b.addEventListener('keydown', e => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return; e.preventDefault(); const next = e.key === 'Home' ? 0 : e.key === 'End' ? 2 : (index + (e.key === 'ArrowRight' ? 1 : 2)) % 3; switchView(tabs[next].dataset.view); tabs[next].focus(); });
  });
  query.addEventListener('input', () => { clearTimeout(timer); generation++; limit = 25; addLink(); if (view === 'directory') timer = setTimeout(() => loadCatalog(), 300); else render(); });
  filter.addEventListener('change', () => { limit = 25; if (view === 'directory') loadCatalog(); else render(); });
  $('#directory-more').addEventListener('click', () => { if (view === 'directory') { page++; loadCatalog(true); } else { limit += 25; render(); } });
  $('#directory-retry').addEventListener('click', load); dialog.querySelector('.dialog-close').addEventListener('click', () => dialog.close()); dialog.addEventListener('close', () => detailGeneration++);
  switchView(view, false); load();
})();
