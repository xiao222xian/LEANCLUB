(() => {
  'use strict';
  const $ = s => document.querySelector(s), panel = $('#panel-comments');
  if (!panel) return;
  const list = $('#admin-comments-list'), drafts = new Map();
  const cities = { shenzhen: '深圳', zhuhai: '珠海', guangzhou: '广州' };
  let api, items = [], page = 1, pages = 1, filter = 'all', total = 0;
  let epoch = 0, busy = false, loaded = false, editing = null;
  const node = (tag, cls, value = '') => { const n = document.createElement(tag); n.className = cls; n.textContent = value; return n; };
  const date = value => new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
  const message = (value = '', error = false) => {
    const el = $('#admin-comments-message'); el.textContent = value; el.hidden = !value;
    el.classList.toggle('is-error', error);
  };
  function button(label, action, cls = '') {
    const b = node('button', cls, label); b.type = 'button'; b.addEventListener('click', action); return b;
  }
  function controls() {
    panel.setAttribute('aria-busy', String(busy));
    panel.querySelectorAll('button,textarea,select').forEach(el => { el.disabled = busy; });
    $('#admin-comments-prev').disabled = busy || page <= 1;
    $('#admin-comments-next').disabled = busy || page >= pages;
  }
  function reset() {
    epoch++; busy = loaded = false; items = []; drafts.clear(); editing = null; page = pages = 1; total = 0;
    filter = 'all'; $('#admin-comments-visibility').value = 'all'; list.replaceChildren(); message(); controls();
    panel.querySelectorAll('[data-comment-filter]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.commentFilter === filter)));
  }
  function edit(item) {
    if (busy) return;
    if (!drafts.has(item.id)) drafts.set(item.id, { content: item.reply?.content || '', base: item.reply?.content || '', revision: item.replyRevision });
    editing = item.id; render(); $('#reply-' + item.id)?.focus();
  }
  function editor(item) {
    const draft = drafts.get(item.id), form = node('form', 'admin-reply-editor');
    const top = node('div', 'admin-reply-editor-head'), label = node('label', '', '回复 · 薄肌学院');
    label.htmlFor = 'reply-' + item.id;
    const count = node('span', 'admin-reply-count', `${[...draft.content].length} / 500`);
    top.append(label, count);
    const input = node('textarea', ''); input.id = label.htmlFor; input.rows = 3;
    input.value = draft.content; input.required = true; input.placeholder = '写下回复…';
    input.addEventListener('input', () => {
      draft.content = input.value; const length = [...input.value].length;
      count.textContent = `${length} / 500`;
      input.setCustomValidity(length > 500 ? '回复请控制在500字以内。' : '');
    });
    const footer = node('div', 'admin-reply-editor-actions');
    const cancel = button('取消', () => { drafts.delete(item.id); editing = null; render(); focusAction(item.id); }, 'admin-comment-text');
    const save = node('button', 'admin-comment-primary', item.reply ? '保存修改 ↗' : '发布回复 ↗'); save.type = 'submit';
    footer.append(cancel, save); form.append(top, input, footer);
    // Preserve a stale draft, but never silently overwrite another admin's reply.
    if (draft.revision !== item.replyRevision) {
      const conflict = node('div', 'admin-reply-conflict', '回复已有更新。');
      conflict.append(button('载入最新回复', () => { drafts.delete(item.id); edit(item); }, 'admin-comment-text'));
      form.append(conflict);
    }
    form.addEventListener('submit', event => {
      event.preventDefault(); if (busy || !form.reportValidity()) return;
      mutate(item, 'reply', { content: input.value, revision: draft.revision });
    });
    return form;
  }
  function focusAction(id) { list.querySelector(`[data-reply-for="${id}"]`)?.focus(); }
  function render() {
    list.replaceChildren();
    for (const item of items) {
      const row = node('article', 'admin-comment-row'); row.dataset.status = item.status;
      const head = node('div', 'admin-comment-meta');
      head.append(node('strong', '', item.nickname || '薄肌同好'));
      if (item.city) head.append(node('span', '', cities[item.city]));
      if (item.parentId) head.append(node('span', 'admin-comment-thread-label', '回复 ' + (item.replyToName || '留言')));
      if (item.status === 'hidden') head.append(node('span', 'admin-comment-hidden', '已隐藏'));
      const time = node('time', '', date(item.createdAt)); time.dateTime = item.createdAt; head.append(time);
      row.append(head);
      if (item.parentId && item.parentContent) row.append(node('blockquote', 'admin-comment-context', item.parentContent));
      row.append(node('p', 'admin-comment-content', item.content));
      if (item.reply) {
        const reply = node('div', 'admin-comment-reply'), replyHead = node('div', 'admin-reply-meta');
        replyHead.append(node('strong', '', '薄肌学院'));
        const updated = node('time', '', date(item.reply.updatedAt)); updated.dateTime = item.reply.updatedAt; replyHead.append(updated);
        reply.append(replyHead, node('p', '', item.reply.content)); row.append(reply);
      }
      const actions = node('div', 'admin-comment-actions');
      actions.append(button(item.status === 'hidden' ? '恢复显示' : '隐藏留言', () => mutate(item, 'status', { status: item.status === 'hidden' ? 'visible' : 'hidden' }), 'admin-comment-text'));
      const replyActions = node('div', 'admin-comment-reply-actions');
      if (item.reply && editing !== item.id) replyActions.append(button('撤回', () => {
        replyActions.replaceChildren(
          button('确认撤回', () => mutate(item, 'reply', { content: null, revision: item.replyRevision }), 'admin-comment-text is-danger'),
          button('取消', () => { render(); focusAction(item.id); }, 'admin-comment-text')
        ); replyActions.querySelector('button').focus();
      }, 'admin-comment-text'));
      if (editing !== item.id) {
        const action = button(item.reply ? '修改回复' : drafts.has(item.id) ? '继续回复 ↗' : '回复 ↗', () => edit(item), item.reply ? 'admin-comment-text' : 'admin-comment-primary');
        action.dataset.replyFor = item.id; replyActions.append(action);
      }
      actions.append(replyActions); row.append(actions);
      if (editing === item.id && drafts.has(item.id)) row.append(editor(item));
      list.append(row);
    }
    if (!items.length) list.append(node('p', 'admin-comments-empty', loaded ? '暂无符合条件的留言' : '正在加载…'));
    $('#admin-comments-total').textContent = loaded ? `${total} 条` : '';
    $('#admin-comments-page').textContent = `${page} / ${pages}`;
    $('#admin-comments-pagination').hidden = pages <= 1;
    controls();
  }
  async function load() {
    if (!api || busy) return;
    busy = true; const version = epoch; controls();
    try {
      const query = new URLSearchParams({ status: $('#admin-comments-visibility').value, reply: filter, page });
      let result = await api('/api/admin/comments?' + query);
      if (version !== epoch) return;
      if (page > result.totalPages) {
        page = result.totalPages; query.set('page', page); result = await api('/api/admin/comments?' + query);
        if (version !== epoch) return;
      }
      items = result.items; total = result.total; pages = result.totalPages; loaded = true; render();
    } catch (error) { if (version === epoch) message(error.message, true); }
    finally { if (version === epoch) { busy = false; controls(); } }
  }
  async function mutate(item, action, body) {
    if (busy) return;
    busy = true; controls(); const version = epoch; message();
    let saved = false;
    try {
      const result = await api(`/api/admin/comments/${item.id}/${action}`, { method: 'POST', body });
      if (version !== epoch) return;
      items = items.map(current => current.id === item.id ? result.item : current);
      if (action === 'reply') { drafts.delete(item.id); editing = null; }
      saved = true; render();
      message(action === 'status' ? (body.status === 'hidden' ? '留言已隐藏' : '留言已恢复') : body.content === null ? '回复已撤回' : result.item.status === 'hidden' ? '回复已保存；留言恢复显示后公开' : '回复已发布');
    } catch (error) { if (version === epoch) message(error.message, true); }
    finally { if (version === epoch) { busy = false; controls(); } }
    if (saved && version === epoch) { await load(); focusAction(item.id); }
  }
  panel.querySelectorAll('[data-comment-filter]').forEach(b => b.addEventListener('click', () => {
    if (busy) return; filter = b.dataset.commentFilter; page = 1; message();
    panel.querySelectorAll('[data-comment-filter]').forEach(n => n.setAttribute('aria-pressed', String(n === b))); load();
  }));
  $('#admin-comments-visibility').addEventListener('change', () => { page = 1; message(); load(); });
  $('#admin-comments-refresh').addEventListener('click', () => { message(); load(); });
  $('#admin-comments-prev').addEventListener('click', () => { page--; load(); });
  $('#admin-comments-next').addEventListener('click', () => { page++; load(); });
  window.LeanCommentAdmin = { init: client => { api = client; }, load, reset,
    dirty: () => busy || [...drafts.values()].some(draft => draft.content !== draft.base) };
})();
