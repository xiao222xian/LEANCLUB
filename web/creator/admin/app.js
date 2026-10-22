(() => {
  'use strict';

  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
  const CITIES = [
    { id: 'shenzhen', name: '深圳', code: 'SHENZHEN', short: 'SZ' },
    { id: 'zhuhai', name: '珠海', code: 'ZHUHAI', short: 'ZH' },
    { id: 'guangzhou', name: '广州', code: 'GUANGZHOU', short: 'GZ' }
  ];
  const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
  const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);
  const USERNAME = /^[A-Za-z0-9][A-Za-z0-9_.-]{2,39}$/;
  const EMAIL_LOCAL = /^[A-Za-z0-9!#$%&'*+\/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+\/=?^_`{|}~-]+)*$/;
  const EMAIL_LABEL = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/;
  const numberFormat = new Intl.NumberFormat('zh-CN');
  const dateFormat = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false
  });
  const state = {
    authenticated: false, csrf: '', setup: false, epoch: 0,
    panel: 'overview', statsLoading: false, postersLoading: false,
    postersLoaded: false, posters: new Map(), busy: 0,
    feishu: null, feishuLoading: false, feishuBusy: false, feishuDirty: false, feishuTimer: null, feishuRevision: 0
  };

  class RequestError extends Error {
    constructor(message, status = 0) { super(message); this.status = status; }
  }

  function message(element, text, tone = '') {
    element.textContent = text || '';
    element.hidden = !text;
    if (tone) element.dataset.tone = tone;
    else delete element.dataset.tone;
  }

  function announce(text) { $('#live-status').textContent = text; }
  function validUsername(value) {
    if (value.length > 254 || /\s/.test(value)) return false;
    if (USERNAME.test(value)) return true;
    const parts = value.split('@');
    if (parts.length !== 2) return false;
    const [local, domain] = parts;
    const labels = domain.split('.');
    return local.length >= 1 && local.length <= 64 && EMAIL_LOCAL.test(local)
      && labels.length >= 2 && labels.every(label => EMAIL_LABEL.test(label));
  }
  function validateUsername() {
    const input = $('#username');
    const valid = !input.value || validUsername(input.value);
    input.setCustomValidity(valid ? '' : '请输入3–40位用户名（字母或数字开头，可含 . _ -），或有效的英文邮箱（最长254位），不要包含空格。');
    return valid;
  }
  function count(value) { return Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0; }
  function formatCount(value) { return numberFormat.format(count(value)); }
  function position(value, fallback = 50) { const n = Number(value); return Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : fallback; }
  function formatDate(value) {
    const date = new Date(value);
    return value && Number.isFinite(date.getTime()) ? dateFormat.format(date) : '';
  }

  async function api(path, options = {}) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), options.file ? 60000 : 25000);
    const method = options.method || 'GET';
    const headers = { Accept: 'application/json' };
    let body;
    if (method !== 'GET') {
      headers['X-CSRF-Token'] = state.csrf;
      headers['Content-Type'] = options.file ? options.file.type : 'application/json';
      body = options.file || JSON.stringify(options.body || {});
    }
    try {
      const response = await fetch(path, {
        method, headers, body, credentials: 'same-origin', cache: 'no-store',
        signal: controller.signal
      });
      let data;
      try { data = await response.json(); }
      catch { throw new RequestError('管理服务返回了无法读取的内容，请稍后重试。', response.status); }
      if (!response.ok) {
        const serverError = typeof data.error === 'string' ? data.error : data.error?.message || data.message;
        const detail = typeof serverError === 'string' ? serverError : `请求未完成（${response.status}），请稍后重试。`;
        if (response.status === 401 && state.authenticated) {
          showAuth({ setupRequired: false, setupAvailable: false });
          message($('#auth-error'), '登录已过期，请重新登录。');
          $('#username').focus();
        }
        throw new RequestError(detail, response.status);
      }
      return data;
    } catch (error) {
      if (error instanceof RequestError) throw error;
      if (error.name === 'AbortError') throw new RequestError('请求超时。请刷新确认最新状态，再重试。');
      throw new RequestError('暂时无法连接管理服务，请检查网络后重试。');
    } finally { window.clearTimeout(timer); }
  }

  function authBusy(busy) {
    $('#auth-submit').disabled = busy;
    $('#auth-submit').setAttribute('aria-busy', String(busy));
    for (const input of $$('#auth-form input')) input.disabled = busy;
    for (const button of $$('#auth-form .password-toggle')) button.disabled = busy;
    $('#auth-submit-label').textContent = busy ? (state.setup ? '正在创建账号' : '正在登录') : (state.setup ? '创建管理员并进入' : '登录管理台');
  }

  function releaseDraft(record) {
    if (record.objectUrl) URL.revokeObjectURL(record.objectUrl);
    record.objectUrl = '';
    record.file = null;
    record.selectionVersion += 1;
  }

  function clearProtectedContent() {
    for (const record of state.posters.values()) releaseDraft(record);
    state.posters.clear();
    state.postersLoaded = false;
    state.statsLoading = false;
    state.postersLoading = false;
    state.busy = 0;
    window.clearTimeout(state.feishuTimer);
    state.feishuTimer = null;
    state.feishu = null;
    state.feishuLoading = false;
    state.feishuBusy = false;
    state.feishuDirty = false;
    $('#feishu-form').reset();
    $('#feishu-content').hidden = true;
    $('#feishu-loading').hidden = true;
    $('#feishu-connection').textContent = '未配置';
    $('#feishu-delivery-state').textContent = '暂无记录';
    $('#feishu-delivery-detail').textContent = '';
    $('#feishu-webhook').placeholder = '粘贴飞书机器人的 Webhook';
    $('#feishu-secret').placeholder = '机器人开启签名校验时填写';
    $('#feishu-webhook-hint').textContent = '飞书群设置 / 群机器人 / 添加自定义机器人';
    $('#feishu-secret-hint').textContent = '未开启签名校验可留空';
    $('#feishu-clear-secret-wrap').hidden = true;
    $('#feishu-retry').hidden = true;
    message($('#feishu-queue'), '');
    message($('#feishu-error'), '');
    message($('#feishu-feedback'), '');
    feishuControls();
    $('#posters-grid').replaceChildren();
    $('#account-name').textContent = '';
    $('#votes-list').replaceChildren();
    $('#daily-table').replaceChildren();
    $('#trend-chart').replaceChildren();
    for (const metric of $$('[id^="metric-"]')) metric.textContent = '—';
    $('#last-updated').textContent = '等待数据';
    $('#trend-range').textContent = '访问与新增投票';
    $('#votes-empty').hidden = true;
    $('#stats-content').setAttribute('aria-busy', 'true');
    message($('#stats-error'), '');
    message($('#posters-error'), '');
    message($('#app-message'), '');
    $('#logout').disabled = false;
    $('#logout').textContent = '退出';
  }

  function showAuth(session) {
    state.authenticated = false;
    state.csrf = session.csrfToken || '';
    state.setup = Boolean(session.setupRequired && session.setupAvailable);
    state.epoch += 1;
    clearProtectedContent();
    $('#admin-view').hidden = true;
    $('#auth-view').hidden = false;
    $('#auth-loading').hidden = true;
    $('#auth-title').textContent = state.setup ? '建立管理账号' : '进入俱乐部';
    $('#auth-description').textContent = state.setup ? '设置账号，开启你的管理台。' : '城市、海报、每一次见面的可能。';
    $('#setup-unavailable').hidden = !(session.setupRequired && !session.setupAvailable);
    $('#auth-form').hidden = Boolean(session.setupRequired && !session.setupAvailable);
    $('#confirm-field').hidden = !state.setup;
    $('#confirm-password').required = state.setup;
    $('#username-hint').hidden = !state.setup;
    $('#password-hint').hidden = !state.setup;
    $('#username').maxLength = 254;
    $('#username').removeAttribute('pattern');
    $('#username').setCustomValidity('');
    if (state.setup) {
      $('#username').minLength = 3;
      $('#username').setAttribute('aria-describedby', 'username-hint');
      $('#password').minLength = 12;
      $('#password').autocomplete = 'new-password';
      $('#password').setAttribute('aria-describedby', 'password-hint');
    } else {
      $('#username').removeAttribute('minlength');
      $('#username').removeAttribute('pattern');
      $('#username').removeAttribute('aria-describedby');
      $('#password').removeAttribute('minlength');
      $('#password').autocomplete = 'current-password';
      $('#password').removeAttribute('aria-describedby');
    }
    $('#password').value = '';
    $('#confirm-password').value = '';
    $('#confirm-password').setCustomValidity('');
    for (const button of $$('.password-toggle')) {
      const target = document.getElementById(button.dataset.passwordTarget);
      target.type = 'password';
      button.textContent = '显示';
      button.setAttribute('aria-pressed', 'false');
      button.setAttribute('aria-label', target.id === 'confirm-password' ? '显示确认密码' : '显示密码');
    }
    authBusy(false);
  }

  async function loadSession() {
    $('#auth-loading').hidden = false;
    $('#auth-form').hidden = true;
    $('#setup-unavailable').hidden = true;
    message($('#auth-error'), '');
    $('#retry-session').disabled = true;
    try {
      const session = await api('/api/admin/session');
      if (session.authenticated) enterAdmin(session);
      else showAuth(session);
    } catch (error) {
      $('#auth-loading').hidden = true;
      message($('#auth-error'), error.message);
      $('#setup-unavailable').hidden = false;
      $('#setup-unavailable').querySelectorAll('p').forEach(element => { element.hidden = true; });
    } finally { $('#retry-session').disabled = false; }
  }

  function enterAdmin(session) {
    state.authenticated = true;
    state.csrf = session.csrfToken || '';
    state.epoch += 1;
    $('#account-name').textContent = session.username || '管理员';
    $('#password').value = '';
    $('#confirm-password').value = '';
    message($('#auth-error'), '');
    $('#auth-view').hidden = true;
    $('#admin-view').hidden = false;
    selectPanel('overview', false);
    loadStats();
    document.title = '数据概览 · Lean Club';
  }

  function validateStats(data) {
    const nonnegativeInteger = value => Number.isSafeInteger(value) && value >= 0;
    const metricKeys = ['pv', 'uv', 'todayPv', 'todayUv'];
    const valid = data && nonnegativeInteger(data.totalVotes)
      && Array.isArray(data.votes)
      && CITIES.every(city => data.votes.some(vote => vote.city === city.id && nonnegativeInteger(vote.count)))
      && ['home', 'vote'].every(page => metricKeys.every(key => nonnegativeInteger(data.metrics?.[page]?.[key])))
      && Array.isArray(data.daily)
      && data.daily.every(day => typeof day.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(day.date)
        && ['homePv', 'votePv', 'newVotes'].every(key => nonnegativeInteger(day[key])));
    if (!valid) throw new RequestError('统计数据不完整，请稍后刷新。');
  }

  $('#auth-form').addEventListener('submit', async event => {
    event.preventDefault();
    if ($('#auth-submit').disabled) return;
    message($('#auth-error'), '');
    if (!validateUsername()) {
      $('#username').reportValidity();
      return;
    }
    if (state.setup && $('#password').value !== $('#confirm-password').value) {
      $('#confirm-password').setCustomValidity('两次输入的密码不一致。');
      $('#confirm-password').reportValidity();
      return;
    }
    if (!$('#auth-form').reportValidity()) return;
    authBusy(true);
    try {
      const session = await api(state.setup ? '/api/admin/setup' : '/api/admin/login', {
        method: 'POST', body: { username: $('#username').value, password: $('#password').value }
      });
      if (!session.authenticated) throw new RequestError('未能建立登录状态，请重新登录。');
      enterAdmin(session);
      $('#main').focus({ preventScroll: true });
      window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
    } catch (error) {
      message($('#auth-error'), error.message);
      if (state.setup && error.status === 409) {
        showAuth({ setupRequired: false, setupAvailable: false });
        message($('#auth-error'), '管理员已创建，请使用现有账号登录。');
      }
    } finally { authBusy(false); }
  });

  $('#username').addEventListener('input', validateUsername);
  $('#confirm-password').addEventListener('input', () => $('#confirm-password').setCustomValidity(''));
  $('#password').addEventListener('input', () => $('#confirm-password').setCustomValidity(''));
  for (const button of $$('.password-toggle')) {
    button.addEventListener('click', () => {
      const input = document.getElementById(button.dataset.passwordTarget);
      const visible = input.type === 'password';
      input.type = visible ? 'text' : 'password';
      button.textContent = visible ? '隐藏' : '显示';
      button.setAttribute('aria-pressed', String(visible));
      button.setAttribute('aria-label', `${visible ? '隐藏' : '显示'}${input.id === 'confirm-password' ? '确认' : ''}密码`);
    });
  }
  $('#retry-session').addEventListener('click', () => {
    $('#setup-unavailable').querySelectorAll('p').forEach(element => { element.hidden = false; });
    loadSession();
  });

  $('#logout').addEventListener('click', async () => {
    if (state.busy || $('#logout').disabled) return;
    $('#logout').disabled = true;
    $('#logout').textContent = '退出中';
    try {
      await api('/api/admin/logout', { method: 'POST' });
      showAuth({ setupRequired: false, setupAvailable: false });
      document.title = '内容管理 · Lean Club';
      $('#username').focus();
      announce('已退出管理台。');
    } catch (error) {
      if (state.authenticated) message($('#app-message'), error.message);
    } finally { $('#logout').disabled = false; $('#logout').textContent = '退出'; }
  });

  function selectPanel(name, focus = false) {
    state.panel = name;
    for (const tab of $$('[role="tab"]')) {
      const active = tab.dataset.panel === name;
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      if (active && focus) tab.focus();
    }
    for (const panel of $$('[role="tabpanel"]')) panel.hidden = panel.id !== `panel-${name}`;
    document.title = `${({ posters: '城市海报', overview: '数据概览', feishu: '飞书通知' })[name]} · Lean Club`;
    if (name === 'posters' && !state.postersLoaded && !state.postersLoading) loadPosters();
    if (name === 'overview') refreshVisibleStats();
    window.clearTimeout(state.feishuTimer);
    state.feishuTimer = null;
    if (name === 'feishu') loadFeishu();
  }
  for (const tab of $$('[role="tab"]')) {
    tab.addEventListener('click', () => selectPanel(tab.dataset.panel));
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const names = $$('[role="tab"]').map(item => item.dataset.panel);
      const current = names.indexOf(state.panel);
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? names.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + names.length) % names.length;
      const name = names[index];
      selectPanel(name, true);
    });
  }

  function renderVotes(data) {
    const total = count(data.totalVotes);
    const rows = Array.isArray(data.votes) ? data.votes : [];
    const fragment = document.createDocumentFragment();
    for (const city of CITIES) {
      const vote = rows.find(item => item.city === city.id) || {};
      const value = count(vote.count);
      const share = total ? Math.max(0, Math.min(100, value / total * 100)) : 0;
      const label = `${share.toFixed(1)}%`;
      const row = $('#vote-row-template').content.firstElementChild.cloneNode(true);
      row.dataset.city = city.id;
      $('.city-code', row).textContent = city.short;
      $('h3', row).textContent = city.name;
      $('.vote-count', row).textContent = `${formatCount(value)} 票`;
      $('.vote-share', row).textContent = label;
      const bar = $('.vote-bar', row);
      bar.setAttribute('aria-label', `${city.name}投票占比`);
      bar.setAttribute('aria-valuenow', share.toFixed(1));
      bar.setAttribute('aria-valuetext', `${formatCount(value)} 票，占 ${label}`);
      $('span', bar).style.width = `${share}%`;
      fragment.append(row);
    }
    $('#votes-list').replaceChildren(fragment);
    $('#votes-empty').hidden = total !== 0;
  }

  function svgElement(tag, attributes = {}, content = '') {
    const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
    if (content) element.textContent = content;
    return element;
  }

  function renderTrend(rows) {
    const daily = Array.isArray(rows) ? rows.slice(-14) : [];
    const chart = $('#trend-chart');
    const body = $('#daily-table');
    body.replaceChildren();
    for (const day of daily) {
      const row = document.createElement('tr');
      for (const value of [String(day.date || ''), formatCount(day.homePv), formatCount(day.votePv), formatCount(day.newVotes)]) {
        const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
      }
      body.append(row);
    }
    const svg = svgElement('svg', { viewBox: '0 0 580 195', preserveAspectRatio: 'none', 'aria-hidden': 'true', focusable: 'false' });
    const left = 34, right = 568, top = 13, bottom = 165;
    const maximum = Math.max(1, ...daily.flatMap(day => [count(day.homePv), count(day.votePv), count(day.newVotes)]));
    const ceiling = maximum <= 4 ? 4 : Math.ceil(maximum / 4) * 4;
    const y = value => bottom - count(value) / ceiling * (bottom - top);
    const x = index => left + index / Math.max(1, daily.length - 1) * (right - left);
    for (let i = 0; i < 3; i += 1) {
      const value = ceiling * i / 2;
      svg.append(svgElement('line', { x1: left, x2: right, y1: y(value), y2: y(value), class: 'chart-grid-line' }));
      svg.append(svgElement('text', { x: left - 9, y: y(value) + 3, 'text-anchor': 'end', class: 'chart-axis' }, numberFormat.format(value)));
    }
    const series = [
      { key: 'homePv', color: '#1b2023' },
      { key: 'votePv', color: '#8d9e41' },
      { key: 'newVotes', color: '#626eef' }
    ];
    for (const item of series) {
      if (!daily.length) continue;
      const d = daily.map((day, index) => `${index ? 'L' : 'M'} ${x(index).toFixed(2)} ${y(day[item.key]).toFixed(2)}`).join(' ');
      svg.append(svgElement('path', { d, class: 'chart-line', stroke: item.color }));
      daily.forEach((day, index) => {
        if (!count(day[item.key]) && daily.some(record => count(record[item.key]))) return;
        if (daily.length > 1 && !count(day[item.key]) && index !== daily.length - 1) return;
        svg.append(svgElement('circle', { cx: x(index), cy: y(day[item.key]), r: 3, fill: item.color, class: 'chart-point' }));
      });
    }
    const indices = [...new Set([0, Math.floor((daily.length - 1) / 2), daily.length - 1])].filter(index => index >= 0);
    for (const index of indices) {
      const label = String(daily[index]?.date || '').slice(5).replace('-', '/');
      svg.append(svgElement('text', { x: x(index), y: 190, 'text-anchor': index === 0 ? 'start' : index === daily.length - 1 ? 'end' : 'middle', class: 'chart-axis' }, label));
    }
    chart.replaceChildren(svg);
    const homeTotal = daily.reduce((sum, day) => sum + count(day.homePv), 0);
    const voteTotal = daily.reduce((sum, day) => sum + count(day.votePv), 0);
    const newTotal = daily.reduce((sum, day) => sum + count(day.newVotes), 0);
    chart.setAttribute('aria-label', `最近 ${daily.length} 天，主站浏览 ${formatCount(homeTotal)} 次，投票页浏览 ${formatCount(voteTotal)} 次，新增投票 ${formatCount(newTotal)} 票。逐日数据可在下方展开查看。`);
    $('#trend-range').textContent = daily.length ? `${String(daily[0].date).slice(5).replace('-', '/')} — ${String(daily[daily.length - 1].date).slice(5).replace('-', '/')}` : '暂无数据';
  }

  async function loadStats(options = {}) {
    if (!state.authenticated || state.statsLoading) return;
    const silent = options.silent === true;
    const epoch = state.epoch;
    state.statsLoading = true;
    $('#refresh-stats').disabled = true;
    $('#refresh-stats').setAttribute('aria-busy', 'true');
    $('#stats-content').setAttribute('aria-busy', 'true');
    message($('#stats-error'), '');
    try {
      const data = await api('/api/admin/stats');
      if (state.epoch !== epoch) return;
      validateStats(data);
      const home = data.metrics?.home || {};
      const vote = data.metrics?.vote || {};
      const metrics = {
        'metric-votes': data.totalVotes, 'metric-home-pv': home.pv,
        'metric-home-today': home.todayPv, 'metric-home-today-uv': home.todayUv,
        'metric-home-uv': home.uv, 'metric-vote-pv': vote.pv,
        'metric-vote-today': vote.todayPv, 'metric-vote-uv': vote.uv
      };
      for (const [id, value] of Object.entries(metrics)) document.getElementById(id).textContent = formatCount(value);
      renderVotes(data);
      renderTrend(data.daily);
      const updated = formatDate(data.updatedAt);
      $('#last-updated').textContent = updated ? `更新于 ${updated}` : '数据已更新';
      if (!silent) announce('统计数据已更新。');
    } catch (error) {
      if (state.epoch === epoch) message($('#stats-error'), `${error.message} 已加载的数据保持上次结果。`);
    } finally {
      if (state.epoch === epoch) {
        state.statsLoading = false;
        $('#refresh-stats').disabled = false;
        $('#refresh-stats').setAttribute('aria-busy', 'false');
        $('#stats-content').setAttribute('aria-busy', 'false');
      }
    }
  }
  $('#refresh-stats').addEventListener('click', loadStats);

  function refreshVisibleStats() {
    if (document.visibilityState === 'visible' && state.authenticated && state.panel === 'overview') {
      loadStats({ silent: true });
    }
  }
  let statsRefreshTimer = window.setInterval(refreshVisibleStats, 10000);
  document.addEventListener('visibilitychange', refreshVisibleStats);
  window.addEventListener('focus', refreshVisibleStats);
  window.addEventListener('pageshow', event => {
    if (event.persisted) {
      if (!statsRefreshTimer) statsRefreshTimer = window.setInterval(refreshVisibleStats, 10000);
      loadSession();
    }
  });
  let voteUpdates;
  if ('BroadcastChannel' in window) {
    try {
      voteUpdates = new BroadcastChannel('lean-campaign-events');
      voteUpdates.onmessage = event => {
        if (event.data?.type === 'vote-updated') refreshVisibleStats();
      };
    } catch { /* Visibility refresh and polling work without cross-tab messages. */ }
  }
  window.addEventListener('pagehide', event => {
    window.clearInterval(statsRefreshTimer);
    statsRefreshTimer = null;
    if (!event.persisted) voteUpdates?.close();
    window.clearTimeout(state.feishuTimer);
    state.feishuTimer = null;
    $('#feishu-webhook').value = '';
    $('#feishu-secret').value = '';
  });

  function feishuControls() {
    const locked = state.feishuBusy || (state.feishuLoading && !state.feishu);
    for (const control of $$('#feishu-form input')) control.disabled = locked;
    $('#feishu-save').disabled = locked;
    $('#feishu-save').textContent = state.feishuBusy ? '处理中' : '保存设置';
    $('#feishu-test').disabled = locked || !state.feishu?.configured || state.feishuDirty;
    $('#feishu-summary').disabled = locked || !state.feishu?.configured || state.feishuDirty;
    $('#feishu-retry').disabled = locked || state.feishuDirty || !state.feishu?.configured;
    $('#refresh-feishu').disabled = locked || state.feishuLoading;
    $('#logout').disabled = state.busy > 0;
    $('#feishu-form').setAttribute('aria-busy', String(locked));
  }

  function renderFeishu(settings, resetForm = false) {
    if (!settings || typeof settings.enabled !== 'boolean' || typeof settings.configured !== 'boolean') {
      throw new RequestError('通知设置不完整，请重新加载。');
    }
    state.feishu = settings;
    $('#feishu-content').hidden = false;
    if (resetForm || !state.feishuDirty) $('#feishu-enabled').checked = settings.enabled;
    if (resetForm) {
      state.feishuDirty = false;
      $('#feishu-webhook').value = '';
      $('#feishu-secret').value = '';
      $('#feishu-clear-secret').checked = false;
    }
    $('#feishu-connection').textContent = settings.enabled ? '通知已开启' : settings.configured ? '已配置 · 已暂停' : '未配置';
    $('#feishu-connection').dataset.connected = String(settings.enabled);
    $('#feishu-webhook').placeholder = settings.configured ? '已保存 · 留空保留，输入可替换' : '粘贴飞书机器人的 Webhook';
    $('#feishu-webhook-hint').textContent = settings.configured ? '地址已保存，不在页面回显。' : '飞书群设置 / 群机器人 / 添加自定义机器人';
    $('#feishu-secret').placeholder = settings.hasSigningSecret ? '已保存签名密钥 · 留空保留' : '机器人开启签名校验时填写';
    $('#feishu-secret-hint').textContent = settings.hasSigningSecret ? '留空保留；更换机器人时，请一并填写新密钥。' : '未开启签名校验可留空';
    $('#feishu-clear-secret-wrap').hidden = !settings.hasSigningSecret;
    const delivery = settings.lastDelivery;
    const labels = { pending: '等待发送', sending: '正在发送', retrying: '等待自动重试', sent: '已发送', failed: '发送失败', cancelled: '已取消' };
    $('#feishu-delivery-state').textContent = delivery ? labels[delivery.status] || '等待更新' : '暂无记录';
    $('#feishu-delivery-state').dataset.status = delivery?.status || '';
    const type = ({ test: '测试消息', summary: '当前票数汇总', change: '历史改投通知', new: '新增投票通知' })[delivery?.kind] || '投票通知';
    const time = formatDate(delivery?.sentAt || delivery?.createdAt);
    $('#feishu-delivery-detail').textContent = delivery ? `${type}${time ? ` · ${time}` : ''}${delivery.error ? ` · ${delivery.error}` : ''}` : '保存配置后，可发送测试消息确认连接。';
    const pending = count(settings.pendingCount), failed = count(settings.failedCount);
    message($('#feishu-queue'), [pending ? `${pending} 条待发送` : '', failed ? `${failed} 条发送失败` : ''].filter(Boolean).join(' · '));
    $('#feishu-retry').hidden = failed === 0;
    feishuControls();
  }

  function scheduleFeishu() {
    window.clearTimeout(state.feishuTimer);
    state.feishuTimer = null;
    if (!state.authenticated || state.feishuBusy || state.panel !== 'feishu' || document.visibilityState !== 'visible') return;
    const delay = count(state.feishu?.pendingCount) || ['pending', 'sending', 'retrying'].includes(state.feishu?.lastDelivery?.status) ? 3000 : 10000;
    state.feishuTimer = window.setTimeout(() => loadFeishu({ silent: true }), delay);
  }

  async function loadFeishu(options = {}) {
    if (!state.authenticated || state.feishuLoading || state.feishuBusy) return;
    const epoch = state.epoch;
    const revision = state.feishuRevision;
    const silent = options.silent === true;
    state.feishuLoading = true;
    if (!state.feishu) $('#feishu-loading').hidden = false;
    if (!silent) message($('#feishu-error'), '');
    if (!silent || !state.feishu) feishuControls();
    try {
      const data = await api('/api/admin/feishu');
      if (state.epoch !== epoch || state.feishuRevision !== revision || state.feishuBusy) return;
      renderFeishu(data);
      message($('#feishu-error'), '');
    } catch (error) {
      if (state.epoch === epoch && state.feishuRevision === revision && !state.feishuBusy) message($('#feishu-error'), error.message);
    } finally {
      if (state.epoch === epoch) {
        state.feishuLoading = false;
        $('#feishu-loading').hidden = true;
        feishuControls();
        scheduleFeishu();
      }
    }
  }

  async function changeFeishu(action) {
    if (state.feishuBusy || (state.feishuLoading && !state.feishu) || !state.authenticated) return;
    if (action !== 'save' && state.feishuDirty) {
      message($('#feishu-feedback'), '请先保存设置，再发送消息。', 'error');
      return;
    }
    const epoch = state.epoch;
    const body = action === 'save' ? {
      enabled: $('#feishu-enabled').checked,
      webhook: $('#feishu-webhook').value.trim(),
      signingSecret: $('#feishu-secret').value.trim(),
      clearSigningSecret: $('#feishu-clear-secret').checked
    } : {};
    if (action === 'save' && body.enabled && !body.webhook && !state.feishu?.configured) {
      message($('#feishu-feedback'), '先填写机器人的 Webhook 地址。', 'error');
      $('#feishu-webhook').focus();
      return;
    }
    state.feishuBusy = true;
    state.feishuRevision += 1;
    state.busy += 1;
    window.clearTimeout(state.feishuTimer);
    feishuControls();
    message($('#feishu-error'), '');
    message($('#feishu-feedback'), action === 'save' ? '正在保存设置…' : '正在提交发送任务…');
    try {
      const data = await api(`/api/admin/feishu${action === 'save' ? '' : `/${action}`}`, { method: 'POST', body });
      if (state.epoch !== epoch) return;
      renderFeishu(action === 'save' ? data : data.settings, action === 'save');
      const feedback = action === 'save' ? '设置已保存。'
        : action === 'test' ? '测试消息已加入队列，请查看发送状态。'
        : action === 'summary' ? '当前三城汇总已加入队列，请查看发送状态。'
        : `${formatCount(data.queuedCount)} 条消息已重新加入队列。`;
      message($('#feishu-feedback'), feedback, 'success');
    } catch (error) {
      if (state.epoch === epoch) message($('#feishu-feedback'), error.message, 'error');
    } finally {
      if (state.epoch === epoch) {
        state.feishuBusy = false;
        state.busy = Math.max(0, state.busy - 1);
        feishuControls();
        scheduleFeishu();
      }
    }
  }

  $('#feishu-form').addEventListener('submit', event => { event.preventDefault(); changeFeishu('save'); });
  $('#feishu-test').addEventListener('click', () => changeFeishu('test'));
  $('#feishu-summary').addEventListener('click', () => changeFeishu('summary'));
  $('#feishu-retry').addEventListener('click', () => changeFeishu('retry'));
  $('#refresh-feishu').addEventListener('click', () => loadFeishu());
  for (const input of $$('#feishu-form input')) input.addEventListener('input', () => {
    state.feishuDirty = true;
    if (input.id === 'feishu-secret' && input.value) $('#feishu-clear-secret').checked = false;
    if (input.id === 'feishu-clear-secret' && input.checked) $('#feishu-secret').value = '';
    message($('#feishu-feedback'), '有未保存的更改。');
    feishuControls();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && state.panel === 'feishu') loadFeishu({ silent: true });
    else { window.clearTimeout(state.feishuTimer); state.feishuTimer = null; }
  });
  window.addEventListener('focus', () => { if (state.panel === 'feishu') loadFeishu({ silent: true }); });

  function safeImageUrl(value, fallback) {
    try {
      const url = new URL(value || fallback, window.location.origin);
      return url.origin === window.location.origin && ['http:', 'https:'].includes(url.protocol) ? url.href : fallback;
    } catch { return fallback; }
  }

  function normalisePoster(city, data) {
    return {
      ...data, id: city.id, name: city.name,
      posterUrl: safeImageUrl(data.posterUrl, `/city-vote/assets/alan-v1/${city.id}.webp`),
      positionX: position(data.positionX), positionY: position(data.positionY)
    };
  }

  function updatePosterMetadata(record) {
    const { element, current } = record;
    $('.poster-source-type', element).textContent = current.isDefault ? '默认海报' : '已发布自定义海报';
    const time = $('.poster-time', element);
    time.textContent = current.updatedAt ? `${formatDate(current.updatedAt)} 更新` : '原始素材';
    if (current.updatedAt) time.dateTime = current.updatedAt;
    else time.removeAttribute('datetime');
  }

  function focusChanged(record) {
    return Number($('.focus-x', record.element).value) !== record.current.positionX || Number($('.focus-y', record.element).value) !== record.current.positionY;
  }

  function updatePosterControls(record) {
    const { element, busy } = record;
    const hasFile = Boolean(record.file);
    $('.publish-poster', element).disabled = busy || !hasFile;
    $('.choose-poster', element).disabled = busy;
    $('.discard-poster', element).disabled = busy;
    $('.discard-poster', element).hidden = !hasFile;
    $('.save-focus', element).disabled = busy || hasFile || !focusChanged(record);
    $('.reset-poster', element).disabled = busy;
    $('.confirm-reset', element).disabled = busy;
    $('.cancel-reset', element).disabled = busy;
    $('.focus-unsaved', element).hidden = !focusChanged(record);
    for (const input of $$('input', element)) input.disabled = busy;
    for (const button of $$('.preview-switch button', element)) button.disabled = busy;
    $('.focus-controls .field-hint', element).textContent = hasFile ? '新图发布后，可保存当前预览的构图。' : '预览为裁切示意，保存后可打开投票页确认。';
    $('#refresh-posters').disabled = state.postersLoading || state.busy > 0;
    $('#logout').disabled = state.busy > 0;
  }

  function updateFocusPreview(record) {
    const x = $('.focus-x', record.element).value;
    const y = $('.focus-y', record.element).value;
    $('.focus-x-value', record.element).textContent = `${x}%`;
    $('.focus-y-value', record.element).textContent = `${y}%`;
    $('.poster-image', record.element).style.objectPosition = `${x}% ${y}%`;
    updatePosterControls(record);
  }

  function previewPoster(record, draft = Boolean(record.file)) {
    const { element, current } = record;
    const showingDraft = draft && Boolean(record.objectUrl);
    element.dataset.preview = showingDraft ? 'draft' : 'current';
    const image = $('.poster-image', element);
    const nextSource = showingDraft ? record.objectUrl : current.posterUrl;
    if (image.src !== nextSource) { $('.poster-image-error', element).hidden = true; image.src = nextSource; }
    image.alt = `${current.name}${showingDraft ? '待发布' : '当前在线'}海报`;
    $('.poster-badge', element).textContent = showingDraft ? '待发布预览' : '当前在线';
    $('.preview-switch', element).hidden = !record.file;
    $('.selected-file', element).hidden = !record.file;
    $('.poster-note', element).hidden = !record.file;
    $('.selected-file', element).textContent = record.file ? `${record.file.name} · ${(record.file.size / 1024 / 1024).toFixed(2)} MB` : '';
    for (const button of $$('.preview-switch button', element)) button.setAttribute('aria-pressed', String((button.dataset.preview === 'draft') === showingDraft));
    updateFocusPreview(record);
  }

  function posterBusy(record, busy, label = '') {
    if (record.busy !== busy) state.busy = Math.max(0, state.busy + (busy ? 1 : -1));
    record.busy = busy;
    record.element.setAttribute('aria-busy', String(busy));
    updatePosterControls(record);
    if (label) message($('.poster-feedback', record.element), label);
  }

  function posterFeedback(record, text, error = false) {
    message($('.poster-feedback', record.element), text, error ? 'error' : 'success');
    if (text) announce(`${record.current.name}：${text}`);
  }

  async function selectFile(record, file) {
    if (!file) return;
    const version = ++record.selectionVersion;
    if (!IMAGE_TYPES.has(file.type)) { posterFeedback(record, '请选择 PNG、JPEG 或 WebP 图片。', true); return; }
    if (file.size > MAX_IMAGE_BYTES) { posterFeedback(record, '这张图片超过 8 MB，请压缩后重试。', true); return; }
    if (!file.size) { posterFeedback(record, '这张图片为空，请重新选择。', true); return; }
    const objectUrl = URL.createObjectURL(file);
    const image = new Image();
    image.src = objectUrl;
    try { await image.decode(); }
    catch {
      URL.revokeObjectURL(objectUrl);
      if (version === record.selectionVersion && state.authenticated) posterFeedback(record, '无法读取这张图片，请选择完整的静态图片。', true);
      return;
    }
    if (version !== record.selectionVersion || !state.authenticated) { URL.revokeObjectURL(objectUrl); return; }
    if (record.objectUrl) URL.revokeObjectURL(record.objectUrl);
    record.file = file;
    record.objectUrl = objectUrl;
    $('.reset-confirm', record.element).hidden = true;
    previewPoster(record, true);
    const dimensions = `${image.naturalWidth} × ${image.naturalHeight}`;
    posterFeedback(record, image.naturalWidth >= image.naturalHeight ? `已选择 ${dimensions} 横幅图片。全屏页面会裁切，请检查主体位置。` : `已选择 ${dimensions} 图片。确认效果后点击“发布海报”。`);
  }

  async function publishPoster(record) {
    if (!record.file || record.busy) return;
    const epoch = state.epoch;
    posterBusy(record, true, '正在发布海报…');
    try {
      const data = await api(`/api/admin/posters/${record.current.id}`, { method: 'POST', file: record.file });
      if (state.epoch !== epoch) return;
      record.current = normalisePoster(record.current, data.city);
      releaseDraft(record);
      $('.poster-file-input', record.element).value = '';
      updatePosterMetadata(record);
      previewPoster(record, false);
      posterFeedback(record, focusChanged(record) ? '海报已发布。当前预览的构图尚未保存。' : '海报已发布，投票页已使用新图。');
    } catch (error) { if (state.epoch === epoch) posterFeedback(record, error.message, true); }
    finally { if (state.epoch === epoch) posterBusy(record, false); }
  }

  async function saveFocus(record) {
    if (record.busy || record.file || !focusChanged(record)) return;
    const epoch = state.epoch;
    const x = Number($('.focus-x', record.element).value);
    const y = Number($('.focus-y', record.element).value);
    posterBusy(record, true, '正在保存构图…');
    try {
      const data = await api(`/api/admin/posters/${record.current.id}/position`, { method: 'POST', body: { x, y } });
      if (state.epoch !== epoch) return;
      record.current = normalisePoster(record.current, data.city);
      updatePosterMetadata(record);
      posterFeedback(record, '构图已保存。');
    } catch (error) { if (state.epoch === epoch) posterFeedback(record, error.message, true); }
    finally { if (state.epoch === epoch) posterBusy(record, false); }
  }

  async function resetPoster(record) {
    if (record.busy) return;
    const epoch = state.epoch;
    posterBusy(record, true, '正在恢复默认海报…');
    try {
      const data = await api(`/api/admin/posters/${record.current.id}/reset`, { method: 'POST' });
      if (state.epoch !== epoch) return;
      record.current = normalisePoster(record.current, data.city);
      releaseDraft(record);
      $('.poster-file-input', record.element).value = '';
      $('.focus-x', record.element).value = record.current.positionX;
      $('.focus-y', record.element).value = record.current.positionY;
      $('.reset-confirm', record.element).hidden = true;
      updatePosterMetadata(record);
      previewPoster(record, false);
      posterFeedback(record, '已恢复默认海报与构图。');
      $('.reset-poster', record.element).focus();
    } catch (error) { if (state.epoch === epoch) posterFeedback(record, error.message, true); }
    finally { if (state.epoch === epoch) posterBusy(record, false); }
  }

  function createPoster(city, data) {
    const element = $('#poster-template').content.firstElementChild.cloneNode(true);
    const current = normalisePoster(city, data);
    const record = { element, current, file: null, objectUrl: '', busy: false, selectionVersion: 0 };
    element.dataset.city = city.id;
    $('.poster-code', element).textContent = city.code;
    $('.poster-city', element).textContent = city.name;
    $('.poster-city', element).id = `poster-heading-${city.id}`;
    element.setAttribute('aria-labelledby', `poster-heading-${city.id}`);
    $('.poster-open', element).href = `/city-vote?city=${city.id}`;
    $('.poster-open', element).setAttribute('aria-label', `在新标签页打开${city.name}投票页`);
    $('.poster-file-input', element).setAttribute('aria-label', `选择${city.name}的新海报`);
    $('.focus-x', element).id = `focus-x-${city.id}`;
    $('.focus-y', element).id = `focus-y-${city.id}`;
    $('.focus-x', element).value = current.positionX;
    $('.focus-y', element).value = current.positionY;
    $('.focus-x', element).previousElementSibling.htmlFor = `focus-x-${city.id}`;
    $('.focus-y', element).previousElementSibling.htmlFor = `focus-y-${city.id}`;
    $('.focus-x-value', element).setAttribute('for', `focus-x-${city.id}`);
    $('.focus-y-value', element).setAttribute('for', `focus-y-${city.id}`);
    const input = $('.poster-file-input', element);
    $('.choose-poster', element).addEventListener('click', () => { input.value = ''; input.click(); });
    input.addEventListener('change', () => selectFile(record, input.files?.[0]));
    $('.publish-poster', element).addEventListener('click', () => publishPoster(record));
    $('.discard-poster', element).addEventListener('click', () => {
      releaseDraft(record); input.value = ''; previewPoster(record, false); posterFeedback(record, '已取消新图选择。');
    });
    for (const button of $$('.preview-switch button', element)) button.addEventListener('click', () => previewPoster(record, button.dataset.preview === 'draft'));
    for (const range of $$('.range-field input', element)) range.addEventListener('input', () => updateFocusPreview(record));
    $('.save-focus', element).addEventListener('click', () => saveFocus(record));
    $('.reset-poster', element).addEventListener('click', () => { $('.reset-confirm', element).hidden = false; $('.cancel-reset', element).focus(); });
    $('.cancel-reset', element).addEventListener('click', () => { $('.reset-confirm', element).hidden = true; $('.reset-poster', element).focus(); });
    $('.confirm-reset', element).addEventListener('click', () => resetPoster(record));
    $('.poster-image', element).addEventListener('error', () => { $('.poster-image-error', element).hidden = false; });
    $('.poster-image', element).addEventListener('load', () => { $('.poster-image-error', element).hidden = true; });
    updatePosterMetadata(record);
    previewPoster(record, false);
    return record;
  }

  async function loadPosters() {
    if (!state.authenticated || state.postersLoading || state.busy) return;
    const epoch = state.epoch;
    state.postersLoading = true;
    $('#refresh-posters').disabled = true;
    $('#refresh-posters').setAttribute('aria-busy', 'true');
    $('#posters-loading').hidden = state.postersLoaded;
    message($('#posters-error'), '');
    try {
      const data = await api('/api/admin/posters');
      if (state.epoch !== epoch) return;
      for (const city of CITIES) {
        const latest = data.cities?.find(item => item.id === city.id);
        if (!latest) throw new RequestError('海报数据不完整，请刷新后重试。');
        let record = state.posters.get(city.id);
        if (record) {
          const preserveFocus = focusChanged(record);
          record.current = normalisePoster(city, latest);
          if (!preserveFocus) {
            $('.focus-x', record.element).value = record.current.positionX;
            $('.focus-y', record.element).value = record.current.positionY;
          }
          updatePosterMetadata(record);
          previewPoster(record, record.element.dataset.preview === 'draft');
        } else {
          record = createPoster(city, latest);
          state.posters.set(city.id, record);
          $('#posters-grid').append(record.element);
        }
      }
      state.postersLoaded = true;
      announce('城市海报已更新。');
    } catch (error) { if (state.epoch === epoch) message($('#posters-error'), error.message); }
    finally {
      if (state.epoch === epoch) {
        state.postersLoading = false;
        $('#refresh-posters').disabled = false;
        $('#refresh-posters').setAttribute('aria-busy', 'false');
        $('#posters-loading').hidden = true;
      }
    }
  }
  $('#refresh-posters').addEventListener('click', loadPosters);
  window.addEventListener('beforeunload', event => {
    if (!state.feishuDirty && !state.feishuBusy && ![...state.posters.values()].some(record => record.file || focusChanged(record) || record.busy)) return;
    event.preventDefault();
    event.returnValue = '';
  });
  window.addEventListener('pagehide', event => {
    if (!event.persisted) for (const record of state.posters.values()) if (record.objectUrl) URL.revokeObjectURL(record.objectUrl);
  });
  loadSession();
})();
