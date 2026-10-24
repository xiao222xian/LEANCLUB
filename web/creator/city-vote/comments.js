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
  let request = null, sending = false, loadPromise = null, feedSnapshot = '';
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
      Number.isFinite(new Date(item.createdAt).getTime());
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
  function renderList() {
    const snapshot = JSON.stringify(items);
    if (snapshot !== feedSnapshot) {
      const nodes = items.map(item => {
        const card = text('li', '', 'comment-card');
        const head = text('div', '', 'comment-card-head');
        head.append(text('span', item.nickname || '薄肌同好', 'comment-author'));
        if (item.city) head.append(text('span', cityNames[item.city], 'comment-city'));
        const time = text('time', formatDate(item.createdAt));
        time.dateTime = item.createdAt;
        head.append(time);
        card.append(head, text('p', item.content, 'comment-body'));
        return card;
      });
      list.replaceChildren(...nodes);
      feedSnapshot = snapshot;
    }
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
    if (document.visibilityState !== 'visible' || !(heroVisible || conversationVisible)) return;
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
      nextCursor = cursor;
      total = latestTotal;
      feedLoaded = true;
      renderList();
      setFeedStatus('');
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
      setFormMessage('请先同意公开展示，或选择「私密意见」。', true);
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
      form.reset(); request = null; updateCount();
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
    if (document.visibilityState === 'visible' && (heroVisible || conversationVisible)) loadFeed(false, true);
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
