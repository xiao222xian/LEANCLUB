(() => {
  'use strict';
  const { request, node, statuses } = window.LeanDirectory, $ = s => document.querySelector(s);
  const form = $('#entry-form'), f = form.elements, params = new URLSearchParams(location.search);
  let key = new URLSearchParams(location.hash.slice(1)).get('edit') || '', revision = 0, qr = '', busy = false, current = null, schoolId = null, alias = '', timer, suggestionVersion = 0;
  const initialSchool = params.get('schoolName') || '';
  function message(selector, text = '') { $(selector).textContent = text; $(selector).hidden = !text; }
  function type(update = false) {
    const kind = f.kind.value;
    $('#entry-name-label').textContent = { school: '学校名称', group: '团体名称', person: '昵称' }[kind];
    f.name.autocomplete = kind === 'person' ? 'nickname' : 'organization';
    $('#entry-contact-field').hidden = kind === 'school'; f.contact.required = kind !== 'school';
    $('#entry-contact-public').hidden = kind === 'school'; $('#entry-school-field').hidden = kind === 'school'; $('#entry-count-field').hidden = kind !== 'group';
    if (update) { f.profilePublic.checked = kind !== 'person'; f.contactPublic.checked = false; }
    $('#entry-public-label').textContent = kind === 'person' ? '审核后公开我的个人资料' : '审核后公开这份资料';
  }
  function preview() { $('#entry-qr-preview').hidden = !qr; if (qr) $('#entry-qr-preview').src = qr; else $('#entry-qr-preview').removeAttribute('src'); $('#entry-qr-remove').hidden = !qr; $('#entry-qr-label-field').hidden = !qr; }
  function fill(data) {
    current = data; revision = data.revision; const p = data.profile;
    for (const name of ['kind', 'name', 'country', 'city', 'schoolName', 'contact', 'en', 'description', 'count', 'qrLabel']) f[name].value = p[name] ?? '';
    f.contactPublic.checked = p.contactPublic; f.profilePublic.checked = p.profilePublic;
    qr = p.qr || ''; schoolId = p.schoolId; alias = p.alias || ''; preview(); type();
    $('#entry-title').textContent = '编辑资料'; $('#entry-submit').textContent = '保存并提交审核 ↗';
    $('#entry-state').hidden = false; $('#entry-state').replaceChildren(node('strong', '', statuses[data.status] || '已提交'));
    if (data.note) $('#entry-state').append(node('p', '', data.note));
    if (data.status === 'published' && !data.visible) $('#entry-state').append(node('p', '', '资料未公开'));
  }
  async function load() {
    if (!key) return;
    form.hidden = true; message('#entry-load-error'); $('#entry-reload').hidden = true;
    try { fill(await request('/api/directory/manage', { editKey: key })); form.hidden = false; }
    catch (error) {
      // A key is placed in the fragment before the first request, making retries idempotent.
      if (error.status === 404 && new URLSearchParams(location.hash.slice(1)).get('new') === '1') form.hidden = false;
      else { message('#entry-load-error', error.message); $('#entry-reload').hidden = false; }
    }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (busy || !form.reportValidity()) return;
    if (!key) { const bytes = crypto.getRandomValues(new Uint8Array(32)); key = btoa(String.fromCharCode(...bytes)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', ''); history.replaceState(null, '', location.pathname + '#edit=' + encodeURIComponent(key) + '&new=1'); }
    const profile = { kind: f.kind.value, contactPublic: f.contactPublic.checked, profilePublic: f.profilePublic.checked, count: Number(f.count.value || 0), qr, schoolId, alias };
    for (const name of ['name', 'country', 'city', 'schoolName', 'contact', 'en', 'description', 'qrLabel']) profile[name] = f[name].value.trim();
    busy = true; $('#entry-submit').disabled = true; message('#entry-error');
    try {
      const data = await request('/api/directory/submit', { editKey: key, revision, profile }); fill(data);
      const link = location.origin + '/join#edit=' + encodeURIComponent(key); history.replaceState(null, '', link);
      $('#entry-manage-link').value = link; $('#entry-success-status').textContent = statuses[data.status];
      $('#entry-editor').hidden = true; $('#entry-success').hidden = false; $('#entry-success-title').tabIndex = -1; $('#entry-success-title').focus();
    } catch (error) { message('#entry-error', error.message); if (error.status === 409) $('#entry-reload').hidden = false; }
    finally { busy = false; $('#entry-submit').disabled = false; }
  });
  form.querySelectorAll('[name=kind]').forEach(el => el.addEventListener('change', () => type(true)));
  $('#entry-qr').addEventListener('change', async event => {
    const file = event.target.files[0]; if (!file) return;
    if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 2 * 1024 * 1024) { event.target.value = ''; message('#entry-error', '二维码请选择2MB以内的PNG或JPG图片。'); return; }
    busy = true; $('#entry-submit').disabled = true;
    try { qr = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file); }); preview(); message('#entry-error'); }
    catch { message('#entry-error', '图片读取失败，请重新选择。'); }
    finally { busy = false; $('#entry-submit').disabled = false; }
  });
  $('#entry-qr-remove').addEventListener('click', () => { qr = ''; $('#entry-qr').value = ''; preview(); });
  $('#entry-copy').addEventListener('click', async () => { try { await navigator.clipboard.writeText($('#entry-manage-link').value); $('#entry-copy-status').textContent = '已复制，请妥善保存。'; } catch { $('#entry-manage-link').select(); $('#entry-copy-status').textContent = '请复制已选中的管理链接。'; } });
  $('#entry-edit-again').addEventListener('click', () => { $('#entry-success').hidden = true; $('#entry-editor').hidden = false; f.name.focus(); });
  $('#entry-reload').addEventListener('click', load);
  f.schoolName.addEventListener('input', () => {
    schoolId = null; clearTimeout(timer); const version = ++suggestionVersion;
    if (f.schoolName.value.trim().length < 2) return;
    timer = setTimeout(async () => { try { const data = await window.LeanClubPortal.api('/leanclub/school/list?' + new URLSearchParams({ keyword: f.schoolName.value.trim(), pageNo: 1, pageSize: 8 })); if (version !== suggestionVersion) return; $('#entry-school-options').replaceChildren(...data.list.map(s => { const o = node('option'); o.value = s.name; o.dataset.id = s.id; return o; })); } catch { /* Suggestions never prevent a free-text submission. */ } }, 300);
  });
  f.schoolName.addEventListener('change', () => { const match = [...$('#entry-school-options').options].find(o => o.value === f.schoolName.value); schoolId = match ? Number(match.dataset.id) : null; });
  f.kind.value = ['school', 'group', 'person'].includes(params.get('kind')) ? params.get('kind') : 'group';
  f.name.value = params.get('name') || ''; f.schoolName.value = initialSchool; f.country.value = params.get('country') || '中国'; f.city.value = params.get('city') || '';
  if (/^[1-9][0-9]*$/.test(params.get('schoolId') || '')) schoolId = Number(params.get('schoolId'));
  type(true); if (initialSchool) $('.entry-more').open = true; if (key) load();
})();
