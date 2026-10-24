(() => {
  'use strict';
  const $ = s => document.querySelector(s), nf = new Intl.NumberFormat('zh-CN');
  const node = (tag, cls, text) => { const el = document.createElement(tag); el.className = cls || ''; if (text !== undefined) el.textContent = text; return el; };
  let api, items = [], current = null, epoch = 0, request = 0, busy = false, historyPage = 1;
  const dialog = $('#club-editor'), form = $('#club-editor-form');
  const msg = (id, text = '') => { $(id).textContent = text; $(id).hidden = !text; };
  const date = stamp => new Date(stamp * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
  function reset() { epoch++; request++; items = []; current = null; busy = false; $('#clubs-list').replaceChildren(); $('#clubs-summary').replaceChildren(); $('#clubs-history-list').replaceChildren(); $('#clubs-history').open = false; form.reset(); $('#club-edit-qr').removeAttribute('src'); $('#club-audit').replaceChildren(); msg('#clubs-error'); msg('#club-editor-error'); msg('#club-save-status'); if (dialog.open) dialog.close(); }
  function render() {
    const q = $('#clubs-query').value.trim().toLocaleLowerCase(), status = $('#clubs-visibility').value;
    const selected = items.filter(x => x.name.toLocaleLowerCase().includes(q) && (status === 'all' || String(x.visible) === status));
    $('#clubs-list').replaceChildren(...selected.map(x => {
      const row = node('button', 'club-admin-row'); row.type = 'button';
      const title = node('span'); title.append(node('strong', '', x.name), node('small', '', [x.region || '未设置地区', x.hasContact ? '联系资料' : '无联系资料', x.hasQr ? '二维码' : '无二维码'].join(' · ')));
      row.append(title, node('span', 'club-admin-count', nf.format(x.count)), node('span', x.visible ? 'club-is-visible' : 'club-is-hidden', x.visible ? '展示中' : '已隐藏'), node('span', '', '编辑 ↗'));
      row.addEventListener('click', () => open(x.id)); return row;
    }));
    if (!selected.length) $('#clubs-list').append(node('p', 'empty-copy', '暂无匹配的学校资料。'));
  }
  async function load() {
    const e = epoch, r = ++request; msg('#clubs-error'); $('#clubs-refresh').disabled = true;
    try { const data = await api('/api/admin/schools'); if (e !== epoch || r !== request) return; items = data.items; render();
      const imports = data.imports || [], summary = $('#clubs-summary'); summary.replaceChildren();
      for (const [label, value] of [['学校社群', items.length], ['社群规模合计', items.reduce((n, x) => n + x.count, 0)], ['展示中', items.filter(x => x.visible).length], ['历史操作', imports.reduce((n, x) => n + x.events, 0)]]) { const d = node('div'); d.append(node('small', '', label), node('strong', '', nf.format(value))); summary.append(d); }
    } catch (error) { if (e === epoch && r === request) msg('#clubs-error', error.message); } finally { if (e === epoch && r === request) $('#clubs-refresh').disabled = false; }
  }
  function fill(data) {
    current = data.club;
    for (const key of ['name', 'en', 'region', 'alias', 'count', 'contact', 'schoolId']) form.elements[key].value = current[key] ?? '';
    form.elements.visible.checked = !!current.visible; form.elements.removeQr.checked = false;
    $('#club-qr-file').value = '';
    const image = $('#club-edit-qr'); image.hidden = !current.qr; image.removeAttribute('src');
    if (/^data:image\/(png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(current.qr || '')) image.src = current.qr;
    else image.hidden = true;
    $('#club-editor-source').textContent = `${current.source.startsWith('leanmuscleclub.com:') ? '原 com 站资料' : '社群资料'} · 版本 ${current.revision} · ${date(current.updated)}`;
    $('#club-audit').replaceChildren(...data.audit.map(a => node('p', '', `${date(a.created)} · ${a.actor} · ${a.action === 'qr' ? '更新二维码' : '更新资料'} · 版本 ${a.revision}`)));
    if (!data.audit.length) $('#club-audit').append(node('p', '', '迁入后暂无修改。'));
  }
  async function open(id) {
    const e = epoch, r = ++request; current = null; msg('#clubs-error');
    try { const data = await api('/api/admin/schools/' + encodeURIComponent(id)); if (e !== epoch || r !== request) return; fill(data); msg('#club-editor-error'); msg('#club-save-status'); $('#club-save').disabled = false; if (!dialog.open) dialog.showModal(); }
    catch (error) { if (e === epoch && r === request) msg('#clubs-error', error.message); }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (busy || !current || !form.reportValidity()) return;
    const file = $('#club-qr-file').files[0];
    if (file && (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 2 * 1024 * 1024)) { msg('#club-editor-error', '请选择2MB以内的PNG或JPG图片。'); return; }
    const e = epoch, id = current.id, body = { revision: current.revision };
    for (const key of ['name', 'en', 'region', 'alias', 'contact']) body[key] = form.elements[key].value.trim();
    body.count = Number(form.elements.count.value); body.visible = form.elements.visible.checked; body.removeQr = form.elements.removeQr.checked;
    body.schoolId = form.elements.schoolId.value ? Number(form.elements.schoolId.value) : null;
    busy = true; $('#club-save').disabled = true; msg('#club-editor-error'); msg('#club-save-status', '正在保存…');
    let saved = false;
    try {
      let data = await api('/api/admin/schools/' + encodeURIComponent(id), { method: 'POST', body });
      if (e !== epoch) return; saved = true; current = data.club;
      if (file) {
        try { data = await api('/api/admin/schools/' + encodeURIComponent(id) + '/qr', { method: 'POST', file, headers: { 'X-Club-Revision': String(current.revision) } }); }
        catch (error) { fill(data); throw new Error('文字资料已保存，二维码未更新：' + error.message); }
      }
      if (e !== epoch) return;
      fill(data); msg('#club-save-status', '已保存，榜单已同步。'); await load();
    } catch (error) { if (e === epoch) { msg('#club-save-status'); msg('#club-editor-error', error.message); if (saved) await load(); } }
    finally { if (e === epoch) { busy = false; $('#club-save').disabled = false; } }
  });
  async function history(append = false) {
    const e = epoch;
    if (!append) { historyPage = 1; $('#clubs-history-list').replaceChildren(); }
    $('#clubs-history-more').disabled = true;
    try { const data = await api('/api/admin/schools/history?page=' + historyPage); if (e !== epoch) return;
      data.items.forEach(x => $('#clubs-history-list').append(node('p', '', `${date(x.stamp)} · ${{ read: '查看资料', write: '提交资料' }[x.kind] || '历史操作'} · ${x.school || '原学校记录'} · ${x.network}`)));
      $('#clubs-history-more').hidden = historyPage * 30 >= data.total;
      if (!data.total) $('#clubs-history-list').append(node('p', '', '暂无历史操作记录。'));
    } catch (error) { if (e === epoch) { msg('#clubs-error', error.message); if (append) historyPage--; } }
    finally { if (e === epoch) $('#clubs-history-more').disabled = false; }
  }
  $('#clubs-history').addEventListener('toggle', () => { if ($('#clubs-history').open) history(); });
  $('#clubs-history-more').addEventListener('click', () => { historyPage++; history(true); });
  $('#clubs-query').addEventListener('input', render); $('#clubs-visibility').addEventListener('change', render); $('#clubs-refresh').addEventListener('click', load);
  $('#club-editor-close').addEventListener('click', () => { if (!busy) dialog.close(); }); dialog.addEventListener('cancel', e => { if (busy) e.preventDefault(); });
  window.LeanSchoolAdmin = { init: client => { api = client; }, load, reset };
})();
