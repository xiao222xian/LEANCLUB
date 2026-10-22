(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const state = { authenticated: false, csrf: '', busy: false, code: '', preview: null, requestId: '', epoch: 0, stream: null, scanTimer: null, scanning: false, cameraEpoch: 0 };
  const date = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
  function fmt(value) { const item = new Date(value); return value && Number.isFinite(item.getTime()) ? date.format(item) : ''; }
  function notice(id, text) { $(id).textContent = text || ''; $(id).hidden = !text; }
  function uuid() { const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128; const hex = [...bytes].map(x => x.toString(16).padStart(2, '0')).join(''); return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`; }
  function parseCode(value) {
    const text = String(value || '').trim();
    if (!text || text.length > 2048) throw new Error('请输入有效的入场票号或票码链接。');
    if (/^(?:https?:|\/)/i.test(text)) { let link; try { link = new URL(text, location.origin); } catch { throw new Error('票码链接无效。'); } if (link.origin !== location.origin || !['/check-in', '/check-in/'].includes(link.pathname)) throw new Error('请使用本站的入场码链接。'); const token = new URLSearchParams(link.hash.slice(1)).get('ticket'); if (!token) throw new Error('票码链接缺少入场凭证。'); return token; }
    return text;
  }
  async function api(path, body) {
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const options = { credentials: 'same-origin', cache: 'no-store', signal: controller.signal, headers: { Accept: 'application/json' } };
      if (body) { options.method = 'POST'; options.body = JSON.stringify(body); options.headers['Content-Type'] = 'application/json'; options.headers['X-CSRF-Token'] = state.csrf; }
      const response = await fetch(path, options); let data;
      try { data = await response.json(); } catch { throw new Error('核销服务暂时无法响应，请重试。'); }
      if (!response.ok) { if (response.status === 401 && state.authenticated) { showLogin(); notice('#global-error', '登录已过期，请重新登录。'); } throw new Error(typeof data.error === 'string' ? data.error : '请求未完成，请重试。'); }
      return data;
    } catch (error) { if (error.name === 'AbortError') throw new Error('连接超时，请重新核对。重复核对不会重复入场。'); if (error instanceof TypeError) throw new Error('网络暂时不可用，请检查连接后重试。'); throw error; }
    finally { clearTimeout(timer); }
  }
  function setBusy(value) { state.busy = value; for (const id of ['preview-ticket', 'confirm-checkin', 'next-ticket', 'logout', 'ticket-code', 'open-camera']) $(`#${id}`).disabled = value; $('#confirm-checkin').textContent = value ? '正在确认…' : '确认核销 · 允许入场'; }
  function clearResult() { state.preview = null; $('#ticket-result').hidden = true; $('#ready-state').hidden = false; $('#confirm-checkin').hidden = true; $('#confirm-hint').hidden = true; for (const id of ['guest-name', 'guest-ticket', 'guest-phone', 'guest-event', 'guest-time', 'guest-venue', 'checked-in-time', 'result-title', 'result-reason']) $(`#${id}`).textContent = ''; notice('#ticket-error', ''); }
  function showLogin() { stopCamera(); state.authenticated = false; state.csrf = ''; state.epoch++; setBusy(false); clearResult(); $('#workspace').hidden = true; $('#login-form').hidden = false; $('#password').value = ''; $('#session-loading').hidden = true; $('#ticket-loading').hidden = true; }
  async function enter(session) { state.authenticated = true; state.csrf = session.csrfToken || ''; state.epoch++; $('#staff-name').textContent = `${session.username || 'STAFF'} / 现场核销`; $('#login-form').hidden = true; $('#workspace').hidden = false; $('#password').value = ''; $('#session-loading').hidden = true; notice('#global-error', ''); await setupCamera(); if (state.code) { $('#ticket-code').value = state.code; await preview(); } }
  async function loadSession() { $('#session-loading').hidden = false; $('#retry-session').hidden = true; notice('#global-error', ''); try { const session = await api('/api/admin/session'); if (session.authenticated) await enter(session); else { showLogin(); if (session.setupRequired) { $('#login-form').hidden = true; notice('#global-error', '请先在管理台建立工作人员账号。'); } } } catch (error) { notice('#global-error', error.message); $('#retry-session').hidden = false; } finally { $('#session-loading').hidden = true; } }
  $('#login-form').addEventListener('submit', async event => { event.preventDefault(); if ($('#login-submit').disabled || !$('#login-form').reportValidity()) return; $('#login-submit').disabled = true; notice('#global-error', ''); try { const session = await api('/api/admin/login', { username: $('#username').value.trim(), password: $('#password').value }); if (!session.authenticated) throw new Error('未能建立登录状态，请重试。'); await enter(session); } catch (error) { notice('#global-error', error.message); } finally { $('#login-submit').disabled = false; } });
  $('#logout').addEventListener('click', async () => { if (state.busy) return; setBusy(true); try { await api('/api/admin/logout', {}); showLogin(); $('#username').focus(); } catch (error) { notice('#ticket-error', error.message); } finally { setBusy(false); } });
  $('#retry-session').addEventListener('click', loadSession);
  function renderTicket(data, outcome = '') {
    state.preview = data; const registration = data.registration; const event = data.event;
    $('#guest-name').textContent = registration.name; $('#guest-ticket').textContent = registration.ticketNumber; $('#guest-phone').textContent = registration.phone || registration.phoneMasked;
    $('#guest-event').textContent = event.title; $('#guest-time').textContent = `${fmt(event.startsAt)} — ${fmt(event.endsAt)} · 北京时间`; $('#guest-venue').textContent = `${event.venue} · ${event.address}`;
    if (['shenzhen','zhuhai','guangzhou'].includes(event.posterCity)) $('#stage-photo').src = `/city-vote/assets/alan-v1/${event.posterCity}.webp`;
    const checked = registration.status === 'checked_in'; const accepted = outcome === 'success'; const eligible = Boolean(data.eligible) && !checked;
    $('#ticket-result').dataset.tone = accepted ? 'success' : eligible ? 'ready' : 'blocked';
    $('#result-symbol').textContent = accepted ? '✓' : eligible ? '·' : '!';
    $('#result-label').textContent = accepted ? 'WELCOME TO LEAN CLUB' : eligible ? 'READY TO MEET' : 'PLEASE CHECK';
    $('#result-title').textContent = accepted ? '核销成功，欢迎入场。' : checked ? '这张票已核销。' : eligible ? '入场凭证有效。' : '暂不能入场。';
    $('#result-reason').textContent = accepted ? '本次入场已记录。' : checked ? '已有入场记录，请勿重复放行。' : eligible ? '请核对来宾与活动信息。' : (data.reason || '此入场凭证当前不可核销。');
    $('#checked-in-time').textContent = registration.checkedInAt ? `首次核销：${fmt(registration.checkedInAt)} · 北京时间` : ''; $('#checked-in-time').hidden = !registration.checkedInAt;
    $('#ticket-result').hidden = false; $('#ready-state').hidden = true; $('#confirm-checkin').hidden = !eligible || accepted; $('#confirm-hint').hidden = !eligible || accepted;
    $('#result-title').focus({ preventScroll: true });
  }
  async function preview() {
    if (state.busy || !state.authenticated) return;
    let code; try { code = parseCode($('#ticket-code').value); } catch (error) { notice('#ticket-error', error.message); return; }
    if (code !== state.code) { state.code = code; state.requestId = ''; }
    stopCamera(); const epoch = state.epoch; setBusy(true); clearResult(); $('#ready-state').hidden = true; $('#ticket-loading').hidden = false;
    try { const data = await api('/api/admin/events/check-in/preview', { code }); if (epoch !== state.epoch) return; renderTicket(data); }
    catch (error) { if (epoch === state.epoch) notice('#ticket-error', error.message); }
    finally { if (epoch === state.epoch) { $('#ticket-loading').hidden = true; setBusy(false); } }
  }
  $('#code-form').addEventListener('submit', event => { event.preventDefault(); preview(); });
  $('#ticket-code').addEventListener('input', () => { if (!state.busy) clearResult(); });
  $('#confirm-checkin').addEventListener('click', async () => {
    if (state.busy || !state.preview?.eligible || !state.authenticated) return; const epoch = state.epoch;
    if (!state.requestId) state.requestId = uuid(); setBusy(true); notice('#ticket-error', '');
    try { const data = await api('/api/admin/events/check-in', { code: state.code, requestId: state.requestId }); if (epoch !== state.epoch) return; renderTicket({ ...data, eligible: false }, data.duplicate ? 'duplicate' : 'success'); }
    catch (error) { if (epoch === state.epoch) { notice('#ticket-error', error.message); $('#confirm-checkin').hidden = true; $('#confirm-hint').hidden = true; state.preview = null; $('#result-reason').textContent = '核销结果尚未确认，请点击“核对”读取最新状态。'; } }
    finally { if (epoch === state.epoch) setBusy(false); }
  });
  $('#next-ticket').addEventListener('click', () => { if (state.busy) return; clearResult(); state.code = ''; state.requestId = ''; $('#ticket-code').value = ''; history.replaceState(null, '', location.pathname); $('#ticket-code').focus(); });
  async function setupCamera() {
    $('#open-camera').hidden = true;
    if (!isSecureContext || !navigator.mediaDevices?.getUserMedia || !('BarcodeDetector' in window)) return;
    try { const types = await BarcodeDetector.getSupportedFormats(); if (types.includes('qr_code')) { $('#open-camera').hidden = false; $('#camera-hint').textContent = '也可输入票号，或粘贴票码链接。'; } } catch { /* Manual entry remains available. */ }
  }
  function stopCamera() { state.cameraEpoch++; clearTimeout(state.scanTimer); state.scanTimer = null; state.scanning = false; if (state.stream) for (const track of state.stream.getTracks()) track.stop(); state.stream = null; $('#camera-video').srcObject = null; $('#camera-view').hidden = true; $('#open-camera').disabled = state.busy; }
  async function startCamera() {
    if (state.busy || state.stream || !state.authenticated) return; const cameraEpoch = ++state.cameraEpoch;
    $('#open-camera').disabled = true; notice('#ticket-error', '');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
      if (cameraEpoch !== state.cameraEpoch || document.hidden || !state.authenticated) { for (const track of stream.getTracks()) track.stop(); return; }
      state.stream = stream; $('#camera-view').hidden = false; $('#camera-video').srcObject = stream; await $('#camera-video').play();
      const detector = new BarcodeDetector({ formats: ['qr_code'] });
      async function scan() {
        if (!state.stream || state.scanning || cameraEpoch !== state.cameraEpoch) return; state.scanning = true;
        try { const codes = await detector.detect($('#camera-video')); if (cameraEpoch !== state.cameraEpoch) return; if (codes.length) { try { const code = parseCode(codes[0].rawValue); stopCamera(); $('#ticket-code').value = code; await preview(); return; } catch { $('#camera-status').textContent = '这不是本站入场码，请扫描正确的票码。'; } } }
        catch { if (cameraEpoch === state.cameraEpoch) $('#camera-status').textContent = '请保持画面稳定，将完整票码放进框内。'; }
        finally { state.scanning = false; if (state.stream && cameraEpoch === state.cameraEpoch) state.scanTimer = setTimeout(scan, 350); }
      }
      scan();
    } catch { if (cameraEpoch === state.cameraEpoch) { stopCamera(); notice('#ticket-error', '无法开启相机。请检查权限，或直接输入入场票号。'); } }
  }
  $('#open-camera').addEventListener('click', startCamera); $('#close-camera').addEventListener('click', stopCamera);
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopCamera(); }); window.addEventListener('pagehide', stopCamera);
  function readFragment() { const token = new URLSearchParams(location.hash.slice(1)).get('ticket'); if (token && token.length <= 2048) { state.code = token; $('#ticket-code').value = token; return true; } return false; }
  window.addEventListener('hashchange', () => { if (!state.busy && readFragment() && state.authenticated) { state.requestId = ''; preview(); } });
  readFragment(); loadSession();
})();
