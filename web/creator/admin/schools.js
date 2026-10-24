(() => {
  'use strict';
  const $ = s => document.querySelector(s), form = $('#club-editor-form'), dialog = $('#club-editor');
  const kinds = { school: '学校', group: '团体', person: '个人' }, states = { pending: '待审核', published: '已通过', changes: '待补充', hidden: '已隐藏' };
  let api, items = [], current = null, epoch = 0, version = 0, busy = false, limit = 50, historyPage = 1;
  const node = (tag, cls = '', text = '') => { const e = document.createElement(tag); e.className = cls; e.textContent = text; return e; };
  const date = s => new Date(s * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  const message = (selector, text = '') => { $(selector).textContent = text; $(selector).hidden = !text; };
  function reset() { epoch++; version++; items = []; current = null; busy = false; $('#clubs-list').replaceChildren(); $('#clubs-summary').textContent = ''; form.reset(); $('#club-edit-qr').removeAttribute('src'); $('#club-audit').replaceChildren(); $('#clubs-history-list').replaceChildren(); dialog.close(); }
  function render() {
    const q = $('#clubs-query').value.normalize('NFKC').toLowerCase();
    const selected = items.filter(x => [x.name, x.country, x.city].join(' ').normalize('NFKC').toLowerCase().includes(q) && ($('#clubs-kind').value === 'all' || x.kind === $('#clubs-kind').value) && ($('#clubs-visibility').value === 'all' || x.status === $('#clubs-visibility').value));
    $('#clubs-summary').textContent = `${items.length} 份 · ${items.filter(x => x.status === 'pending').length} 待审核`;
    $('#clubs-list').replaceChildren(...selected.slice(0, limit).map(x => {
      const b = node('button', 'club-admin-row'); b.type = 'button';
      const name = node('span'); name.append(node('strong', '', x.name), node('small', '', [x.country, x.city].filter(Boolean).join(' · ')));
      b.append(name, node('span', 'club-kind', kinds[x.kind]), node('span', 'club-status club-status-' + x.status, x.status === 'published' && !x.visible ? '未公开' : states[x.status]), node('span', 'club-edit-arrow', '↗'));
      b.addEventListener('click', () => open(x.id)); return b;
    }));
    if (!selected.length) $('#clubs-list').append(node('p', 'empty-copy', '没有匹配的资料'));
    $('#clubs-more').hidden = selected.length <= limit;
  }
  async function load() {
    const e = epoch, v = ++version; message('#clubs-error'); $('#clubs-refresh').disabled = true;
    try { const data = await api('/api/admin/directory'); if (e !== epoch || v !== version) return; items = data.items; render(); }
    catch (error) { if (e === epoch && v === version) message('#clubs-error', error.message); }
    finally { if (e === epoch && v === version) $('#clubs-refresh').disabled = false; }
  }
  function fields() {
    const f = form.elements, kind = f.kind.value;
    f.count.closest('label').hidden = kind !== 'group'; f.schoolName.closest('label').hidden = kind === 'school';
    f.contact.required = kind !== 'school';
  }
  function fill(data) {
    current = data; const p = data.profile, f = form.elements;
    form.reset();
    for (const k of ['kind', 'name', 'country', 'city', 'schoolName', 'count', 'en', 'description', 'contact']) f[k].value = p[k] ?? '';
    for (const k of ['profilePublic', 'contactPublic']) { f[k].checked = !!p[k]; f[k].disabled = !!data.id && !p[k]; }
    f.note.value = data.note || ''; fields();
    const image = $('#club-edit-qr'); image.removeAttribute('src'); image.hidden = true;
    if (/^data:image\/(png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(p.qr || '')) { image.src = p.qr; image.hidden = false; }
    $('#club-editor-title').textContent = data.id ? '资料详情' : '新增资料';
    $('#club-editor-source').textContent = data.id ? `${states[data.status]} · ${date(data.updated)}` : '';
    $('#club-save').textContent = data.status === 'pending' ? '通过并发布' : '保存并发布';
    $('#club-return').hidden = !data.id; $('#club-hide').hidden = !data.id;
    message('#club-similar', data.similar?.length ? '有同名资料：' + data.similar.map(x => x.name + ' · ' + x.country).join('；') : '');
    $('#club-audit').replaceChildren(...(data.audit || []).map(x => node('p', '', `${date(x.created)} · ${{ submit: '提交资料', publish: '审核发布', changes: '退回补充', hide: '隐藏资料', save: '编辑资料', qr: '更新二维码', 'linked-school': '关联学校' }[x.action] || '更新资料'} · ${x.actor === 'owner' ? '提交人' : x.actor}`)));
    message('#club-editor-error'); message('#club-save-status');
  }
  async function open(id) {
    const e = epoch, v = ++version;
    try { const data = await api('/api/admin/directory/' + encodeURIComponent(id)); if (e !== epoch || v !== version) return; fill(data); if (!dialog.open) dialog.showModal(); }
    catch (error) { if (e === epoch) message('#clubs-error', error.message); }
  }
  async function save(action) {
    if (busy || !current || action === 'publish' && !form.reportValidity()) return;
    const f = form.elements, p = { ...current.profile }, e = epoch;
    for (const k of ['kind', 'name', 'country', 'city', 'schoolName', 'en', 'description', 'contact']) p[k] = f[k].value.trim();
    p.count = Number(f.count.value || 0); p.profilePublic = f.profilePublic.checked; p.contactPublic = f.contactPublic.checked;
    if (p.schoolName !== current.profile.schoolName) p.schoolId = null;
    if (f.removeQr.checked) p.qr = '';
    const file = $('#club-qr-file').files[0];
    if (action === 'publish' && file && (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 2 * 1024 * 1024)) { message('#club-editor-error', '请选择2MB以内的PNG或JPG图片。'); return; }
    busy = true; for (const b of dialog.querySelectorAll('button')) b.disabled = true; message('#club-editor-error');
    try {
      if (action === 'publish' && file) p.qr = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file); });
      const data = await api('/api/admin/directory' + (current.id ? '/' + encodeURIComponent(current.id) : ''), { method: 'POST', body: { profile: p, revision: current.revision, action, note: f.note.value.trim() } });
      if (e !== epoch) return;
      fill(data); message('#club-save-status', action === 'changes' ? '已退回，提交人可在管理链接查看。' : '已保存'); await load();
    } catch (error) { if (e === epoch) message('#club-editor-error', error.message || '保存未完成，请重试。'); }
    finally { if (e === epoch) { busy = false; for (const b of dialog.querySelectorAll('button')) b.disabled = false; } }
  }
  form.addEventListener('submit', e => { e.preventDefault(); save('publish'); });
  form.elements.kind.addEventListener('change', fields);
  $('#club-return').addEventListener('click', () => save('changes')); $('#club-hide').addEventListener('click', () => save('hide'));
  $('#clubs-add').addEventListener('click', () => { fill({ id: null, revision: 0, profile: { kind: 'group', country: '中国', count: 0, profilePublic: true, contactPublic: false, qr: '', qrLabel: '', alias: '', schoolId: null } }); dialog.showModal(); });
  $('#clubs-query').addEventListener('input', () => { limit = 50; render(); }); for (const id of ['#clubs-visibility', '#clubs-kind']) $(id).addEventListener('change', () => { limit = 50; render(); });
  $('#clubs-refresh').addEventListener('click', load); $('#clubs-more').addEventListener('click', () => { limit += 50; render(); });
  $('#club-editor-close').addEventListener('click', () => { if (!busy) dialog.close(); }); dialog.addEventListener('cancel', e => { if (busy) e.preventDefault(); });
  async function history(append = false) {
    const e = epoch; if (!append) { historyPage = 1; $('#clubs-history-list').replaceChildren(); }
    $('#clubs-history-more').disabled = true;
    try { const data = await api('/api/admin/schools/history?page=' + historyPage); if (e !== epoch) return; for (const x of data.items) $('#clubs-history-list').append(node('p', '', `${date(x.stamp)} · ${x.kind === 'write' ? '提交资料' : '查看资料'} · ${x.school || '历史资料'} · ${x.network}`)); $('#clubs-history-more').hidden = historyPage * 30 >= data.total; }
    catch (error) { if (e === epoch) { if (append) historyPage--; message('#clubs-error', error.message); } }
    finally { if (e === epoch) $('#clubs-history-more').disabled = false; }
  }
  $('#clubs-history').addEventListener('toggle', () => { if ($('#clubs-history').open) history(); }); $('#clubs-history-more').addEventListener('click', () => { historyPage++; history(true); });
  window.LeanSchoolAdmin = { init: client => { api = client; }, load, reset };
})();
