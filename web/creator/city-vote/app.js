(() => {
  'use strict';
  const cities = ['shenzhen', 'zhuhai', 'guangzhou'];
  const names = { shenzhen: '深圳', zhuhai: '珠海', guangzhou: '广州' };
  const stage = document.querySelector('#stage');
  const panels = [...document.querySelectorAll('.city')];
  const tabs = [...document.querySelectorAll('[data-tab]')];
  const mobile = matchMedia('(max-width: 800px) and (orientation: portrait), (max-width: 600px)');
  const status = document.querySelector('#vote-status');
  const note = document.querySelector('#page-note');
  const connection = document.querySelector('#connection-message');
  const toast = document.querySelector('#vote-toast');
  const dialog = document.querySelector('#info-dialog');
  const confirmDialog = document.querySelector('#confirm-dialog');
  const confirmButton = document.querySelector('#confirm-submit');
  const cancelButton = document.querySelector('#confirm-cancel');
  const confirmError = document.querySelector('#confirm-error');
  const infoButton = document.querySelector('#open-info');
  const feedbackDialog = document.querySelector('#feedback-dialog');
  const feedbackButton = document.querySelector('#open-feedback');
  const feedbackForm = document.querySelector('#feedback-form');
  const feedbackContent = document.querySelector('#feedback-content');
  const feedbackError = document.querySelector('#feedback-error');
  const feedbackSubmit = document.querySelector('#feedback-submit');
  const queryCity = new URLSearchParams(location.search).get('city');
  let active = cities.includes(queryCity) ? queryCity : 'shenzhen';
  let pending = null;
  let chosen = null;
  let canChange = true;
  let ready = false;
  let initialCampaign = true;
  let campaignLoadPromise = null;
  let campaignIdentityReady = false;
  let loading = false;
  let submitting = false;
  let gesture = null;
  let toastTimer;
  let analyticsSent = false;
  let analyticsAttempts = 0;
  let analyticsEventId;
  let errorMessage = '';
  let feedbackSending = false;
  let feedbackSaved = false;
  let feedbackDraftStarted = false;
  let feedbackRequest = null;

  function announce(message) { status.textContent = message; }
  function notify(message, isError = false) {
    clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.toggle('is-error', isError);
    toast.hidden = false;
    toastTimer = setTimeout(() => { toast.hidden = true; }, isError ? 7000 : 4000);
  }
  async function api(path, options = {}) {
    const abort = new AbortController();
    const timeout = setTimeout(() => abort.abort(), 15000);
    try {
      const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...options, signal: abort.signal });
      let data;
      try { data = await response.json(); } catch { throw new Error('暂时连接不上，请稍后重试。'); }
      if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : '操作未完成，请稍后重试。');
      return data;
    } catch (error) {
      if (error.name === 'AbortError' || error instanceof TypeError) throw new Error('网络连接不稳定，请重试。');
      throw error;
    } finally { clearTimeout(timeout); }
  }

  function randomId() {
    // getRandomValues also works on HTTP; randomUUID requires a secure context.
    const bytes = new Uint8Array(16);
    if (window.crypto?.getRandomValues) crypto.getRandomValues(bytes);
    else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map(b => b.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  function render() {
    stage.classList.toggle('has-pending', Boolean(pending));
    stage.classList.toggle('is-submitting', submitting);
    stage.setAttribute('aria-busy', String(loading || submitting));
    for (const panel of panels) {
      const city = panel.dataset.city;
      const current = city === active;
      const isPending = city === pending;
      const isChosen = city === chosen;
      panel.classList.toggle('is-active', current);
      panel.classList.toggle('is-pending', isPending);
      panel.classList.toggle('is-chosen', isChosen);
      panel.inert = mobile.matches && !current;
      if (mobile.matches) {
        panel.setAttribute('role', 'tabpanel');
        panel.setAttribute('aria-labelledby', `tab-${city}`);
        panel.setAttribute('aria-hidden', String(!current));
      } else {
        panel.removeAttribute('role');
        panel.removeAttribute('aria-hidden');
        panel.setAttribute('aria-labelledby', `name-${city}`);
      }
      const button = panel.querySelector('[data-vote]');
      const undo = panel.querySelector('[data-undo]');
      const ipLocked = Boolean(chosen && !canChange);
      const label = submitting && isPending ? '正在提交' : isChosen ? `已投${names[city]}` : ipLocked ? '已完成投票' : chosen ? `改投${names[city]}` : `投${names[city]}一票`;
      button.querySelector('.button-label').textContent = label;
      button.setAttribute('aria-label', label);
      button.setAttribute('aria-pressed', String(isChosen));
      button.disabled = !ready || loading || submitting || ipLocked;
      button.classList.toggle('is-locked', ipLocked);
      button.classList.toggle('is-busy', submitting && isPending);
      undo.hidden = !isChosen || !canChange;
      undo.disabled = submitting;
      undo.textContent = '更改选择';
    }
    for (const tab of tabs) {
      const selected = tab.dataset.tab === active;
      tab.setAttribute('aria-selected', String(selected));
      tab.dataset.voted = String(tab.dataset.tab === chosen);
      tab.tabIndex = selected ? 0 : -1;
      tab.disabled = submitting;
    }
    note.textContent = chosen ? `${names[chosen]}，见。` : '一起练 · 一起见';
    connection.hidden = !errorMessage;
    connection.querySelector('span').textContent = errorMessage;
    document.querySelector('#retry-connection').disabled = loading;
    confirmDialog.setAttribute('aria-busy', String(submitting));
    confirmButton.disabled = submitting || !ready || Boolean(chosen && !canChange);
    cancelButton.disabled = submitting;
    confirmButton.classList.toggle('is-busy', submitting);
    if (pending) confirmButton.querySelector('span').textContent = submitting ? '正在保存…' : chosen && !canChange ? '已完成投票' : `确认${chosen ? '改' : ''}投给${names[pending]}`;
  }

  function acceptVoteState(data) {
    chosen = cities.includes(data.vote?.city) ? data.vote.city : null;
    canChange = data.canChange !== false;
  }

  function safeImagePath(value) {
    if (typeof value !== 'string') return null;
    const url = new URL(value, location.origin);
    return url.origin === location.origin && (url.pathname.startsWith('/city-vote/assets/') || url.pathname.startsWith('/api/media/')) ? url.href : null;
  }
  function applyPosters(config) {
    if (!Array.isArray(config)) throw new Error('海报配置暂时无法读取，请重试。');
    for (const item of config) {
      if (!cities.includes(item.id)) continue;
      const panel = panels.find(p => p.dataset.city === item.id);
      panel.dataset.defaultPoster = String(item.isDefault);
      const image = panel.querySelector('.city-photo');
      const full = safeImagePath(item.posterUrl);
      const small = safeImagePath(item.posterMobileUrl);
      if (full) {
        image.src = full;
        image.srcset = small ? `${small} 640w, ${full} 941w` : '';
      }
      const x = Number(item.positionX), y = Number(item.positionY);
      if (Number.isFinite(x) && x >= 0 && x <= 100) panel.style.setProperty('--poster-x', `${x}%`);
      if (Number.isFinite(y) && y >= 0 && y <= 100) panel.style.setProperty('--poster-y', `${y}%`);
      if (!item.isDefault) image.alt = `${names[item.id]}线下活动海报`;
    }
  }

  function recordPageView() {
    if (analyticsSent || analyticsAttempts >= 2 || document.visibilityState !== 'visible' || !ready) return;
    analyticsSent = true;
    analyticsAttempts += 1;
    // Analytics must never block voting, including older WebViews without randomUUID.
    Promise.resolve().then(() => {
      if (!analyticsEventId) analyticsEventId = randomId();
      return api('/api/analytics/page', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ page: 'vote', eventId: analyticsEventId }) });
    }).catch(() => { analyticsSent = false; });
  }
  function loadCampaign() {
    if (campaignLoadPromise) return campaignLoadPromise;
    if (submitting) return Promise.resolve();
    loading = true;
    errorMessage = '';
    render();
    campaignLoadPromise = (async () => {
      try {
        const data = await api('/api/campaign');
        applyPosters(data.cities);
        acceptVoteState(data);
        if (initialCampaign && chosen && !cities.includes(queryCity)) active = chosen;
        initialCampaign = false;
        ready = true;
        campaignIdentityReady = true;
      } catch (error) {
        ready = false;
        errorMessage = error.message;
      } finally {
        loading = false;
        render();
      }
      if (ready) recordPageView();
    })().finally(() => { campaignLoadPromise = null; });
    return campaignLoadPromise;
  }

  async function ensureCampaignIdentity() {
    if (campaignIdentityReady) return;
    // Wait for the same initial GET before any feedback POST can create a visitor.
    // Otherwise a late GET cookie could replace the feedback's idempotency owner.
    await loadCampaign();
    if (!campaignIdentityReady) throw new Error(errorMessage || '连接还未就绪，请稍后重试。');
  }

  function switchCity(city, focusTab = false) {
    if (!cities.includes(city) || submitting || confirmDialog.open || feedbackDialog.open) return;
    const previous = panels.find(panel => panel.dataset.city === active);
    const focusInside = previous.contains(document.activeElement);
    active = city;
    pending = null;
    render();
    if (mobile.matches && (focusTab || focusInside)) tabs.find(tab => tab.dataset.tab === city).focus({ preventScroll: true });
  }
  function openConfirmation(city) {
    if (!ready || loading || submitting || confirmDialog.open || feedbackDialog.open || (chosen && !canChange)) return;
    active = city;
    pending = city;
    confirmError.hidden = true;
    confirmError.textContent = '';
    document.querySelector('#confirm-eyebrow').textContent = chosen ? '下一次相见，改在' : '第一站，就在';
    document.querySelector('#confirm-title').textContent = names[city];
    document.querySelector('#confirm-english').textContent = city.toUpperCase();
    const panel = panels.find(p => p.dataset.city === city);
    const poster = panel.querySelector('.city-photo');
    const confirmPhoto = document.querySelector('#confirm-photo');
    confirmDialog.dataset.city = city;
    confirmDialog.dataset.defaultPoster = panel.dataset.defaultPoster;
    confirmPhoto.src = poster.currentSrc || poster.src;
    confirmPhoto.style.objectPosition = getComputedStyle(poster).objectPosition;
    render();
    confirmDialog.returnValue = '';
    confirmDialog.showModal();
    announce(`请确认是否将一票投给${names[city]}。`);
  }

  async function submitVote() {
    if (!ready || submitting || !pending || (chosen && !canChange)) return;
    const city = pending;
    submitting = true;
    confirmError.hidden = true;
    render();
    try {
      const result = await api('/api/campaign/vote', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ city }) });
      if (!cities.includes(result.vote?.city)) throw new Error('暂时无法确认结果，请重试或刷新后查看。');
      acceptVoteState(result);
      const panel = panels.find(p => p.dataset.city === chosen);
      panel.classList.remove('is-confirmed');
      requestAnimationFrame(() => panel.classList.add('is-confirmed'));
      confirmDialog.close('saved');
      notify(canChange ? `${names[chosen]}，收到你的一票。` : `已投给${names[chosen]}。`);
      announce(`投票已保存，当前投给${names[chosen]}。`);
      // Keep other same-origin admin tabs current without persisting identifiers.
      if ('BroadcastChannel' in window) {
        try {
          const updates = new BroadcastChannel('lean-campaign-events');
          updates.postMessage({ type: 'vote-updated' });
          updates.close();
        } catch { /* Admin polling remains available when channels are restricted. */ }
      }
    } catch (error) {
      // Another browser may have claimed this IP while this dialog was open.
      // Reconcile with the API; never show a locally invented successful vote.
      try { acceptVoteState(await api('/api/campaign')); } catch { /* Keep the original error and retry affordance. */ }
      const message = chosen && !canChange ? `已完成投票：${names[chosen]}。` : error.message;
      confirmError.textContent = message;
      confirmError.hidden = false;
      announce(message);
    } finally {
      submitting = false;
      render();
    }
  }

  function updateFeedbackCount() {
    document.querySelector('#feedback-count').textContent = `${[...feedbackContent.value].length} / 1000`;
  }

  function showFeedback() {
    if (feedbackDialog.open || confirmDialog.open || dialog.open) return;
    gesture = null;
    if (feedbackSaved) {
      feedbackSaved = false;
      feedbackDraftStarted = false;
      document.querySelector('#feedback-compose').hidden = false;
      document.querySelector('#feedback-success').hidden = true;
      feedbackDialog.setAttribute('aria-labelledby', 'feedback-title');
    }
    if (!feedbackDraftStarted) {
      feedbackForm.querySelector(`input[name="city"][value="${active}"]`).checked = true;
      feedbackDraftStarted = true;
    }
    const poster = panels.find(p => p.dataset.city === active).querySelector('.city-photo');
    const feedbackPhoto = document.querySelector('#feedback-photo');
    feedbackPhoto.src = poster.currentSrc || poster.src;
    feedbackPhoto.style.objectPosition = getComputedStyle(poster).objectPosition;
    feedbackDialog.showModal();
    // Keep the full composition visible on mobile before opening its keyboard.
    document.querySelector('#feedback-title').focus({ preventScroll: true });
  }

  async function submitFeedback(event) {
    event.preventDefault();
    if (feedbackSending || feedbackSaved) return;
    const payload = {
      content: feedbackContent.value.trim(),
      category: feedbackForm.querySelector('input[name="category"]:checked').value,
      city: feedbackForm.querySelector('input[name="city"]:checked').value || null
    };
    const length = [...payload.content].length;
    const nonWhitespaceLength = [...payload.content.replace(/\s/g, '')].length;
    if (nonWhitespaceLength < 5 || length > 1000) {
      feedbackError.textContent = nonWhitespaceLength < 5 ? '再多聊一点吧，至少写 5 个字。' : '想法很丰富，先精简到 1000 字以内吧。';
      feedbackError.hidden = false;
      feedbackContent.setAttribute('aria-invalid', 'true');
      feedbackContent.focus();
      return;
    }
    const fingerprint = JSON.stringify(payload);
    // A retry after a lost response uses the same key, including close/reopen.
    if (feedbackRequest?.fingerprint !== fingerprint) feedbackRequest = { fingerprint, id: randomId() };
    feedbackSending = true;
    feedbackError.hidden = true;
    feedbackContent.removeAttribute('aria-invalid');
    feedbackForm.setAttribute('aria-busy', 'true');
    for (const control of feedbackForm.querySelectorAll('input,textarea,button')) control.disabled = true;
    feedbackSubmit.classList.add('is-busy');
    feedbackSubmit.querySelector('span').textContent = '正在送出…';
    try {
      await ensureCampaignIdentity();
      const result = await api('/api/campaign/feedback', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, requestId: feedbackRequest.id })
      });
      if (result.ok !== true) throw new Error('暂时无法确认是否收到，请再试一次。');
      feedbackSaved = true;
      feedbackForm.reset();
      updateFeedbackCount();
      feedbackRequest = null;
      document.querySelector('#feedback-compose').hidden = true;
      document.querySelector('#feedback-success').hidden = false;
      feedbackDialog.setAttribute('aria-labelledby', 'feedback-success-title');
      if (feedbackDialog.open) {
        feedbackDialog.scrollTop = 0;
        document.querySelector('#feedback-success-title').focus({ preventScroll: true });
      } else notify('你的想法，收到了。');
      announce('想法已收到，谢谢你一起参与。');
      if ('BroadcastChannel' in window) {
        try {
          const updates = new BroadcastChannel('lean-campaign-events');
          updates.postMessage({ type: 'feedback-created' });
          updates.close();
        } catch { /* Admin polling remains available. */ }
      }
    } catch (error) {
      feedbackError.textContent = error.message;
      feedbackError.hidden = false;
      if (!feedbackDialog.open) notify('想法暂未确认送达，内容已保留在意见箱。', true);
    } finally {
      feedbackSending = false;
      feedbackForm.setAttribute('aria-busy', 'false');
      for (const control of feedbackForm.querySelectorAll('input,textarea,button')) control.disabled = false;
      feedbackSubmit.classList.remove('is-busy');
      feedbackSubmit.querySelector('span').textContent = feedbackError.hidden ? '送出想法' : '重新送出';
    }
  }

  for (const panel of panels) {
    const city = panel.dataset.city;
    panel.querySelector('[data-vote]').addEventListener('click', () => {
      if (!ready || loading || submitting) return;
      if (chosen === city) { notify(`你的一票已投给${names[city]}。`); return; }
      openConfirmation(city);
    });
    panel.querySelector('[data-undo]').addEventListener('click', () => {
      if (submitting || !canChange) return;
      const next = cities[(cities.indexOf(city) + 1) % cities.length];
      switchCity(next, mobile.matches);
      if (!mobile.matches) panels.find(p => p.dataset.city === next).querySelector('[data-vote]').focus({ preventScroll: true });
      announce('选择另一个城市并确认，即可更改投票。');
    });
    panel.addEventListener('animationend', () => panel.classList.remove('is-confirmed'));
  }
  tabs.forEach(tab => {
    tab.addEventListener('click', () => switchCity(tab.dataset.tab));
    tab.addEventListener('keydown', event => {
      const index = cities.indexOf(active);
      const next = event.key === 'ArrowRight' ? (index + 1) % 3 : event.key === 'ArrowLeft' ? (index + 2) % 3 : event.key === 'Home' ? 0 : event.key === 'End' ? 2 : null;
      if (next === null) return;
      event.preventDefault();
      switchCity(cities[next], true);
    });
  });
  stage.addEventListener('pointerdown', event => {
    if (!mobile.matches || submitting || confirmDialog.open || feedbackDialog.open || event.button !== 0 || event.target.closest('button,a')) return;
    gesture = { x: event.clientX, y: event.clientY, id: event.pointerId };
  });
  window.addEventListener('pointerup', event => {
    if (!gesture || gesture.id !== event.pointerId) return;
    const dx = event.clientX - gesture.x, dy = event.clientY - gesture.y;
    gesture = null;
    if (Math.abs(dx) < 48 || Math.abs(dx) < Math.abs(dy) * 1.35) return;
    switchCity(cities[(cities.indexOf(active) + (dx < 0 ? 1 : 2)) % 3]);
  });
  window.addEventListener('pointercancel', () => { gesture = null; });
  window.addEventListener('blur', () => { gesture = null; });
  mobile.addEventListener('change', () => {
    gesture = null;
    const focus = document.activeElement;
    render();
    if (mobile.matches && focus.closest('.city[inert]')) tabs.find(t => t.dataset.tab === active).focus({ preventScroll: true });
    else if (!mobile.matches && focus.closest('.city-switcher')) panels.find(p => p.dataset.city === active).querySelector('[data-vote]').focus({ preventScroll: true });
  });
  infoButton.addEventListener('click', () => dialog.showModal());
  document.querySelector('#close-info').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const r = dialog.getBoundingClientRect();
    if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
  });
  dialog.addEventListener('close', () => infoButton.focus({ preventScroll: true }));
  feedbackButton.addEventListener('click', showFeedback);
  feedbackForm.addEventListener('submit', submitFeedback);
  feedbackForm.addEventListener('input', () => {
    feedbackDraftStarted = true;
    updateFeedbackCount();
    feedbackContent.removeAttribute('aria-invalid');
    feedbackError.hidden = true;
    feedbackSubmit.querySelector('span').textContent = '送出想法';
  });
  document.querySelector('#close-feedback').addEventListener('click', () => feedbackDialog.close());
  document.querySelector('#feedback-done').addEventListener('click', () => feedbackDialog.close());
  feedbackDialog.addEventListener('click', event => {
    if (event.target !== feedbackDialog) return;
    const r = feedbackDialog.getBoundingClientRect();
    if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) feedbackDialog.close();
  });
  feedbackDialog.addEventListener('close', () => feedbackButton.focus({ preventScroll: true }));
  confirmButton.addEventListener('click', submitVote);
  cancelButton.addEventListener('click', () => { if (!submitting) confirmDialog.close('cancel'); });
  confirmDialog.addEventListener('cancel', event => { if (submitting) event.preventDefault(); });
  confirmDialog.addEventListener('click', event => {
    if (submitting || event.target !== confirmDialog) return;
    const r = confirmDialog.getBoundingClientRect();
    if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) confirmDialog.close('cancel');
  });
  confirmDialog.addEventListener('close', () => {
    const previousCity = pending;
    pending = null;
    render();
    const button = panels.find(p => p.dataset.city === previousCity)?.querySelector('[data-vote]');
    if (button && !button.disabled) button.focus({ preventScroll: true });
    else if (mobile.matches) tabs.find(t => t.dataset.tab === active)?.focus({ preventScroll: true });
    else infoButton.focus({ preventScroll: true });
    if (confirmDialog.returnValue !== 'saved') announce(chosen ? `已返回选择，仍保留${names[chosen]}。` : '已返回选择，尚未投票。');
    else window.dispatchEvent(new Event('lean:vote-saved'));
  });
  document.querySelector('#retry-connection').addEventListener('click', loadCampaign);
  // Both public comments and private feedback share the campaign visitor cookie.
  // Waiting on the same GET prevents concurrent responses replacing its owner.
  window.LeanCampaignSession = Object.freeze({ ensureIdentity: ensureCampaignIdentity, randomId });
  document.addEventListener('visibilitychange', recordPageView);
  window.addEventListener('pageshow', event => { if (event.persisted) loadCampaign(); });
  render();
  requestAnimationFrame(() => stage.classList.add('is-ready'));
  loadCampaign();
})();
