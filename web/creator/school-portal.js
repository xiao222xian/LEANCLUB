(() => {
  'use strict';
  const { api, node } = window.LeanClubPortal;
  const search = document.querySelector('#school-search'), list = document.querySelector('#school-rows');
  const count = document.querySelector('#result-count'), more = document.querySelector('#directory-more');
  const panel = document.querySelector('.school-panel'), detail = document.querySelector('#school-content');
  const params = new URLSearchParams(location.search);
  let version = 0, detailVersion = 0, timer = null, page = 1;
  search.value = params.get('keyword') || '';
  function row(school) {
    const item = node('article', 'campus-entry portal-directory-row'); item.setAttribute('role', 'listitem');
    const button = node('button', 'school-button'); button.type = 'button';
    const monogram = node('span', 'portal-school-monogram', school.name?.slice(0, 1) || '校'); monogram.setAttribute('aria-hidden', 'true');
    const label = node('span', 'school-label'); label.append(node('strong', '', school.name), node('small', '', [...new Set([school.province, school.city, school.level].filter(Boolean))].join(' · ')));
    button.append(monogram, label); button.addEventListener('click', () => showSchool(school));
    const action = node('button', 'portal-directory-action'); action.type = 'button';
    action.append(node('span', Number(school.clubCount) > 0 ? 'lc-club-badge' : 'lc-campus-badge', Number(school.clubCount) > 0 ? '已开通俱乐部' : '学校目录'), node('span', 'portal-arrow', '↗'));
    action.setAttribute('aria-label', '查看' + school.name); action.addEventListener('click', () => showSchool(school));
    item.append(button, action); return item;
  }
  async function load(append = false) {
    const current = ++version;
    if (!append) { page = 1; list.replaceChildren(node('p', 'portal-directory-empty', '正在读取学校目录…')); }
    more.hidden = true; document.querySelector('#directory-error').hidden = true;
    try {
      const data = await window.LeanSchoolDirectory.list({ keyword: search.value.trim(), pageNo: page, pageSize: 20 });
      if (current !== version) return;
      if (!append) list.replaceChildren();
      const schools = Array.isArray(data?.list) ? data.list : [];
      schools.forEach(school => list.append(row(school)));
      count.textContent = Number(data?.total || 0) ? `找到 ${Number(data.total)} 所学校` : '没有匹配的学校';
      if (!schools.length && !append) list.append(node('p', 'portal-directory-empty', '暂未找到这所学校。试试学校全称，或稍后再选。'));
      more.hidden = page * 20 >= Number(data?.total || 0);
      const directId = params.get('schoolId');
      if (directId && !append) {
        const found = schools.find(school => String(school.id) === directId);
        if (found) { params.delete('schoolId'); showSchool(found); }
        else if (!search.value) {
          const detailSchool = await api('/leanclub/school/get?id=' + encodeURIComponent(directId)).catch(() => null);
          if (detailSchool && current === version) { params.delete('schoolId'); showSchool(detailSchool); }
        }
      }
    } catch (error) {
      if (current !== version) return;
      if (!append) list.replaceChildren();
      count.textContent = '';
      document.querySelector('#directory-error').hidden = false;
      document.querySelector('#directory-error-message').textContent = error.message;
    }
  }
  async function showSchool(school) {
    const current = ++detailVersion;
    detail.replaceChildren(node('p', 'lc-kicker', 'YOUR CAMPUS / LEAN SCHOOL'));
    const title = node('h2', '', school.name); title.id = 'panel-title';
    const place = node('p', 'lc-muted', [...new Set([school.province, school.city, school.level].filter(Boolean))].join(' · '));
    const save = node('button', 'mint-button', '设为我的学校 ↗'); save.type = 'button';
    save.addEventListener('click', () => { panel.close(); window.LeanClubPortal.saveSchool(school); });
    const status = node('p', 'lc-status'); status.setAttribute('role', 'status');
    const body = node('div', 'portal-club-list'); body.append(node('p', 'lc-muted', '正在读取俱乐部…'));
    detail.append(title, place, save, status, body);
    if (!panel.open) panel.showModal();
    try {
      const [clubs, legacy] = await Promise.all([
        api('/leanclub/club/list?schoolId=' + encodeURIComponent(school.id)),
        fetch('/api/club/schools', { cache: 'no-store', signal: AbortSignal.timeout(10000) }).then(r => r.ok ? r.json() : { published: [] }).catch(() => ({ published: [] }))
      ]);
      if (current !== detailVersion || !panel.open) return;
      body.replaceChildren();
      const existing = (legacy.published || []).filter(c => String(c.schoolId) === String(school.id));
      existing.forEach(club => {
        const card = node('article', 'portal-club-detail');
        card.append(node('span', 'lc-campus-badge', '社群榜单'), node('h3', '', club.name), node('p', '', '社群规模 ' + Number(club.count).toLocaleString('zh-CN')));
        const link = node('a', 'portal-apply-link', '联系社群 / 查看二维码 ↗'); link.href = '/schools?club=' + encodeURIComponent(club.id); card.append(link); body.append(card);
      });
      if (!Array.isArray(clubs) || !clubs.length) {
        if (existing.length) return;
        const empty = node('div', 'portal-club-empty');
        empty.append(node('h3', '', '本校暂未开通'), node('p', '', '你仍然可以保存学校、参加开放活动，或申请发起本校俱乐部。'));
        const apply = node('a', 'portal-apply-link', '申请发起 ↗'); apply.href = '/join?schoolId=' + encodeURIComponent(school.id) + '&schoolName=' + encodeURIComponent(school.name);
        empty.append(apply); body.append(empty); return;
      }
      clubs.forEach(club => {
        const card = node('article', 'portal-club-detail');
        card.append(node('span', 'lc-club-badge', '已开通'), node('h3', '', club.name), node('p', '', club.introduction));
        if (club.contactValue) { const contact = node('div', 'portal-club-contact'); contact.append(node('small', '', club.contactName ? '联系 ' + club.contactName : '俱乐部联系方式'), node('p', '', club.contactValue)); card.append(contact); }
        else card.append(node('p', 'lc-muted', '联系信息暂未公开，请稍后再来。'));
        body.append(card);
      });
    } catch (error) { if (current === detailVersion && panel.open) body.replaceChildren(node('p', 'lc-status is-error', error.message)); }
  }
  search.addEventListener('input', () => { clearTimeout(timer); version += 1; timer = setTimeout(() => load(), 300); });
  document.querySelector('#directory-search').addEventListener('submit', event => { event.preventDefault(); clearTimeout(timer); load(); });
  document.querySelector('#clear-search').addEventListener('click', () => { search.value = ''; load(); search.focus(); });
  document.querySelector('#directory-retry').addEventListener('click', () => load());
  more.addEventListener('click', () => { page += 1; load(true); });
  document.querySelector('.close-panel').addEventListener('click', () => panel.close());
  panel.addEventListener('close', () => { detailVersion += 1; });
  panel.addEventListener('click', event => { if (event.target === panel) panel.close(); });
  document.querySelector('[data-directory-select]').addEventListener('click', () => window.LeanClubPortal.showMember());
  async function legacyDetail(id) {
    if (!/^[a-zA-Z0-9_-]{1,80}$/.test(id)) return;
    const current = ++detailVersion;
    detail.replaceChildren(node('p', 'lc-kicker', 'EXISTING COMMUNITY'));
    const title = node('h2', '', '既有社群资料'); title.id = 'panel-title'; detail.append(title, node('p', 'lc-muted', '历史资料独立保留，尚未绑定标准学校或完成新平台审核。'));
    panel.showModal();
    try {
      const response = await fetch('/api/club/profile/' + encodeURIComponent(id), { cache: 'no-store', signal: AbortSignal.timeout(10000) });
      const result = await response.json();
      if (current !== detailVersion || !panel.open) return;
      if (!response.ok || !result.club) throw new Error('这份社群资料暂时无法读取。');
      title.textContent = result.club.name || '既有社群';
      if (result.club.contact) detail.append(node('p', 'portal-legacy-contact', result.club.contact));
      if (result.club.qr && window.LeanClubPortal.safeHref(result.club.qr, true)) { const img = node('img', 'portal-legacy-qr'); img.src = window.LeanClubPortal.safeHref(result.club.qr, true); img.alt = '历史社群公开二维码'; detail.append(img); }
    } catch (error) { if (current === detailVersion && panel.open) detail.append(node('p', 'lc-status is-error', error.message)); }
  }
  load();
  if (params.get('school')) legacyDetail(params.get('school'));
})();
