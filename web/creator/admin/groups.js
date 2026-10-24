(() => {
  'use strict';
  const $ = s => document.querySelector(s), form = $('#group-editor-form'), panel = $('#panel-groups');
  let api, state = { items: [], revision: 0, activeId: null }, current = null, imageData = null, epoch = 0, busy = false, loadVersion = 0;
  const node = (tag, cls = '', text = '') => { const n = document.createElement(tag); n.className = cls; n.textContent = text; return n; };
  const date = n => n ? new Date(n * 1000).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '未设失效时间';
  const message = (text = '') => { $('#groups-message').textContent = text; $('#groups-message').hidden = !text; };
  function lock(value) { busy = value; panel.querySelectorAll('button,input').forEach(n => { n.disabled = value; }); }
  function reset() { epoch++; loadVersion++; state = { items: [], revision: 0, activeId: null }; imageData = null; current = null; lock(false); form.reset(); $('#group-editor-image').removeAttribute('src'); $('#groups-list').replaceChildren(); $('#group-editor').hidden = true; window.LeanGroupInvite?.close(); message(); }
  function button(text, action, cls = '') { const b = node('button', cls, text); b.type = 'button'; b.addEventListener('click', action); return b; }
  function render() {
    const list = $('#groups-list'); list.replaceChildren();
    if (!state.items.length) list.append(node('p', 'groups-empty', '还没有活动群二维码'));
    for (const item of state.items) {
      const active = item.id === state.activeId;
      const row = node('article', 'groups-row'), img = node('img', 'groups-thumbnail'); img.src = item.imageUrl; img.alt = item.label + '二维码';
      const copy = node('div'), heading = node('h3', '', item.label);
      heading.append(node('span', 'groups-state' + (item.expired ? ' is-expired' : active ? ' is-active' : ''), item.expired ? '已到期' : active ? '展示中' : '未展示'));
      copy.append(heading, node('p', '', item.expiresAt ? '到期 ' + date(item.expiresAt) : date(null)), node('p', '', `展示 ${item.views} · 保存点击 ${item.saves}`));
      const actions = node('div', 'groups-actions');
      actions.append(button('预览', async () => { if (!await window.LeanGroupInvite.open(item, { preview: true })) message('图片加载失败，请刷新重试。'); }), button('编辑', () => edit(item)));
      actions.append(button(active ? '停用' : '设为展示', () => mutate(active ? 'disable' : 'activate', item.id), active ? '' : 'group-activate'));
      if (!active) actions.append(button('删除', () => {
        actions.replaceChildren(button('确认删除', () => mutate('delete', item.id), 'group-delete-confirm'), button('取消', render));
      }));
      row.append(img, copy, actions); list.append(row);
    }
  }
  async function load() {
    if (busy || !api) return;
    const e = epoch, v = ++loadVersion; $('#groups-refresh').disabled = true;
    try { const result = await api('/api/admin/group-qrs'); if (e !== epoch || v !== loadVersion) return; state = result; render(); }
    catch (error) { if (e === epoch) message(error.message); }
    finally { if (e === epoch) $('#groups-refresh').disabled = false; }
  }
  async function mutate(action, id, extra = {}) {
    if (busy) return;
    const e = epoch; lock(true); message();
    try {
      const result = await api('/api/admin/group-qrs', { method: 'POST', body: { action, id, revision: state.revision, ...extra } });
      if (e !== epoch) return;
      state = result; $('#group-editor').hidden = true; form.reset(); imageData = null; current = null; $('#group-editor-image').removeAttribute('src'); render();
      message(({ save: '已保存', activate: '已设为展示', disable: '已停用', delete: '已删除' })[action]);
    } catch (error) { if (e === epoch) { message(error.message); if (error.status === 409) { lock(false); await load(); } } }
    finally { if (e === epoch) lock(false); }
  }
  function localDate(stamp) { const d = new Date(stamp * 1000); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
  function edit(item = null) {
    if (busy) return;
    current = item; imageData = null; form.reset(); message(); $('#group-editor').hidden = false;
    $('#group-editor-title').textContent = item ? '编辑二维码' : '添加二维码';
    $('#group-label').value = item?.label || ''; $('#group-expires').value = item?.expiresAt ? localDate(item.expiresAt) : '';
    const image = $('#group-editor-image'); image.removeAttribute('src'); image.hidden = !item; if (item) image.src = item.imageUrl;
    $('#group-upload-label').textContent = item ? '点击替换二维码' : '上传二维码';
    $('#group-upload-icon').hidden = !!item; $('#group-label').focus({ preventScroll: true });
    if (matchMedia('(max-width:1000px)').matches) $('#group-editor').scrollIntoView({ block: 'start', behavior: 'smooth' });
  }
  $('#group-file').addEventListener('change', async e => {
    const file = e.target.files[0]; if (!file) return;
    if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 2 * 1024**2) { message('请选择 2MB 以内的 PNG 或 JPG。'); e.target.value = ''; return; }
    const version = epoch, selected = current; lock(true);
    try {
      const value = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = reject; r.readAsDataURL(file); });
      const test = new Image(); test.src = value; await test.decode();
      if (version !== epoch || selected !== current) return;
      imageData = value; $('#group-editor-image').src = value; $('#group-editor-image').hidden = false; $('#group-upload-icon').hidden = true; $('#group-upload-label').textContent = '点击替换二维码'; message();
    } catch { if (version === epoch) message('无法读取这张图片，请重新选择。'); }
    finally { if (version === epoch) lock(false); }
  });
  form.addEventListener('submit', e => {
    e.preventDefault(); if (busy || !form.reportValidity()) return;
    if (!current && !imageData) { message('请先上传二维码。'); return; }
    const expiresAt = $('#group-expires').value ? Math.floor(new Date($('#group-expires').value).getTime() / 1000) : null;
    if (expiresAt !== null && !Number.isFinite(expiresAt)) { message('请选择有效的失效时间。'); return; }
    mutate('save', current?.id || null, { label: $('#group-label').value.trim(), expiresAt, ...(imageData ? { image: imageData } : {}) });
  });
  $('#groups-add').addEventListener('click', () => edit());
  $('#group-editor-cancel').addEventListener('click', () => { $('#group-editor').hidden = true; imageData = null; form.reset(); });
  $('#groups-refresh').addEventListener('click', load);
  window.LeanGroupAdmin = { init: client => { api = client; }, load, reset };
})();
