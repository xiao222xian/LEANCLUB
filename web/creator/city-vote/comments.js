(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const form = byId('comment-form');
  if (!form) return;
  const content = byId('comment-content'), nickname = byId('comment-nickname');
  const consent = byId('comment-consent'), submit = byId('comment-submit');
  const formMessage = byId('comment-form-message'), feedStatus = byId('comment-feed-status');
  const list = byId('comment-list'), more = byId('comment-more'), refresh = byId('comment-refresh');
  const band = byId('danmaku-band'), toggle = byId('danmaku-toggle');
  const cityNames = { shenzhen: '深圳', zhuhai: '珠海', guangzhou: '广州' };
  // Decorative prompts only: no invented authors, public posts or comment counts.
  const topics = [
    '深圳蹲一个', '广州这边集合', '珠海也别落下啊', '广深都能去',
    '地铁能到就好', '离学校近点就好了', '蹲蹲', '来了来了',
    '这个可以有', '有点想去', '+1', '哈哈哈哈',
    '别早八就行', '周六下午可以', '下班局有没有', '等个具体时间',
    '傍晚吧 没那么晒', '周日也行', '新手能跟上吗', '求个新手局',
    '慢跑可以 快跑不行', '别一上来五公里啊', '散步组有吗哈哈', '体力一般 但想玩',
    '社恐先探个头', '有没有同校的', '想找个练搭子', '一个人来也行吧',
    '带朋友一起行不', '不会只有我零基础吧', '想练肩背+1', '不追求很大块',
    '有点线条就满足了', '练腿先放一放哈哈', '有人教动作就好了', '拉伸算我一份',
    '羽毛球可以安排', '飞盘也想试试', '户外局爱了', '下雨的话咋办',
    '别安排太满', '晚上应该凉快点', '练完吃啥', '饭搭子顺便找了',
    '水先带够', '防晒不能忘', '蹲个报名', '时间定了再约'
  ];
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const PAGE_SIZE = 20, MAX_VISIBLE = 100;
  let items = [], nextCursor = null, total = null, feedLoaded = false;
  let request = null, sending = false, loadPromise = null;
  const threads = new Map(), cards = new Map(), replyDrafts = new Map();
  let editor = null, rememberedNickname = '';
  const pageActive = () => document.visibilityState === 'visible' && document.hasFocus();
  let pollTimer = null, driftTimer = null, heroVisible = true, conversationVisible = false;
  let laneReady = [0, 0], topicIndex = -1, topicQueue = [], commentIndex = 0, emitted = 0;
  let motionEnabled = true;
  try { motionEnabled = sessionStorage.getItem('leanclub-comment-motion') !== 'off'; } catch { /* Preference is optional. */ }

  const text = (tag, value, className = '') => {
    const node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    return node;
  };
  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...options, signal: controller.signal });
      let data;
      try { data = await response.json(); } catch { throw new Error('暂时连接不上留言板，请稍后重试。'); }
      if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : '操作未完成，请稍后重试。');
      return data;
    } catch (error) {
      if (error.name === 'AbortError' || error instanceof TypeError) throw new Error('网络连接不稳定，内容已保留，请重试。');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  async function ensureIdentity() {
    if (!window.LeanCampaignSession) throw new Error('页面还未准备好，请刷新后重试。');
    await window.LeanCampaignSession.ensureIdentity();
  }
  function validComment(item) {
    return item && typeof item.id === 'string' && item.id.length <= 100 && typeof item.content === 'string' &&
      [...item.content].length >= 2 && [...item.content].length <= 240 &&
      (item.nickname == null || typeof item.nickname === 'string' && [...item.nickname].length <= 20) &&
      (item.city == null || Object.hasOwn(cityNames, item.city)) && typeof item.createdAt === 'string' &&
      Number.isFinite(new Date(item.createdAt).getTime()) &&
      (item.reply == null || typeof item.reply.content === 'string' && [...item.reply.content].length <= 500 &&
        typeof item.reply.updatedAt === 'string' && Number.isFinite(new Date(item.reply.updatedAt).getTime())) &&
      (item.parentId == null || typeof item.parentId === 'string') &&
      (item.rootId == null || typeof item.rootId === 'string') &&
      (item.replyToName == null || typeof item.replyToName === 'string') &&
      (item.replies == null || Array.isArray(item.replies) && item.replies.every(validComment));
  }
  function parsePage(data) {
    if (!data || !Array.isArray(data.items) || !data.items.every(validComment) ||
      !(data.nextCursor == null || typeof data.nextCursor === 'string' && data.nextCursor.length <= 200)) {
      throw new Error('留言数据暂时无法读取，请重新刷新。');
    }
    return data;
  }
  function setFeedStatus(message, error = false) {
    feedStatus.textContent = message;
    feedStatus.hidden = !message;
    feedStatus.classList.toggle('is-error', error);
  }
  function setFormMessage(message, error = false) {
    formMessage.textContent = message;
    formMessage.hidden = !message;
    formMessage.classList.toggle('is-error', error);
  }
  function formatDate(value) {
    return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value));
  }
  function button(label, className, callback) {
    const node = text('button', label, className);
    node.type = 'button';
    node.addEventListener('click', callback);
    return node;
  }
  // Keep each post and editor mounted while polling. Moving/replacing a focused
  // textarea loses the mobile keyboard, selection and IME composition state.
  function makePost(item, rootId, nested = false) {
    const node = text('li', '', nested ? 'comment-thread-item' : 'comment-card');
    node.id = `comment-${item.id}`;
    const head = text('div', '', 'comment-card-head');
    const author = text('span', '', 'comment-author'), city = text('span', '', 'comment-city');
    const time = text('time', ''); head.append(author, city, time);
    const target = text('p', '', 'comment-reply-target');
    const body = text('p', '', 'comment-body');
    const actions = text('div', '', 'comment-post-actions');
    const slot = text('div', '', 'comment-editor-slot');
    const official = text('div', '', 'comment-reply');
    const officialHead = text('div', '', 'comment-reply-head');
    const officialTime = text('time', '');
    officialHead.append(text('strong', '薄肌学院', 'comment-reply-author'), officialTime);
    const officialBody = text('p', '', 'comment-reply-body');
    const officialActions = text('div', '', 'comment-post-actions');
    const officialSlot = text('div', '', 'comment-editor-slot');
    const view = { node, head, author, city, time, target, body, slot, official, officialTime, officialBody, officialSlot, item, rootId };
    actions.append(button('回复', 'comment-reply-action', () => openReply(view, false)));
    officialActions.append(button('回复', 'comment-reply-action', () => openReply(view, true)));
    official.append(officialHead, officialBody, officialActions, officialSlot);
    node.append(head, target, body, actions, slot, official);
    updatePost(view, item);
    return view;
  }
  function updatePost(view, item) {
    view.item = item;
    const put = (node, value) => { if (node.textContent !== value) node.textContent = value; };
    put(view.author, item.nickname || '薄肌同好');
    view.city.hidden = !item.city; put(view.city, cityNames[item.city] || '');
    put(view.time, formatDate(item.createdAt)); view.time.dateTime = item.createdAt;
    view.target.hidden = !item.parentId;
    put(view.target, item.parentId ? `回复 ${item.replyToName || '薄肌同好'}` : '');
    put(view.body, item.content);
    view.official.hidden = !item.reply;
    if (item.reply) {
      put(view.officialBody, item.reply.content);
      put(view.officialTime, formatDate(item.reply.updatedAt)); view.officialTime.dateTime = item.reply.updatedAt;
    } else if (editor?.view === view && editor.official) closeEditor();
  }
  function syncChildren(container, nodes) {
    const live = new Set(nodes);
    for (const child of [...container.children]) if (!live.has(child)) child.remove();
    // Normally only new items are inserted. Existing focused nodes stay put.
    nodes.forEach((node, index) => { if (container.children[index] !== node) container.insertBefore(node, container.children[index] || null); });
  }
  function threadFor(root) {
    if (!threads.has(root.id)) threads.set(root.id, {
      items: root.replies || [], total: root.replyCount || 0, nextCursor: root.repliesNextCursor || null,
      expanded: false, loading: false, message: '', views: new Map(), generation: 0, recent: new Map()
    });
    return threads.get(root.id);
  }
  function reconcileThread(root) {
    const state = threadFor(root);
    state.total = Number.isInteger(root.replyCount) ? root.replyCount : 0;
    if (!state.expanded) {
      state.items = root.replies || [];
      state.nextCursor = root.repliesNextCursor || null;
    } else {
      const merged = new Map(state.items.map(item => [item.id, item]));
      for (const item of root.replies || []) merged.set(item.id, item);
      state.items = [...merged.values()].sort((a, b) => a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id));
    }
  }
  function renderThread(rootId) {
    const card = cards.get(rootId), state = threads.get(rootId);
    if (!card || !state) return;
    const live = new Set();
    const nodes = state.items.map(item => {
      live.add(item.id);
      let view = state.views.get(item.id);
      if (!view) { view = makePost(item, rootId, true); state.views.set(item.id, view); }
      else updatePost(view, item);
      return view.node;
    });
    for (const [id, view] of state.views) if (!live.has(id)) {
      if (editor?.view === view) closeEditor();
      state.views.delete(id);
    }
    syncChildren(card.thread, nodes);
    card.thread.hidden = nodes.length === 0;
    card.threadMore.hidden = !state.nextCursor;
    const remaining = Math.max(0, state.total - state.items.length);
    card.threadMore.textContent = state.loading ? '加载中…' : remaining ? `展开更多回复 · ${remaining}` : '展开更多回复';
    card.threadMore.disabled = state.loading;
    card.threadStatus.textContent = state.message;
    card.threadStatus.hidden = !state.message;
  }
  function renderList() {
    const live = new Set(items.map(item => item.id));
    for (const [id] of cards) if (!live.has(id)) {
      if (editor?.view.rootId === id) closeEditor();
      cards.delete(id); threads.delete(id);
    }
    const nodes = items.map(item => {
      let card = cards.get(item.id);
      if (!card) {
        card = makePost(item, item.id);
        card.thread = text('ol', '', 'comment-thread-list');
        card.thread.setAttribute('aria-label', '楼内回复');
        card.threadMore = button('', 'comment-thread-more', () => loadThread(item.id, true));
        card.threadStatus = text('p', '', 'comment-thread-status');
        card.threadStatus.setAttribute('role', 'status');
        card.node.append(card.thread, card.threadMore, card.threadStatus);
        cards.set(item.id, card);
      } else updatePost(card, item);
      renderThread(item.id);
      return card.node;
    });
    syncChildren(list, nodes);
    byId('comment-empty').hidden = !feedLoaded || items.length > 0;
    byId('comment-total').textContent = total == null ? '' : String(total);
    byId('comment-total').setAttribute('aria-label', total == null ? '留言数量加载中' : `${total} 条留言`);
    more.hidden = !nextCursor;
    more.textContent = items.length >= MAX_VISIBLE ? '最新留言 ↑' : '更多留言 ↓';
    const liveIds = new Set(items.map(item => item.id));
    for (const node of band.querySelectorAll('[data-comment-id]')) {
      if (!liveIds.has(node.dataset.commentId)) node.remove();
    }
  }
  function schedulePoll() {
    clearTimeout(pollTimer);
    if (!pageActive() || !(heroVisible || conversationVisible)) return;
    pollTimer = setTimeout(() => loadFeed(false, true), 30000);
  }
  function loadFeed(append = false, quiet = false) {
    if (loadPromise) return loadPromise;
    refresh.disabled = more.disabled = true;
    list.setAttribute('aria-busy', 'true');
    if (!quiet) setFeedStatus('加载中…');
    loadPromise = (async () => {
      const wanted = append ? PAGE_SIZE : Math.max(PAGE_SIZE, items.length);
      let cursor = append ? nextCursor : null, fetched = [], latestTotal = null;
      // Refresh every displayed page, so removed comments leave both the wall
      // and danmaku. The cap bounds background work to five small requests.
      do {
        if (quiet && !pageActive()) return;
        const query = new URLSearchParams({ limit: String(PAGE_SIZE) });
        if (cursor) query.set('cursor', cursor);
        const page = parsePage(await api(`/api/campaign/comments?${query}`));
        fetched.push(...page.items);
        if (Number.isInteger(page.total) && page.total >= 0) latestTotal = page.total;
        const previousCursor = cursor;
        cursor = page.nextCursor || null;
        if (cursor && cursor === previousCursor) throw new Error('留言分页暂时无法读取，请刷新后重试。');
      } while (!append && cursor && fetched.length < Math.min(wanted, MAX_VISIBLE));
      const combined = append ? [...items, ...fetched] : fetched;
      items = [...new Map(combined.map(item => [item.id, item])).values()].slice(0, MAX_VISIBLE);
      items.forEach(reconcileThread);
      nextCursor = cursor;
      total = latestTotal;
      feedLoaded = true;
      renderList();
      setFeedStatus('');
      if (!quiet || pageActive() && conversationVisible) {
        for (const [id, thread] of threads) if (thread.expanded) loadThread(id, false, quiet);
      }
    })().catch(error => {
      setFeedStatus(error.message + (feedLoaded ? ' 当前保留上次读取的留言。' : ' 点击「刷新」再试一次。'), true);
      // Do not keep replaying potentially removed comments while refresh fails.
      clearDrift();
      feedLoaded = false;
      syncMotion();
    }).finally(() => {
      loadPromise = null;
      refresh.disabled = more.disabled = false;
      list.setAttribute('aria-busy', 'false');
      schedulePoll();
    });
    return loadPromise;
  }
  async function loadThread(rootId, append = false, quiet = false) {
    const state = threads.get(rootId);
    if (!state || state.loading || quiet && !pageActive()) return;
    state.loading = true; state.expanded = true; state.message = '';
    const generation = ++state.generation;
    renderThread(rootId);
    try {
      let cursor = append ? state.nextCursor : null, fetched = [], latestTotal = state.total;
      const wanted = Math.max(PAGE_SIZE, state.items.length);
      do {
        if (quiet && !pageActive()) return;
        const query = new URLSearchParams({ limit: String(PAGE_SIZE) });
        if (cursor) query.set('cursor', cursor);
        const page = parsePage(await api(`/api/campaign/comments/${encodeURIComponent(rootId)}/replies?${query}`));
        fetched.push(...page.items);
        if (Number.isInteger(page.total)) latestTotal = page.total;
        const previousCursor = cursor; cursor = page.nextCursor || null;
        if (cursor && cursor === previousCursor) throw new Error('回复分页暂时无法读取，请重试。');
      } while (!append && cursor && fetched.length < wanted);
      if (threads.get(rootId) !== state || generation !== state.generation) return;
      const combined = append ? [...state.items, ...fetched] : fetched;
      // A reply just sent can be after the currently loaded page. Keep that
      // confirmed item visible until pagination reaches it; once covered, the
      // server result (including moderation/removal) is authoritative.
      const last = fetched.at(-1);
      for (const [id, item] of state.recent) {
        const covered = !cursor || last && (item.createdAt < last.createdAt || item.createdAt === last.createdAt && item.id <= last.id);
        if (covered) state.recent.delete(id);
        else combined.push(item);
      }
      state.items = [...new Map(combined.map(item => [item.id, item])).values()]
        .sort((a, b) => a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id));
      state.nextCursor = cursor; state.total = latestTotal;
    } catch (error) {
      state.message = error.message;
    } finally {
      state.loading = false;
      renderThread(rootId);
    }
  }
  function draftKey(view, official) { return `${view.item.id}:${official ? 'official' : 'public'}`; }
  function saveDraft(current) {
    replyDrafts.set(current.key, { content: current.content.value, nickname: current.nickname.value,
      consent: current.consent.checked, request: current.request });
  }
  function closeEditor() {
    if (!editor || editor.sending) return;
    saveDraft(editor);
    editor.form.remove(); editor = null;
  }
  function setReplyMessage(current, message, error = false) {
    current.message.textContent = message;
    current.message.hidden = !message;
    current.message.classList.toggle('is-error', error);
  }
  function openReply(view, official) {
    if (editor?.sending) return;
    const key = draftKey(view, official);
    if (editor?.key === key) { editor.content.focus(); return; }
    closeEditor();
    const draft = replyDrafts.get(key);
    const replyForm = text('form', '', 'comment-inline-composer');
    replyForm.noValidate = true;
    const targetName = official ? '薄肌学院' : view.item.nickname || '薄肌同好';
    const header = text('div', '', 'comment-inline-heading');
    const title = text('label', `回复 @${targetName}`);
    title.htmlFor = `reply-content-${view.item.id}`;
    const cancel = button('取消', 'comment-inline-cancel', () => { closeEditor(); view.node.querySelector('.comment-reply-action')?.focus(); });
    header.append(title, cancel);
    const writing = text('div', '', 'comment-field comment-writing');
    const textarea = text('textarea', '');
    textarea.id = title.htmlFor; textarea.rows = 3; textarea.required = true;
    textarea.placeholder = '说点什么…'; textarea.value = draft?.content || '';
    const count = text('span', '', 'comment-count');
    writing.append(textarea, count);
    const alias = text('input', ''); alias.type = 'text'; alias.autocomplete = 'nickname';
    alias.placeholder = '昵称 · 选填'; alias.setAttribute('aria-label', '回复昵称，选填');
    alias.value = draft?.nickname ?? rememberedNickname;
    const aliasField = text('div', '', 'comment-field comment-reply-nickname'); aliasField.append(alias);
    const actions = text('div', '', 'comment-actions');
    const label = text('label', '', 'comment-consent');
    const checkbox = text('input', ''); checkbox.type = 'checkbox'; checkbox.required = true;
    checkbox.checked = draft?.consent === true;
    label.append(checkbox, text('span', '同意公开展示'));
    const send = text('button', '发送 ↗', 'comment-submit'); send.type = 'submit';
    actions.append(label, send);
    const message = text('p', '', 'comment-form-message'); message.hidden = true;
    message.id = `reply-message-${view.item.id}`; message.setAttribute('role', 'status');
    textarea.setAttribute('aria-describedby', message.id);
    replyForm.append(header, writing, aliasField, actions, message);
    const current = { key, view, official, form: replyForm, content: textarea, nickname: alias,
      consent: checkbox, send, cancel, message, request: draft?.request || null, sending: false };
    editor = current;
    const update = () => {
      const size = [...textarea.value].length;
      count.textContent = `${size} / 240`; count.classList.toggle('is-over-limit', size > 240);
      textarea.removeAttribute('aria-invalid'); alias.removeAttribute('aria-invalid');
      if (!current.sending) { send.textContent = '发送 ↗'; setReplyMessage(current, ''); }
      saveDraft(current);
    };
    replyForm.addEventListener('input', update);
    replyForm.addEventListener('submit', event => publishReply(event, current));
    update();
    (official ? view.officialSlot : view.slot).append(replyForm);
    textarea.focus({ preventScroll: true });
    replyForm.scrollIntoView({ behavior: reducedMotion.matches ? 'auto' : 'smooth', block: 'nearest' });
  }
  async function publishReply(event, current) {
    event.preventDefault();
    if (current.sending) return;
    const payload = { content: current.content.value.trim(), nickname: current.nickname.value.trim(), city: null,
      publishConsent: current.consent.checked, parentId: current.view.item.id, replyToOfficial: current.official };
    const size = [...payload.content].length;
    if ([...payload.content.replace(/\s/g, '')].length < 2 || size > 240) {
      setReplyMessage(current, size > 240 ? '最多 240 字。' : '至少写 2 个字。', true);
      current.content.setAttribute('aria-invalid', 'true'); current.content.focus(); return;
    }
    if ([...payload.nickname].length > 20) {
      setReplyMessage(current, '昵称最多 20 字。', true);
      current.nickname.setAttribute('aria-invalid', 'true'); current.nickname.focus(); return;
    }
    if (!payload.publishConsent) {
      setReplyMessage(current, '请先同意公开展示。', true); current.consent.focus(); return;
    }
    const fingerprint = JSON.stringify(payload);
    if (current.request?.fingerprint !== fingerprint) {
      if (!window.LeanCampaignSession) { setReplyMessage(current, '页面还未准备好，请刷新后重试。', true); return; }
      current.request = { fingerprint, id: window.LeanCampaignSession.randomId() };
    }
    saveDraft(current);
    current.sending = true; current.form.setAttribute('aria-busy', 'true');
    for (const input of current.form.querySelectorAll('input,textarea,button')) input.disabled = true;
    current.send.textContent = '发送中…'; setReplyMessage(current, '');
    try {
      await ensureIdentity();
      const data = await api('/api/campaign/comments', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, requestId: current.request.id }) });
      if (data.ok !== true || !validComment(data.item) || !['visible', 'hidden'].includes(data.item.status) ||
        data.item.rootId !== current.view.rootId) throw new Error('暂时无法确认是否发布，内容已保留，请重试。');
      rememberedNickname = payload.nickname; nickname.value = rememberedNickname;
      current.sending = false;
      const rootId = current.view.rootId;
      current.form.remove();
      if (editor === current) editor = null;
      replyDrafts.delete(current.key);
      const state = threads.get(rootId);
      if (state && data.item.status === 'visible') {
        // Display the confirmed response immediately, without waiting for another
        // round trip. No fabricated local message or counter is ever inserted.
        state.generation++;
        state.recent.set(data.item.id, data.item);
        state.items = [...new Map([...state.items, data.item].map(item => [item.id, item])).values()]
          .sort((a, b) => a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id));
        state.total = Math.max(state.total + (data.duplicate ? 0 : 1), state.items.length);
        state.expanded = true; state.message = '';
        renderThread(rootId);
        const node = state.views.get(data.item.id)?.node;
        if (node) {
          node.tabIndex = -1; node.focus({ preventScroll: true });
          node.scrollIntoView({ behavior: reducedMotion.matches ? 'auto' : 'smooth', block: 'nearest' });
        }
      } else setFeedStatus('这条回复已被团队收起。');
    } catch (error) {
      setReplyMessage(current, error.message, true);
    } finally {
      current.sending = false; current.form.setAttribute('aria-busy', 'false');
      for (const input of current.form.querySelectorAll('input,textarea,button')) input.disabled = false;
      current.send.textContent = '重试 ↗';
    }
  }
  function updateCount() {
    const count = [...content.value].length;
    byId('comment-count').textContent = `${count} / 240`;
    byId('comment-count').classList.toggle('is-over-limit', count > 240);
  }
  async function publish(event) {
    event.preventDefault();
    if (sending) return;
    const payload = {
      content: content.value.trim(), nickname: nickname.value.trim(),
      city: form.querySelector('input[name="comment-city"]:checked').value || null,
      publishConsent: consent.checked
    };
    const size = [...payload.content].length;
    if ([...payload.content.replace(/\s/g, '')].length < 2 || size > 240) {
      setFormMessage(size > 240 ? '最多 240 字。' : '至少写 2 个字。', true);
      content.setAttribute('aria-invalid', 'true'); content.focus(); return;
    }
    if ([...payload.nickname].length > 20) {
      setFormMessage('昵称最多 20 字。', true);
      byId('comment-options').open = true;
      nickname.setAttribute('aria-invalid', 'true'); nickname.focus(); return;
    }
    if (!payload.publishConsent) {
      setFormMessage('请先同意公开展示，或选择「私密留言」。', true);
      consent.focus(); return;
    }
    const fingerprint = JSON.stringify(payload);
    if (request?.fingerprint !== fingerprint) {
      if (!window.LeanCampaignSession) { setFormMessage('页面还未准备好，请刷新后重试。', true); return; }
      request = { fingerprint, id: window.LeanCampaignSession.randomId() };
    }
    sending = true;
    setFormMessage('正在发布…');
    form.setAttribute('aria-busy', 'true');
    for (const input of form.querySelectorAll('input,textarea,button')) input.disabled = true;
    submit.firstElementChild.textContent = '正在发布…';
    try {
      await ensureIdentity();
      const data = await api('/api/campaign/comments', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, requestId: request.id })
      });
      if (data.ok !== true || !validComment(data.item) || !['visible', 'hidden'].includes(data.item.status)) throw new Error('暂时无法确认是否发布，内容已保留，请重试。');
      // A response lost in transit keeps the same id for a safe retry. Reset
      // only after a server-confirmed result; never invent a local public post.
      rememberedNickname = payload.nickname;
      form.reset(); nickname.value = rememberedNickname; request = null; updateCount();
      byId('comment-options').open = false;
      setFormMessage(data.item.status === 'hidden' ? '这条留言已被团队收起。' : '已发布。');
      if (loadPromise) await loadPromise;
      await loadFeed(false, true);
    } catch (error) {
      setFormMessage(error.message, true);
    } finally {
      sending = false;
      form.setAttribute('aria-busy', 'false');
      for (const input of form.querySelectorAll('input,textarea,button')) input.disabled = false;
      submit.firstElementChild.textContent = request ? '重试' : '发布';
    }
  }

  function clearDrift() {
    clearTimeout(driftTimer);
    band.replaceChildren();
    laneReady = [0, 0];
  }
  function canDrift() {
    return motionEnabled && !reducedMotion.matches && heroVisible && document.visibilityState === 'visible';
  }
  function emitDrift() {
    if (!canDrift()) return;
    const now = Date.now(), lane = laneReady.findIndex(time => time <= now);
    if (lane >= 0 && band.children.length < 6) {
      const activeComments = new Set([...band.children].map(node => node.dataset.commentId).filter(Boolean));
      const publicPool = feedLoaded ? items.slice(0, 40).filter(item => !activeComments.has(item.id)) : [];
      const ambient = publicPool.length === 0 || emitted % 3 === 0;
      const pill = text('div', '', 'danmaku-pill');
      pill.dataset.lane = String(lane);
      pill.style.top = `${lane * 40}px`;
      let message, label;
      if (ambient) {
        const activeTopics = new Set([...band.children].map(node => node.dataset.topicIndex).filter(value => value !== undefined));
        // Play a shuffled round before recycling a phrase; toggling motion keeps the round.
        if (!topicQueue.length) {
          topicQueue = topics.map((_, index) => index);
          for (let i = topicQueue.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [topicQueue[i], topicQueue[j]] = [topicQueue[j], topicQueue[i]];
          }
        }
        const next = topicQueue.findIndex(index => index !== topicIndex && !activeTopics.has(String(index)));
        if (next < 0) {
          driftTimer = setTimeout(emitDrift, 2200);
          return;
        }
        topicIndex = topicQueue.splice(next, 1)[0];
        pill.dataset.topicIndex = String(topicIndex);
        message = topics[topicIndex];
      } else {
        const comment = publicPool[commentIndex++ % publicPool.length];
        pill.dataset.commentId = comment.id;
        label = comment.city ? `留言 · ${cityNames[comment.city]}` : '公开留言';
        message = comment.content.replace(/\s+/g, ' ');
      }
      const compact = [...message];
      if (label) pill.append(text('span', label, 'danmaku-label'));
      pill.append(text('span', compact.length > 44 ? compact.slice(0, 44).join('') + '…' : message));
      band.append(pill);
      const width = pill.getBoundingClientRect().width || 250;
      const distance = band.clientWidth + width + 20;
      const speed = band.clientWidth <= 600 ? 56 : 68;
      pill.style.setProperty('--drift-distance', `${-distance}px`);
      pill.style.setProperty('--drift-duration', `${distance / speed}s`);
      laneReady[lane] = now + (width + 80) / speed * 1000;
      pill.addEventListener('animationend', () => pill.remove(), { once: true });
      emitted++;
    }
    driftTimer = setTimeout(emitDrift, 2200);
  }
  function syncMotion() {
    clearDrift();
    const enabled = motionEnabled && !reducedMotion.matches;
    toggle.setAttribute('aria-pressed', String(enabled));
    toggle.setAttribute('aria-label', reducedMotion.matches ? '已跟随系统减少动态效果设置，弹幕关闭' : enabled ? '关闭弹幕' : '开启弹幕');
    toggle.querySelector('span').textContent = reducedMotion.matches ? '静态' : enabled ? '开' : '关';
    toggle.disabled = reducedMotion.matches;
    if (canDrift()) emitDrift();
  }
  form.addEventListener('submit', publish);
  form.addEventListener('input', () => {
    updateCount();
    content.removeAttribute('aria-invalid'); nickname.removeAttribute('aria-invalid');
    setFormMessage('');
    if (!sending) submit.firstElementChild.textContent = '发布';
  });
  byId('comment-private').addEventListener('click', () => byId('open-feedback').click());
  refresh.addEventListener('click', () => loadFeed());
  more.addEventListener('click', () => {
    if (items.length >= MAX_VISIBLE) {
      items = []; nextCursor = null;
      return loadFeed();
    }
    return loadFeed(true);
  });
  toggle.addEventListener('click', () => {
    motionEnabled = !motionEnabled;
    try { sessionStorage.setItem('leanclub-comment-motion', motionEnabled ? 'on' : 'off'); } catch { /* Preference is optional. */ }
    syncMotion();
  });
  reducedMotion.addEventListener('change', syncMotion);
  document.addEventListener('visibilitychange', () => {
    syncMotion(); schedulePoll();
    if (pageActive() && (heroVisible || conversationVisible)) loadFeed(false, true);
  });
  window.addEventListener('blur', () => clearTimeout(pollTimer));
  window.addEventListener('focus', () => {
    schedulePoll();
    if (pageActive() && (heroVisible || conversationVisible)) loadFeed(false, true);
  });
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (entry.target.id === 'vote-hero') heroVisible = entry.isIntersecting;
        else conversationVisible = entry.isIntersecting;
      }
      syncMotion(); schedulePoll();
    }, { threshold: 0 });
    observer.observe(byId('vote-hero')); observer.observe(byId('conversation'));
  }
  updateCount(); syncMotion(); loadFeed();
})();
