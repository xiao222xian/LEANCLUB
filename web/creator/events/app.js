(() => {
  'use strict';
  const { api, randomId, date, poster, dialog, ticketUrl, memberOrderUrl, text, cityNames, money } = window.LeanEvents;
  const portal = window.LeanClubPortal;
  const $ = id => document.getElementById(id);
  const registerDialog = dialog($('register-dialog'), $('open-register'));
  const mineDialog = dialog($('mine-dialog'), $('open-mine'));
  let events = [], selected = null, tickets = [], sending = false, request = null, loading = false, mineLoading = false, mineSequence = 0, formEvent = null;
  let accountVersion = portal.identityVersion(), mineMode = 'member', minePage = 1, mineTotal = 0;
  const drafts = new Map();
  const sameAccount = version => version === accountVersion && version === portal.identityVersion();
  function error(message) { $('register-error').textContent = message; $('register-error').hidden = !message; }
  function activeTicket(event) { return tickets.find(t => t.event.id === event.id && ['pending_payment', 'paid', 'closing', 'refund_pending'].includes(t.order?.status) && t.registration.status !== 'cancelled'); }
  function orderLink(ticket, legacy = false) { return legacy ? ticketUrl(ticket.orderToken || ticket.ticketToken) : memberOrderUrl(ticket.order?.id); }
  function availability(event) {
    if (event.status === 'cancelled') return '活动已取消';
    if (event.status === 'closed') return '报名已结束';
    if (new Date(event.endsAt) <= new Date()) return '活动已结束';
    if (event.paymentReady !== true) return '报名即将开放';
    if (!event.registrationOpen) return event.remaining <= 0 ? '本场名额已满' : '报名已截止';
    return `还剩 ${event.remaining} 个位置`;
  }
  function mayRegister(event) { return event?.registrationOpen === true && event.paymentReady === true; }
  function render() {
    if (!selected) return;
    $('waiting-state').hidden = true; $('event-stage').hidden = false;
    const existing = activeTicket(selected);
    $('event-photo').src = poster(selected); $('register-photo').src = poster(selected);
    $('event-city').textContent = cityNames[selected.city] || '线下见';
    $('event-number').textContent = String(events.indexOf(selected) + 1).padStart(2, '0');
    $('event-title').textContent = selected.title;
    $('event-description').textContent = selected.subtitle || ''; $('event-description').hidden = !selected.subtitle;
    $('event-date').textContent = date(selected.startsAt); $('event-venue').textContent = selected.venue; $('event-address').textContent = selected.address;
    $('event-price').textContent = money(selected.priceFen); $('event-body').textContent = selected.description || ''; $('event-long-description').hidden = !selected.description;
    $('event-availability').textContent = availability(selected);
    $('event-deadline').textContent = mayRegister(selected) ? `报名截止 ${date(selected.registrationClosesAt)}` : '';
    $('open-register').textContent = existing ? (existing.order?.status === 'pending_payment' ? '查看待支付订单' : '查看我的报名') : mayRegister(selected) ? '一起，线下见' : availability(selected);
    $('open-register').disabled = !existing && !mayRegister(selected);
    document.title = `${selected.title} — LEAN SCHOOL`;
    const picker = $('event-picker'); picker.replaceChildren();
    if (events.length > 1) events.forEach((event, i) => {
      const button = text('button', event.title); button.type = 'button'; button.setAttribute('aria-pressed', String(event.id === selected.id));
      button.prepend(text('span', String(i + 1).padStart(2, '0')));
      button.addEventListener('click', () => { selected = event; render(); const url = new URL(location.href); url.searchParams.set('event', event.id); history.replaceState(null, '', url); }); picker.append(button);
    });
  }
  async function load() {
    if (loading) return; loading = true; $('retry-load').disabled = true;
    try {
      const result = await api('/api/campaign/events'); events = result.events || [];
      Object.keys(cityNames).forEach(city => { $(`empty-${city}`).src = poster({ city }); });
      const id = new URLSearchParams(location.search).get('event');
      selected = events.find(e => e.id === (selected?.id || id)) || events.find(e => e.registrationOpen) || events[0] || null;
      if (selected) render(); else { $('event-stage').hidden = true; $('waiting-state').hidden = false; $('connection-status').textContent = '活动发布后，在这里报名。'; }
      $('retry-load').hidden = true;
      if (mineMode === 'member' && portal.isSignedIn()) loadMine(false);
    } catch (failure) { $('connection-status').textContent = failure.message; $('retry-load').hidden = false; }
    finally { loading = false; $('retry-load').disabled = false; }
  }
  function renderMineGate(message = '登录后，找回你的报名与入场券。') {
    $('mine-list').replaceChildren(text('p', message, 'mine-empty'));
    $('mine-sign-in').hidden = false; $('mine-pagination').hidden = true; $('retry-mine').hidden = true;
  }
  function renderMine(items, legacy) {
    const list = $('mine-list'); list.replaceChildren();
    if (!items.length) list.append(text('p', legacy ? '这个浏览器里还没有旧报名记录。' : '还没有报名记录。\n下一次，现场见。', 'mine-empty'));
    items.forEach(ticket => {
      const link = document.createElement('a'); link.className = 'mine-item'; link.href = orderLink(ticket, legacy);
      link.append(text('h3', ticket.event.title), text('p', `${cityNames[ticket.event.city] || ''} · ${date(ticket.event.startsAt)}`));
      const financialState = { closing: '正在确认订单关闭', refund_pending: '退款处理中', refunded: '已退款' }[ticket.order?.status];
      const state = financialState || (ticket.event.status === 'cancelled' ? '活动已取消' : ({ pending_payment: '待付款', expired: '订单已过期', cancelled: '订单已取消' }[ticket.order?.status] || { registered: '查看入场券', checked_in: '已入场', cancelled: '已取消报名' }[ticket.registration.status] || '状态待核实'));
      const meta = text('div', '', 'mine-item-meta'); meta.append(text('p', state, 'mine-status'));
      if (ticket.order) meta.append(text('span', money(ticket.order.amountFen), 'mine-price'));
      link.append(meta); list.append(link);
    });
    $('mine-pagination').hidden = legacy || mineTotal <= 20;
    $('mine-page-label').textContent = `${minePage} / ${Math.max(1, Math.ceil(mineTotal / 20))}`;
    $('mine-prev').disabled = minePage <= 1; $('mine-next').disabled = minePage * 20 >= mineTotal;
  }
  async function loadMine(showState = true) {
    if (mineLoading) return;
    if (mineMode === 'member' && !portal.isSignedIn()) { if (showState) renderMineGate(); return; }
    mineLoading = true;
    const sequence = ++mineSequence, version = accountVersion, legacy = mineMode === 'legacy';
    if (showState) $('mine-list').replaceChildren(text('p', '正在读取报名记录…', 'mine-empty'));
    $('mine-sign-in').hidden = true; $('retry-mine').hidden = true; $('mine-pagination').hidden = true;
    try {
      const result = legacy ? await api('/api/campaign/events/mine') : await portal.api(`/leanclub/orders/page?pageNo=${minePage}&pageSize=20&status=all`, { auth: true });
      if (sequence !== mineSequence || !sameAccount(version)) return;
      const items = legacy ? result.tickets : result.list;
      if (!Array.isArray(items)) throw new Error('报名记录暂时无法读取，请重试。');
      if (!legacy) {
        tickets = items; mineTotal = Number(result.total) || 0;
        $('ticket-count').textContent = mineTotal; $('ticket-count').hidden = !mineTotal;
      }
      renderMine(items, legacy); if (selected) render();
    } catch (failure) {
      if (sequence !== mineSequence || !sameAccount(version)) return;
      if (!legacy) { tickets = []; $('ticket-count').textContent = ''; $('ticket-count').hidden = true; if (selected) render(); }
      if (showState || $('mine-dialog').open) { $('mine-list').replaceChildren(text('p', failure.message, 'mine-empty')); $('retry-mine').hidden = false; }
    } finally { if (sequence === mineSequence && sameAccount(version)) mineLoading = false; }
  }
  function switchMine(mode) {
    mineMode = mode; minePage = 1; mineSequence++; mineLoading = false;
    $('mine-member').setAttribute('aria-pressed', String(mode === 'member')); $('mine-legacy').setAttribute('aria-pressed', String(mode === 'legacy'));
    $('mine-note').textContent = mode === 'legacy' ? '仅显示此浏览器曾经的旧报名，不会自动并入账号。' : '登录同一账号，可在不同设备查看报名。';
    $('mine-list').replaceChildren(); loadMine();
  }
  function openRegistration(eventId) {
    const event = events.find(item => item.id === eventId);
    if (!event || !mayRegister(event) || !portal.isSignedIn() || sending) return;
    selected = event;
    const existing = activeTicket(event); if (existing) { location.assign(orderLink(existing)); return; }
    formEvent = { id: event.id, priceFen: event.priceFen };
    const draft = drafts.get(event.id) || {};
    $('register-name').value = draft.name || ''; $('register-phone').value = draft.phone || ''; $('register-consent').checked = !!draft.consent && draft.priceFen === event.priceFen;
    $('register-event-name').textContent = `${event.title} · ${date(event.startsAt)}`; $('register-price').textContent = money(event.priceFen);
    $('register-submit').disabled = false; $('refresh-registration-price').hidden = true;
    $('register-name').removeAttribute('aria-invalid'); $('register-phone').removeAttribute('aria-invalid');
    error(''); registerDialog.open();
  }
  $('open-register').addEventListener('click', () => {
    if (!selected || sending) return;
    const existing = activeTicket(selected); if (existing) { location.assign(orderLink(existing)); return; }
    if (!mayRegister(selected)) return;
    const eventId = selected.id;
    if (!portal.isSignedIn()) { portal.requireMember(() => openRegistration(eventId)); return; }
    openRegistration(eventId);
  });
  $('register-form').addEventListener('input', () => {
    if (formEvent && portal.isSignedIn()) drafts.set(formEvent.id, { name: $('register-name').value, phone: $('register-phone').value, consent: $('register-consent').checked, priceFen: formEvent.priceFen });
    error(''); $('register-name').removeAttribute('aria-invalid'); $('register-phone').removeAttribute('aria-invalid');
  });
  $('register-form').addEventListener('submit', async e => {
    e.preventDefault(); if (sending || !formEvent || !portal.isSignedIn()) return;
    const event = events.find(item => item.id === formEvent.id); if (!mayRegister(event)) { error('当前活动尚未开放付款报名。'); return; }
    const name = $('register-name').value.trim(), phone = $('register-phone').value.trim().replace(/[\s-]/g, '').replace(/^\+86/, '');
    if ([...name].length < 2 || [...name].length > 30) { error('请填写 2–30 个字的称呼。'); $('register-name').setAttribute('aria-invalid', 'true'); $('register-name').focus(); return; }
    if (!/^1[3-9]\d{9}$/.test(phone)) { error('请填写有效的 11 位中国大陆手机号。'); $('register-phone').setAttribute('aria-invalid', 'true'); $('register-phone').focus(); return; }
    if (!$('register-consent').checked) { error('请先确认活动联系与入场核验说明。'); $('register-consent').focus(); return; }
    const version = accountVersion, eventId = formEvent.id, payload = { name, phone, consent: true, expectedPriceFen: formEvent.priceFen }, fingerprint = JSON.stringify([eventId, payload]);
    if (!request || request.fingerprint !== fingerprint) request = { fingerprint, requestId: randomId() };
    sending = true; error(''); $('register-submit').disabled = true; $('register-submit').classList.add('is-busy'); $('register-submit').textContent = '正在为你留位…';
    ['register-name', 'register-phone', 'register-consent'].forEach(id => { $(id).disabled = true; });
    try {
      const result = await portal.api(`/leanclub/events/${encodeURIComponent(eventId)}/register`, { auth: true, body: { ...payload, requestId: request.requestId } });
      if (!sameAccount(version) || !portal.isSignedIn()) return;
      const href = memberOrderUrl(result.order?.id); drafts.delete(eventId); location.assign(href);
    } catch (failure) {
      if (!sameAccount(version)) return;
      error(failure.message); $('refresh-registration-price').hidden = !((failure.code === 409 || failure.status === 409) && /价格|费用/.test(failure.message));
    } finally {
      if (sameAccount(version)) { sending = false; $('register-submit').disabled = false; $('register-submit').classList.remove('is-busy'); $('register-submit').textContent = '锁定名额，去付款'; ['register-name', 'register-phone', 'register-consent'].forEach(id => { $(id).disabled = false; }); }
    }
  });
  $('refresh-registration-price').addEventListener('click', async () => {
    if (!formEvent || sending || !portal.isSignedIn()) return; const eventId = formEvent.id, version = accountVersion; $('refresh-registration-price').disabled = true;
    try {
      const { event } = await api(`/api/campaign/events/${encodeURIComponent(eventId)}`);
      if (!sameAccount(version) || !formEvent || eventId !== formEvent.id) return;
      formEvent = { id: event.id, priceFen: event.priceFen }; events = events.map(item => item.id === event.id ? event : item); selected = event; render();
      $('register-price').textContent = money(event.priceFen); $('register-consent').checked = false; request = null;
      drafts.set(event.id, { name: $('register-name').value, phone: $('register-phone').value, consent: false, priceFen: event.priceFen });
      $('register-event-name').textContent = `${event.title} · ${date(event.startsAt)}`;
      $('register-submit').disabled = !mayRegister(event); $('refresh-registration-price').hidden = true;
      error(mayRegister(event) ? '费用已更新，请重新确认费用与取消说明。' : '当前活动已无法报名，已保留你的填写内容。');
    } catch (failure) { if (sameAccount(version)) error(failure.message); }
    finally { if (sameAccount(version)) $('refresh-registration-price').disabled = false; }
  });
  function clearAccount() {
    accountVersion = portal.identityVersion(); mineSequence++; mineLoading = false; tickets = []; mineTotal = 0; minePage = 1;
    drafts.clear(); request = null; formEvent = null; sending = false;
    $('register-name').value = ''; $('register-phone').value = ''; $('register-consent').checked = false;
    ['register-name', 'register-phone', 'register-consent', 'register-submit', 'refresh-registration-price'].forEach(id => { $(id).disabled = false; });
    $('register-submit').classList.remove('is-busy'); $('register-submit').textContent = '锁定名额，去付款'; error('');
    if ($('register-dialog').open) registerDialog.close();
    $('ticket-count').textContent = ''; $('ticket-count').hidden = true; $('mine-list').replaceChildren(); $('mine-pagination').hidden = true;
    if (selected) render();
    if (mineMode === 'member') { if (portal.isSignedIn()) loadMine(); else renderMineGate('账户已退出，请登录后查看报名。'); }
    else { $('mine-list').append(text('p', '账户状态已改变，请重新读取浏览器旧记录。', 'mine-empty')); $('retry-mine').hidden = false; }
  }
  window.addEventListener('leanclub:identity', clearAccount);
  $('close-register').addEventListener('click', () => { if (!sending) registerDialog.close(); });
  $('register-dialog').addEventListener('cancel', event => { if (sending) event.preventDefault(); });
  $('open-mine').addEventListener('click', () => { mineDialog.open(); loadMine(); });
  $('close-mine').addEventListener('click', () => mineDialog.close());
  $('mine-member').addEventListener('click', () => switchMine('member')); $('mine-legacy').addEventListener('click', () => switchMine('legacy'));
  $('mine-sign-in').addEventListener('click', () => { mineDialog.close(); portal.requireMember(() => { mineMode = 'member'; mineDialog.open(); switchMine('member'); }); });
  $('mine-prev').addEventListener('click', () => { if (!mineLoading && minePage > 1) { minePage--; loadMine(); } });
  $('mine-next').addEventListener('click', () => { if (!mineLoading && minePage * 20 < mineTotal) { minePage++; loadMine(); } });
  $('retry-load').addEventListener('click', load); $('retry-mine').addEventListener('click', () => loadMine());
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && !$('register-dialog').open) load(); });
  load();
  if (new URLSearchParams(location.search).get('mine') === '1') { mineDialog.open(); loadMine(); }
})();
