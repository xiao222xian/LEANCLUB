(() => {
  'use strict';
  const { api, randomId, date, poster, dialog, ticketUrl, text, cityNames, money } = window.LeanEvents;
  const $ = id => document.getElementById(id);
  const registerDialog = dialog($('register-dialog'), $('open-register'));
  const mineDialog = dialog($('mine-dialog'), $('open-mine'));
  let events = [], selected = null, tickets = [], sending = false, request = null, loading = false, mineLoading = false, mineSequence = 0, formEvent = null;
  const drafts = new Map();
  function error(message) { $('register-error').textContent = message; $('register-error').hidden = !message; }
  function activeTicket(event) { return tickets.find(t => t.event.id === event.id && (!t.order || ['pending_payment', 'paid', 'refund_pending'].includes(t.order.status)) && t.registration.status !== 'cancelled'); }
  function orderLink(ticket) { return ticketUrl(ticket.orderToken || ticket.ticketToken); }
  function availability(event) {
    if (event.status === 'cancelled') return '活动已取消';
    if (event.status === 'closed') return '报名已结束';
    if (new Date(event.endsAt) <= new Date()) return '活动已结束';
    if (event.paymentReady === false) return '报名即将开放';
    if (!event.registrationOpen) return event.remaining <= 0 ? '本场名额已满' : '报名已截止';
    return `还剩 ${event.remaining} 个位置`;
  }
  function render() {
    if (!selected) return;
    $('waiting-state').hidden = true; $('event-stage').hidden = false;
    const existing = activeTicket(selected);
    $('event-photo').src = poster(selected); $('register-photo').src = poster(selected);
    $('event-city').textContent = cityNames[selected.city] || '线下见';
    $('event-number').textContent = String(events.indexOf(selected) + 1).padStart(2, '0');
    $('event-title').textContent = selected.title;
    $('event-description').textContent = selected.subtitle || '';
    $('event-description').hidden = !selected.subtitle;
    $('event-date').textContent = date(selected.startsAt);
    $('event-venue').textContent = selected.venue;
    $('event-address').textContent = selected.address;
    $('event-price').textContent = money(selected.priceFen);
    $('event-body').textContent = selected.description || '';
    $('event-long-description').hidden = !selected.description;
    $('event-availability').textContent = availability(selected);
    $('event-deadline').textContent = selected.registrationOpen ? `报名截止 ${date(selected.registrationClosesAt)}` : '';
    $('open-register').textContent = existing ? (existing.order?.status === 'pending_payment' ? '查看待支付订单' : '查看我的入场券') : selected.registrationOpen ? '一起，线下见' : availability(selected);
    $('open-register').disabled = !existing && !selected.registrationOpen;
    document.title = `${selected.title} — LEAN CLUB`;
    const picker = $('event-picker'); picker.replaceChildren();
    if (events.length > 1) events.forEach((event, i) => {
      const button = text('button', event.title); button.type = 'button'; button.setAttribute('aria-pressed', String(event.id === selected.id));
      button.prepend(text('span', String(i + 1).padStart(2, '0')));
      button.addEventListener('click', () => { selected = event; render(); const url = new URL(location.href); url.searchParams.set('event', event.id); history.replaceState(null, '', url); });
      picker.append(button);
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
      $('retry-load').hidden = true; loadMine(false);
    } catch (e) { $('connection-status').textContent = e.message; $('retry-load').hidden = false; }
    finally { loading = false; $('retry-load').disabled = false; }
  }
  async function loadMine(showState = true) {
    if (mineLoading) return; mineLoading = true; const sequence = ++mineSequence;
    if (showState) $('mine-list').replaceChildren(text('p', '正在找回你的入场券…', 'mine-empty'));
    $('retry-mine').hidden = true;
    try {
      const result = await api('/api/campaign/events/mine'); if (sequence !== mineSequence) return;
      tickets = result.tickets || [];
      const count = tickets.filter(t => t.registration.status !== 'cancelled' && t.event.status !== 'cancelled' && (!t.order || ['pending_payment', 'paid'].includes(t.order.status))).length;
      $('ticket-count').textContent = count; $('ticket-count').hidden = !count;
      const list = $('mine-list'); list.replaceChildren();
      if (!tickets.length) list.append(text('p', '还没有入场券。\n下一次，现场见。', 'mine-empty'));
      tickets.forEach(ticket => {
        const link = document.createElement('a'); link.className = 'mine-item'; link.href = orderLink(ticket);
        link.append(text('h3', ticket.event.title), text('p', `${cityNames[ticket.event.city] || ''} · ${date(ticket.event.startsAt)}`));
        const state = ticket.event.status === 'cancelled' ? '活动已取消' : ({ pending_payment: '待付款 · 名额限时保留', expired: '订单已过期', cancelled: '订单已取消', refund_pending: '已申请退款', refunded: '已退款' }[ticket.order?.status] || { registered: '查看入场券', checked_in: '已入场', cancelled: '已取消报名' }[ticket.registration.status]);
        link.append(text('p', state, 'mine-status')); list.append(link);
      });
      if (selected) render();
    } catch (e) { if (showState || $('mine-dialog').open) { $('mine-list').replaceChildren(text('p', e.message, 'mine-empty')); $('retry-mine').hidden = false; } }
    finally { mineLoading = false; }
  }
  $('open-register').addEventListener('click', () => {
    if (!selected || sending) return;
    const existing = activeTicket(selected); if (existing) { location.assign(orderLink(existing)); return; }
    formEvent = { id: selected.id, priceFen: selected.priceFen };
    const draft = drafts.get(selected.id) || {};
    $('register-name').value = draft.name || ''; $('register-phone').value = draft.phone || ''; $('register-consent').checked = !!draft.consent;
    $('register-event-name').textContent = `${selected.title} · ${date(selected.startsAt)}`;
    $('register-price').textContent = money(selected.priceFen);
    $('register-submit').disabled = false; $('refresh-registration-price').hidden = true;
    $('register-name').removeAttribute('aria-invalid'); $('register-phone').removeAttribute('aria-invalid');
    error(''); registerDialog.open();
  });
  $('register-form').addEventListener('input', () => {
    if (selected) drafts.set(selected.id, { name: $('register-name').value, phone: $('register-phone').value, consent: $('register-consent').checked });
    error(''); $('register-name').removeAttribute('aria-invalid'); $('register-phone').removeAttribute('aria-invalid');
  });
  $('register-form').addEventListener('submit', async e => {
    e.preventDefault(); if (sending || !formEvent) return;
    const name = $('register-name').value.trim(); const phone = $('register-phone').value.trim().replace(/[\s-]/g, '').replace(/^\+86/, '');
    if ([...name].length < 2 || [...name].length > 30) { error('请填写 2–30 个字的称呼。'); $('register-name').setAttribute('aria-invalid', 'true'); $('register-name').focus(); return; }
    if (!/^1[3-9]\d{9}$/.test(phone)) { error('请填写有效的 11 位中国大陆手机号。'); $('register-phone').setAttribute('aria-invalid', 'true'); $('register-phone').focus(); return; }
    if (!$('register-consent').checked) { error('请先确认活动联系与入场核验说明。'); $('register-consent').focus(); return; }
    const eventId = formEvent.id; const payload = { name, phone, consent: true, expectedPriceFen: formEvent.priceFen }; const fingerprint = JSON.stringify([eventId, payload]);
    if (!request || request.fingerprint !== fingerprint) request = { fingerprint, requestId: randomId() };
    sending = true; error(''); $('register-submit').disabled = true; $('register-submit').classList.add('is-busy'); $('register-submit').textContent = '正在为你留位…';
    ['register-name', 'register-phone', 'register-consent'].forEach(id => { $(id).disabled = true; });
    try {
      const result = await api(`/api/campaign/events/${encodeURIComponent(eventId)}/register`, { ...payload, requestId: request.requestId });
      if (!result.orderToken && !result.ticketToken) throw new Error('正在确认订单，请重试或打开“我的入场券”。');
      drafts.delete(eventId); location.assign(orderLink(result));
    } catch (failure) { error(failure.message); $('refresh-registration-price').hidden = !(failure.status === 409 && /价格|费用/.test(failure.message)); }
    finally { sending = false; $('register-submit').disabled = false; $('register-submit').classList.remove('is-busy'); $('register-submit').textContent = '锁定名额，去付款'; ['register-name', 'register-phone', 'register-consent'].forEach(id => { $(id).disabled = false; }); }
  });
  $('refresh-registration-price').addEventListener('click', async () => {
    if (!formEvent || sending) return; const eventId = formEvent.id; $('refresh-registration-price').disabled = true;
    try {
      const { event } = await api(`/api/campaign/events/${encodeURIComponent(eventId)}`);
      formEvent = { id: event.id, priceFen: event.priceFen }; events = events.map(item => item.id === event.id ? event : item); selected = event; render();
      $('register-price').textContent = money(event.priceFen); $('register-consent').checked = false; request = null;
      $('register-event-name').textContent = `${event.title} · ${date(event.startsAt)}`;
      $('register-submit').disabled = !event.registrationOpen; $('refresh-registration-price').hidden = true;
      error(event.registrationOpen ? '费用已更新，请重新确认费用与取消说明。' : '当前活动已无法报名，已保留你的填写内容。');
    } catch (failure) { error(failure.message); }
    finally { $('refresh-registration-price').disabled = false; }
  });
  $('close-register').addEventListener('click', () => registerDialog.close());
  $('open-mine').addEventListener('click', () => { mineDialog.open(); loadMine(); });
  $('close-mine').addEventListener('click', () => mineDialog.close());
  $('retry-load').addEventListener('click', load); $('retry-mine').addEventListener('click', () => loadMine());
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && !$('register-dialog').open) load(); });
  load();
  if (new URLSearchParams(location.search).get('mine') === '1') { mineDialog.open(); loadMine(); }
})();
