(() => {
  'use strict';
  const node = (tag, className = '', text = '') => { const el = document.createElement(tag); el.className = className; el.textContent = text; return el; };
  async function request(path, body) {
    let response;
    try { response = await fetch(path, { method: body === undefined ? 'GET' : 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store', credentials: 'same-origin', signal: AbortSignal.timeout(20000) }); }
    catch { throw new Error('暂时无法连接，请重试。填写的内容已保留。'); }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(data.error || '读取失败，请重试。'); error.status = response.status; throw error; }
    return data;
  }
  async function api(path) { const r = await request('/app-api' + path); if (r.code !== 0) throw new Error(r.msg || '学校目录暂时无法读取。'); return r.data; }
  window.LeanClubPortal = { api, node };
  window.LeanDirectory = { request, node, types: { school: '学校', group: '团体', person: '个人' }, statuses: { pending: '待审核', published: '已通过', changes: '待补充', hidden: '已隐藏' } };
})();
