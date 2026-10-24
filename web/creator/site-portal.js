/* LEAN SCHOOL public portal. Employee accounts remain in the original Yudao admin. */
(() => {
  'use strict';
  const SESSION_KEY = 'leanclub.member.session.v1';
  let token = '', identity = 0, profile = null, capabilities = null;
  const memberRequests = new Set();
  try { token = sessionStorage.getItem(SESSION_KEY) || ''; } catch (_) { /* Private storage can be unavailable. */ }
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  function setToken(value) {
    token = value || ''; identity += 1; profile = null;
    memberRequests.forEach(controller => controller.abort()); memberRequests.clear();
    try { token ? sessionStorage.setItem(SESSION_KEY, token) : sessionStorage.removeItem(SESSION_KEY); } catch (_) { /* Keep in memory for this page. */ }
    document.querySelectorAll('[data-member-label]').forEach(el => { el.textContent = token ? '我的' : '登录 / 注册'; });
    window.dispatchEvent(new CustomEvent('leanclub:identity'));
  }
  async function api(path, options = {}) {
    if (!/^\/leanclub\//.test(path)) throw new Error('请求地址无效。');
    if (options.auth && !token) { const error = new Error('请先登录，再查看你的报名。'); error.code = 401; throw error; }
    const controller = new AbortController();
    if (options.auth) memberRequests.add(controller);
    const timeout = setTimeout(() => controller.abort(), 12000);
    const requestIdentity = identity;
    const headers = { Accept: 'application/json' };
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';
    if (options.auth && token) headers.Authorization = `Bearer ${token}`;
    try {
      const response = await fetch('/app-api' + path, {
        method: options.method || (options.body !== undefined ? 'POST' : 'GET'),
        headers, body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
        credentials: 'omit', cache: 'no-store', signal: controller.signal
      });
      let result;
      try { result = await response.json(); } catch (_) { throw new Error('服务暂时无法连接，请稍后再试。'); }
      if (options.auth && requestIdentity !== identity) throw new Error('账户状态已改变，请重新操作。');
      if (response.status === 401 || result.code === 401) {
        if (options.auth) {
          setToken('');
          if (memberDialog.open) renderLogin('登录已失效，请重新验证手机号。');
        }
        const error = new Error('登录已失效，请重新验证手机号。'); error.code = 401; throw error;
      }
      if (!response.ok || result.code !== 0) {
        const error = new Error(typeof result.msg === 'string' && result.msg.length < 240 ? result.msg : '操作未完成，请稍后重试。');
        error.code = result.code; error.status = response.status; throw error;
      }
      return result.data;
    } catch (error) {
      if (options.auth && requestIdentity !== identity) { const changed = new Error('账户状态已改变，请重新操作。'); changed.code = 'IDENTITY_CHANGED'; throw changed; }
      if (error.name === 'AbortError') throw new Error('连接超时，请检查网络后重试。');
      throw error;
    } finally { clearTimeout(timeout); memberRequests.delete(controller); }
  }
  const ready = api('/leanclub/site/capabilities').then(data => {
    capabilities = data || {}; return capabilities;
  }).catch(() => {
    capabilities = { memberLoginEnabled: false, memberLoginReason: '账户服务暂未连接，请稍后再试。' };
    return capabilities;
  });
  function safeHref(value, media = false) {
    if (typeof value !== 'string' || !value.trim() || value.length > 2000 || /[\u0000-\u001f\\]/.test(value)) return '';
    try {
      const url = new URL(value, location.origin);
      if (url.username || url.password) return '';
      if (media) {
        if (url.origin === location.origin && /^\/(?:images\/|api\/media\/|app-api\/infra\/file\/|city-vote\/assets\/|lean-[a-z0-9-]+\.(?:jpg|png|webp)$)/i.test(url.pathname)) return url.href;
        if (url.protocol === 'https:') return url.href;
        return '';
      }
      if (url.origin === location.origin) {
        if (/^\/(?:|schools\/?|join\/?|design\/?|events\/?|ticket\/?|city-vote\/?|index\.html|lean-creator-home\.html)$/.test(url.pathname)) return url.pathname + url.search + url.hash;
        return '';
      }
      if (url.protocol === 'https:') return url.href;
      if (url.protocol === 'mailto:' && /^[^\s?]+@[^\s?]+$/.test(url.pathname)) return url.href;
    } catch (_) { /* Invalid published field: retain the original visual. */ }
    return '';
  }
  const memberDialog = node('dialog', 'lc-member-dialog');
  memberDialog.setAttribute('aria-labelledby', 'lc-member-title');
  memberDialog.innerHTML = `<div class="lc-member-art" aria-hidden="true"><span>LEAN SCHOOL</span><p>一起向上。<br>从这里开始。</p><small>LEAN BODY. STRONG MIND.</small></div><div class="lc-member-body"><button type="button" class="lc-close" aria-label="关闭账户">×</button><p class="lc-kicker">YOUR LEAN SCHOOL</p><h2 id="lc-member-title">加入薄肌学院</h2><div id="lc-member-content"></div></div>`;
  document.body.append(memberDialog);
  memberDialog.querySelector('.lc-close').addEventListener('click', () => memberDialog.close());
  memberDialog.addEventListener('click', event => { if (event.target === memberDialog) memberDialog.close(); });
  memberDialog.addEventListener('close', () => { loginVersion += 1; pendingAction = null; });
  const content = memberDialog.querySelector('#lc-member-content');
  let loginVersion = 0, pendingAction = null, smsUntil = 0;
  function message(target, value, error = false) {
    target.textContent = value; target.classList.toggle('is-error', error);
  }
  async function showMember(afterLogin) {
    if (typeof afterLogin === 'function') pendingAction = afterLogin;
    if (!memberDialog.open) memberDialog.showModal();
    if (!token) { renderLogin(); return; }
    const current = ++loginVersion;
    content.replaceChildren(node('p', 'lc-muted', '正在读取你的资料…'));
    try {
      const data = await api('/leanclub/profile/get', { auth: true });
      if (current !== loginVersion || !memberDialog.open) return;
      profile = data;
      if (pendingAction) {
        const action = pendingAction; pendingAction = null;
        memberDialog.addEventListener('close', action, { once: true });
        memberDialog.close(); return;
      }
      renderProfile();
    } catch (error) {
      if (current !== loginVersion || !memberDialog.open) return;
      if (!token) { renderLogin(error.message); return; }
      const status = node('p', 'lc-status is-error', error.message);
      const retry = node('button', 'lc-primary', '重新加载'); retry.type = 'button';
      retry.addEventListener('click', () => showMember()); content.replaceChildren(status, retry);
    }
  }
  function renderLogin(initialError = '') {
    const current = ++loginVersion;
    memberDialog.querySelector('h2').textContent = '加入薄肌学院';
    content.innerHTML = `<p class="lc-intro">保存你的学校，找到同频的人。</p><form id="lc-login-form"><label class="lc-field">手机号<input name="mobile" type="tel" inputmode="tel" autocomplete="tel" maxlength="11" pattern="1[3-9][0-9]{9}" placeholder="输入你的手机号" required></label><label class="lc-field">验证码<div class="lc-code-field"><input name="code" type="text" inputmode="numeric" autocomplete="one-time-code" minlength="4" maxlength="6" pattern="[0-9]{4,6}" placeholder="短信验证码" required><button class="lc-send-code" type="button" disabled>获取验证码</button></div></label><label class="lc-consent"><input name="consent" type="checkbox" required><span>我同意使用本人手机号进行账户验证。<button type="button" class="lc-text-button" data-account-info>账户与隐私说明</button></span></label><details class="lc-account-info"><summary>账户与隐私说明</summary><p>手机号用于验证和找回账户。学校由你自愿选择，不代表学籍认证；学校选择不会公开你的手机号。你可以在本机退出登录。活动订单与必要的报名资料按对应活动规则处理。</p></details><p class="lc-status" role="status" aria-live="polite"></p><button class="lc-primary lc-full" type="submit" disabled>登录 / 注册 <span aria-hidden="true">↗</span></button><p class="lc-footnote">首次验证后自动创建账户，无需单独注册。</p></form>`;
    const form = content.querySelector('form'), send = form.querySelector('.lc-send-code'), submit = form.querySelector('[type=submit]'), status = form.querySelector('.lc-status');
    let enabled = false, sending = false, submitting = false;
    const tick = () => {
      if (current !== loginVersion || !memberDialog.open) return;
      const seconds = Math.max(0, Math.ceil((smsUntil - Date.now()) / 1000));
      send.disabled = !enabled || sending || seconds > 0;
      send.textContent = sending ? '正在发送…' : seconds ? `${seconds}秒后重发` : '获取验证码';
      if (seconds) setTimeout(tick, 1000);
    };
    form.querySelector('[data-account-info]').addEventListener('click', () => { const info = form.querySelector('details'); info.open = !info.open; });
    const retryConnection = node('button', 'lc-text-button lc-retry-connection', '重新检查连接'); retryConnection.type = 'button'; retryConnection.hidden = true;
    status.after(retryConnection);
    const applyCapabilities = config => {
      if (current !== loginVersion) return;
      enabled = config.memberLoginEnabled === true; submit.disabled = !enabled; tick();
      retryConnection.hidden = enabled;
      message(status, initialError || (enabled ? '' : config.memberLoginReason || '手机验证暂未开通，你仍可浏览学校和活动。'), !!initialError);
    };
    if (capabilities) applyCapabilities(capabilities); else ready.then(applyCapabilities);
    retryConnection.addEventListener('click', async () => {
      retryConnection.disabled = true;
      try { const config = await api('/leanclub/site/capabilities'); capabilities = config; applyCapabilities(config); }
      catch (error) { if (current === loginVersion) message(status, error.message, true); }
      finally { retryConnection.disabled = false; }
    });
    send.addEventListener('click', async () => {
      if (!enabled || sending || Date.now() < smsUntil) return;
      if (!form.elements.mobile.reportValidity()) return;
      if (!form.elements.consent.checked) { form.elements.consent.reportValidity(); return; }
      sending = true; tick(); message(status, '');
      try {
        await api('/leanclub/auth/send-sms-code', { body: { mobile: form.elements.mobile.value.trim(), scene: 1 } });
        smsUntil = Date.now() + 60000;
        if (current === loginVersion) { message(status, '验证码已发送，请查看手机短信。'); form.elements.code.focus(); }
      } catch (error) { if (current === loginVersion) message(status, error.message, true); }
      finally { sending = false; tick(); }
    });
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (!enabled || submitting || !form.reportValidity()) return;
      submitting = true; submit.disabled = true; message(status, '正在验证…');
      try {
        const result = await api('/leanclub/auth/sms-login', { body: { mobile: form.elements.mobile.value.trim(), code: form.elements.code.value.trim() } });
        if (current !== loginVersion) return;
        if (!result || typeof result.accessToken !== 'string') throw new Error('未能建立账户会话，请重试。');
        setToken(result.accessToken); showMember();
      } catch (error) { if (current === loginVersion) message(status, error.message, true); }
      finally { submitting = false; if (current === loginVersion) submit.disabled = !enabled; }
    });
  }
  function renderProfile() {
    memberDialog.querySelector('h2').textContent = profile.nickname || '我的薄肌';
    content.replaceChildren();
    const mobile = node('p', 'lc-intro', profile.mobileMasked || '已验证账户');
    const school = node('button', 'lc-school-choice'); school.type = 'button';
    school.append(node('small', '', '我的学校'), node('strong', '', profile.schoolName || '选择你的学校'), node('span', '', '更改 ↗'));
    const status = node('p', 'lc-status'); status.setAttribute('role', 'status');
    school.addEventListener('click', () => chooseSchool('', async selected => {
      const original = profile; school.disabled = true;
      try {
        profile = await api('/leanclub/profile/update', { auth: true, method: 'PUT', body: { schoolId: selected?.id ?? null, expectedVersion: original.version } });
        if (memberDialog.open && token) { renderProfile(); content.querySelector('.lc-status').textContent = '学校已保存。'; }
      } catch (error) { message(status, error.message, true); }
      finally { school.disabled = false; }
    }, true));
    const hint = node('p', 'lc-footnote', '学校可暂不选择；自填学校不代表学籍认证。');
    const links = node('div', 'lc-account-links');
    for (const [title, note, href] of [['报名与电子票', '同一账号，在不同设备查看', '/events?mine=1'], ['本校俱乐部', '找到同频的人', profile.schoolId ? '/schools?schoolId=' + encodeURIComponent(profile.schoolId) : '/schools'], ['发起俱乐部', '提交申请，平台审核后展示', '/join']]) {
      const link = node('a', 'lc-account-link'); link.href = href;
      const copy = node('span'); copy.append(node('strong', '', title), node('small', '', note)); link.append(copy, node('span', '', '↗')); links.append(link);
    }
    const applications = node('section', 'lc-my-applications'); applications.append(node('h3', '', '我的俱乐部申请'));
    const appState = node('p', 'lc-muted', '正在读取…'); applications.append(appState);
    const currentIdentity = identity;
    api('/leanclub/club/mine', { auth: true }).then(list => {
      if (currentIdentity !== identity || !applications.isConnected) return;
      appState.remove();
      if (!Array.isArray(list) || !list.length) { applications.append(node('p', 'lc-muted', '还没有提交申请。')); return; }
      list.forEach(item => {
        const row = node('div', 'lc-application-row');
        const labels = { pending: '审核中', approved: '已通过', rejected: '未通过', closed: '已下架', withdrawn: '已撤回', 0: '审核中', 1: '已通过', 2: '未通过' };
        row.append(node('strong', '', item.name), node('span', '', labels[item.status] || '处理中'));
        if (item.reviewNote) row.append(node('p', '', item.reviewNote)); applications.append(row);
      });
    }).catch(error => { if (currentIdentity === identity && applications.isConnected) message(appState, error.message, true); });
    const logout = node('button', 'lc-logout', '退出登录'); logout.type = 'button';
    logout.addEventListener('click', async () => {
      logout.disabled = true;
      try { await api('/leanclub/auth/logout', { method: 'POST', auth: true }); setToken(''); pendingAction = null; renderLogin(); }
      catch (error) { if (!token) renderLogin(error.message); else { message(status, '退出未完成：' + error.message, true); logout.disabled = false; } }
    });
    content.append(mobile, school, hint, status, links, applications, logout);
  }
  const schoolDialog = node('dialog', 'lc-school-dialog'); schoolDialog.setAttribute('aria-labelledby', 'lc-school-title');
  schoolDialog.innerHTML = `<button type="button" class="lc-close" aria-label="关闭学校选择">×</button><p class="lc-kicker">FIND YOUR CAMPUS</p><h2 id="lc-school-title">找到你的学校</h2><p class="lc-muted">学校目录与俱乐部独立，选校不代表加入组织。</p><form class="lc-school-search"><label class="lc-sr" for="lc-school-query">学校名称或简称</label><input id="lc-school-query" type="search" placeholder="搜索学校名称或简称" autocomplete="off" maxlength="80"><button class="lc-primary" type="submit">搜索</button></form><div class="lc-school-results" aria-live="polite"></div><button class="lc-more" type="button" hidden>加载更多</button><button class="lc-clear-school" type="button" hidden>暂不选择学校</button>`;
  document.body.append(schoolDialog);
  let schoolCallback = null, searchVersion = 0, page = 1, schoolTimer = null;
  schoolDialog.querySelector('.lc-close').addEventListener('click', () => schoolDialog.close());
  schoolDialog.addEventListener('close', () => { searchVersion += 1; clearTimeout(schoolTimer); });
  schoolDialog.addEventListener('click', event => { if (event.target === schoolDialog) schoolDialog.close(); });
  async function searchSchools(append = false) {
    const version = ++searchVersion, results = schoolDialog.querySelector('.lc-school-results'), more = schoolDialog.querySelector('.lc-more');
    const query = schoolDialog.querySelector('input').value.trim();
    if (!append) { page = 1; results.replaceChildren(node('p', 'lc-muted', '正在搜索…')); }
    more.hidden = true;
    try {
      const data = await (window.LeanSchoolDirectory
        ? window.LeanSchoolDirectory.list({ keyword: query, pageNo: page, pageSize: 20 })
        : api('/leanclub/school/list?' + new URLSearchParams({ keyword: query, pageNo: String(page), pageSize: '20' })));
      if (version !== searchVersion || !schoolDialog.open) return;
      if (!append) results.replaceChildren();
      const list = Array.isArray(data?.list) ? data.list : [];
      if (!list.length && !append) results.append(node('p', 'lc-empty', '暂未找到这所学校。试试全称，或稍后再选。'));
      list.forEach(item => {
        const button = node('button', 'lc-school-result'); button.type = 'button';
        const copy = node('span'); copy.append(node('strong', '', item.name), node('small', '', [item.province, item.city, item.level].filter(Boolean).join(' · ')));
        button.append(copy, node('span', Number(item.clubCount) > 0 ? 'lc-club-badge' : 'lc-campus-badge', Number(item.clubCount) > 0 ? '已开通俱乐部' : '学校目录'));
        button.addEventListener('click', () => { const callback = schoolCallback; schoolDialog.close(); if (callback) callback(item); else location.assign('/schools?schoolId=' + encodeURIComponent(item.id)); });
        results.append(button);
      });
      more.hidden = page * 20 >= Number(data?.total || 0);
    } catch (error) {
      if (version !== searchVersion || !schoolDialog.open) return;
      if (!append) results.replaceChildren();
      const alert = node('p', 'lc-status is-error', error.message); alert.setAttribute('role', 'alert'); results.append(alert);
    }
  }
  function chooseSchool(query = '', callback = null, allowClear = false) {
    schoolCallback = callback; schoolDialog.querySelector('input').value = query;
    schoolDialog.querySelector('.lc-clear-school').hidden = !allowClear;
    if (!schoolDialog.open) schoolDialog.showModal();
    searchSchools(); schoolDialog.querySelector('input').focus();
  }
  schoolDialog.querySelector('form').addEventListener('submit', event => { event.preventDefault(); clearTimeout(schoolTimer); searchSchools(); });
  schoolDialog.querySelector('input').addEventListener('input', () => { clearTimeout(schoolTimer); searchVersion += 1; schoolTimer = setTimeout(() => searchSchools(), 300); });
  schoolDialog.querySelector('.lc-more').addEventListener('click', () => { page += 1; searchSchools(true); });
  schoolDialog.querySelector('.lc-clear-school').addEventListener('click', () => { const callback = schoolCallback; schoolDialog.close(); callback?.(null); });
  document.addEventListener('click', event => {
    const anchor = event.target.closest('a[href]');
    const accountLink = anchor && anchor.origin === location.origin && anchor.hash === '#account';
    const trigger = event.target.closest('[data-member-open]') || accountLink;
    if (trigger) { event.preventDefault(); document.querySelector('.masthead')?.classList.remove('menu-open'); document.querySelector('.menu')?.setAttribute('aria-expanded', 'false'); showMember(); }
  });
  document.querySelectorAll('[data-member-label]').forEach(el => { el.textContent = token ? '我的' : '登录 / 注册'; });
  if (location.hash === '#account') showMember();
  window.addEventListener('hashchange', () => { if (location.hash === '#account') showMember(); });
  function saveSchool(school) {
    showMember(async () => {
      memberDialog.showModal(); content.replaceChildren(node('p', 'lc-muted', '正在保存学校…'));
      const current = ++loginVersion;
      try {
        const latest = await api('/leanclub/profile/get', { auth: true });
        const saved = await api('/leanclub/profile/update', { auth: true, method: 'PUT', body: { schoolId: school.id, expectedVersion: latest.version } });
        if (current !== loginVersion || !memberDialog.open) return;
        profile = saved; renderProfile(); content.querySelector('.lc-status').textContent = '学校已保存。';
      } catch (error) {
        if (current !== loginVersion || !memberDialog.open) return;
        const retry = node('button', 'lc-primary', '返回我的资料'); retry.addEventListener('click', () => showMember());
        content.replaceChildren(node('p', 'lc-status is-error', error.message), retry);
      }
    });
  }
  function syncSession() {
    try { const stored = sessionStorage.getItem(SESSION_KEY) || ''; if (stored !== token) { setToken(stored); if (memberDialog.open) showMember(); } } catch (_) { /* Memory-only sessions remain usable. */ }
  }
  window.addEventListener('pageshow', syncSession);
  window.addEventListener('storage', event => { if (event.key === SESSION_KEY || event.key === null) syncSession(); });
  window.LeanClubPortal = { api, ready, node, safeHref, chooseSchool, showMember, saveSchool, requireMember: callback => showMember(callback), isSignedIn: () => !!token, identityVersion: () => identity };
})();
