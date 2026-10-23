(() => {
  'use strict';
  const { api, randomId, date, poster, dialog, cityNames, money } = window.LeanEvents;
  const portal = window.LeanClubPortal;
  const $ = id => document.getElementById(id);
  const cancelDialog = dialog($('cancel-dialog'), $('open-cancel'));
  let current = null, loading = false, cancelling = false, saving = false, poll = null, generation = 0, cancelRequest = null;
  const token = () => new URLSearchParams(location.hash.slice(1)).get('ticket') || '';
  function access() {
    const params = new URLSearchParams(location.search);
    // An explicit account order can never fall back to an anonymous ticket fragment.
    if (params.has('order')) { const id = params.get('order'); return { member: true, id, valid: /^[a-f0-9]{32}$/.test(id || ''), key: `member:${id}` }; }
    const secret = token(); return { member: false, secret, valid: !!secret, key: `legacy:${secret}` };
  }
  const unchanged = (version, key, identity) => version === generation && access().key === key && identity === portal.identityVersion();
  const stateLabels = { pending_payment: '待支付', paid: '待入场', closing: '正在确认订单关闭', expired: '订单已过期', cancelled: '订单已取消', refund_pending: '退款处理中', refunded: '已退款' };
  function isValid(ticket) { return ticket && ticket.order?.status === 'paid' && ['registered', 'checked_in'].includes(ticket.registration.status) && ticket.event.status !== 'cancelled' && new Date(ticket.event.endsAt) > new Date() && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(ticket.qrDataUrl || ''); }
  function render(ticket) {
    current = ticket;
    const { event, registration, order } = ticket; const paid = order?.status === 'paid'; const pending = order?.status === 'pending_payment';
    const cancelled = registration.status === 'cancelled' || event.status === 'cancelled'; const used = registration.status === 'checked_in'; const valid = isValid(ticket);
    const ended = new Date(event.endsAt) <= new Date();
    const financialState = ['closing', 'refund_pending', 'refunded'].includes(order?.status);
    const status = financialState ? stateLabels[order.status] : event.status === 'cancelled' ? '活动已取消' : used && paid ? '已入场' : ended && paid ? '活动已结束' : order ? stateLabels[order.status] || '订单待核实' : cancelled ? '已取消报名' : '订单待核实';
    $('ticket-loading').hidden = true; $('ticket-experience').hidden = false;
    $('ambient-photo').src = poster(event); $('ticket-photo').src = poster(event);
    $('ticket-title').textContent = event.title; $('ticket-subtitle').textContent = event.subtitle || '';
    $('ticket-city').textContent = `${cityNames[event.city] || ''} / ${event.startsAt ? new Date(event.startsAt).getFullYear() : ''}`;
    $('ticket-kicker').textContent = pending ? 'YOUR PLACE IS ON HOLD' : valid ? used ? 'GOOD TO SEE YOU' : "YOU'RE ON THE LIST" : 'LEAN SCHOOL / YOUR ORDER';
    $('ticket-headline').textContent = pending ? '还差一步，现场见。' : valid ? used ? '相聚，就在此刻。' : '期待见到你。' : status;
    $('ticket-intro').textContent = pending ? '完成付款，领取你的专属入场券。' : valid ? '把这一刻，留给真实的连接。' : '订单信息与状态保留在这里。';
    $('ticket-type').textContent = valid ? 'ADMIT ONE' : 'ORDER / ONE PERSON';
    $('ticket-state').textContent = status; $('ticket-state').classList.toggle('is-invalid', !valid && !pending);
    $('ticket-date').textContent = date(event.startsAt); $('ticket-venue').textContent = event.venue; $('ticket-address').textContent = event.address;
    $('ticket-name').textContent = registration.name; $('ticket-phone').textContent = registration.phoneMasked;
    $('ticket-price-row').hidden = !order; $('ticket-price').textContent = money(order?.amountFen ?? event.priceFen);
    $('ticket-number').textContent = valid ? registration.ticketNumber : order ? `ORDER ${String(order.id).slice(-12).toUpperCase()}` : registration.ticketNumber;
    $('ticket-qr-area').hidden = !valid; $('order-state').hidden = valid;
    if (valid) $('ticket-qr').src = ticket.qrDataUrl; else $('ticket-qr').removeAttribute('src');
    $('order-state-title').textContent = pending ? money(order.amountFen) : status;
    $('order-state-copy').textContent = pending ? `付款截止 ${date(order.expiresAt)}。付款确认后，此处显示入场二维码。` : order?.status === 'closing' ? '正在确认订单关闭结果，请稍后刷新。' : order?.status === 'expired' ? '付款时限已过。你可以返回活动查看最新名额。' : order?.status === 'refund_pending' ? '退款正在处理，尚未确认完成。原入场凭证已失效。' : order?.status === 'refunded' ? '订单已退款，原入场凭证已失效。' : '本次入场凭证已失效，期待下一次见面。';
    $('payment-action').hidden = true;
    if (pending) { $('order-state-copy').textContent += ' 付款方式尚未开放，请稍后刷新或联系主办方。'; }
    $('checkin-time').hidden = !used; $('checkin-time').textContent = used && registration.checkedInAt ? `核销时间 ${date(registration.checkedInAt)}` : '';
    $('save-ticket').hidden = !valid; $('open-cancel').hidden = !ticket.canCancel;
    $('open-cancel').textContent = paid && order ? '取消报名与退款' : pending ? '取消订单' : '取消报名';
    $('ticket-save-note').textContent = valid ? '请妥善保存，不要公开分享二维码。' : pending ? '付款状态以服务器确认为准，付款后可刷新查看。' : access().member ? '登录同一账号，可在“我的报名”查看历史状态。' : '这是一张旧票，保留原链接以便再次查看。';
    $('cancel-description').textContent = pending ? '确认取消此订单？如已付款但状态未更新，请先刷新订单。' : order && paid ? '取消后入场券立即失效，并提交退款申请。退款进度以主办方实际处理为准。' : '确认取消本次报名？入场凭证将按最新订单状态更新。';
    clearTimeout(poll);
    if (['pending_payment', 'closing', 'refund_pending'].includes(order?.status) && document.visibilityState === 'visible') poll = setTimeout(() => load(false), 15000);
  }
  function clearView(message = '正在读取你的入场券…') {
    generation++; clearTimeout(poll); poll = null; current = null; cancelRequest = null; loading = false; cancelling = false; saving = false;
    $('ticket-experience').hidden = true; $('ticket-loading').hidden = false; $('loading-message').textContent = message;
    $('ticket-qr').removeAttribute('src'); $('ticket-qr-area').hidden = true;
    ['ticket-name', 'ticket-phone', 'ticket-number', 'ticket-price', 'ticket-title', 'ticket-subtitle', 'ticket-date', 'ticket-venue', 'ticket-address', 'checkin-time', 'ticket-message', 'cancel-error', 'payment-error'].forEach(id => { $(id).textContent = ''; });
    $('save-ticket').hidden = true; $('open-cancel').hidden = true; $('payment-action').hidden = true;
    ['refresh-ticket', 'retry-ticket', 'save-ticket', 'confirm-cancel', 'keep-ticket'].forEach(id => { $(id).disabled = false; });
    $('save-ticket').textContent = '保存入场券'; $('retry-ticket').hidden = true; $('ticket-sign-in').hidden = true;
    if ($('cancel-dialog').open) cancelDialog.close();
  }
  function loginGate() {
    $('loading-message').textContent = '登录报名时的账号，查看这份订单与入场券。';
    $('ticket-sign-in').hidden = false; $('retry-ticket').hidden = true; $('back-events').hidden = false;
  }
  async function load(manual = false) {
    if (loading || cancelling) return; const mode = access(), version = generation, identity = portal.identityVersion();
    if (!mode.valid) { $('loading-message').textContent = '请从“我的报名”打开有效的订单或旧票链接。'; $('back-events').hidden = false; return; }
    if (mode.member && !portal.isSignedIn()) { loginGate(); return; }
    $('ticket-sign-in').hidden = true;
    loading = true; $('refresh-ticket').disabled = true; $('retry-ticket').disabled = true;
    if (manual) $('ticket-message').textContent = '';
    try {
      const ticket = mode.member ? await portal.api(`/leanclub/orders/get?id=${mode.id}`, { auth: true }) : await api('/api/campaign/events/ticket', { ticketToken: mode.secret });
      if (!unchanged(version, mode.key, identity)) return;
      if (mode.member && (ticket.accessMode !== 'member' || ticket.order?.id !== mode.id)) throw new Error('订单归属未能确认，请重新打开“我的报名”。');
      render(ticket); if (manual) $('ticket-message').textContent = '状态已更新。';
    } catch (error) {
      if (!unchanged(version, mode.key, identity)) return;
      if (mode.member && [403, 404].includes(error.code)) { clearView(error.message); $('back-events').hidden = false; return; }
      if (current) $('ticket-message').textContent = error.message; else { $('loading-message').textContent = error.message; $('retry-ticket').hidden = false; $('back-events').hidden = false; }
    }
    finally {
      if (unchanged(version, mode.key, identity)) {
        loading = false; $('refresh-ticket').disabled = false; $('retry-ticket').disabled = false;
        if (['pending_payment', 'closing', 'refund_pending'].includes(current?.order?.status) && document.visibilityState === 'visible') { clearTimeout(poll); poll = setTimeout(() => load(false), 15000); }
      }
    }
  }
  $('refresh-ticket').addEventListener('click', () => load(true)); $('retry-ticket').addEventListener('click', () => load(true));
  $('open-cancel').addEventListener('click', () => { if (!current?.canCancel) return; $('cancel-error').hidden = true; cancelDialog.open(); });
  $('keep-ticket').addEventListener('click', () => { if (!cancelling) cancelDialog.close(); });
  $('cancel-dialog').addEventListener('cancel', event => { if (cancelling) event.preventDefault(); });
  $('confirm-cancel').addEventListener('click', async () => {
    if (cancelling || !current?.canCancel) return;
    const mode = access(), identity = portal.identityVersion();
    if (mode.member && !portal.isSignedIn()) return;
    // A GET started before cancellation must not restore a now-invalid paid QR afterwards.
    const version = ++generation; loading = false;
    if (!cancelRequest || cancelRequest.key !== mode.key) cancelRequest = { key: mode.key, requestId: randomId() };
    cancelling = true; clearTimeout(poll); $('confirm-cancel').disabled = true; $('keep-ticket').disabled = true; $('cancel-error').hidden = true;
    $('ticket-qr').removeAttribute('src'); $('ticket-qr-area').hidden = true; $('save-ticket').hidden = true;
    try {
      const result = mode.member ? await portal.api('/leanclub/orders/cancel', { auth: true, body: { id: mode.id, requestId: cancelRequest.requestId } }) : await api('/api/campaign/events/cancel', { ticketToken: mode.secret });
      if (!unchanged(version, mode.key, identity)) return;
      if (mode.member && (result.accessMode !== 'member' || result.order?.id !== mode.id)) throw new Error('订单结果未确认，请刷新后核对。');
      render(result); cancelDialog.close(); $('ticket-message').textContent = result.order?.status === 'refund_pending' ? '已提交退款申请，请留意后续进度。' : result.order?.status === 'closing' ? '正在确认订单关闭，请稍后刷新。' : '订单状态已更新。';
    } catch (error) { if (unchanged(version, mode.key, identity)) { $('cancel-error').textContent = error.message; $('cancel-error').hidden = false; $('ticket-message').textContent = '取消结果尚未确认，请重试或刷新订单。'; } }
    finally { if (unchanged(version, mode.key, identity)) { cancelling = false; $('confirm-cancel').disabled = false; $('keep-ticket').disabled = false; $('refresh-ticket').disabled = false; $('retry-ticket').disabled = false; } }
  });
  // The image is assembled entirely in this browser. Ticket credentials never leave this origin.
  function imageSource(src) { return new Promise((resolve, reject) => { const image = new Image(); image.onload = () => resolve(image); image.onerror = reject; image.src = src; }); }
  function wrap(ctx, value, width) {
    const lines = []; let line = '';
    for (const character of String(value || '')) { if (character === '\n') { lines.push(line); line = ''; continue; } if (ctx.measureText(line + character).width > width && line) { lines.push(line); line = character; } else line += character; }
    if (line) lines.push(line); return lines;
  }
  async function downloadTicket() {
    if (saving || cancelling || $('save-ticket').hidden || !isValid(current)) return; saving = true; $('save-ticket').disabled = true; $('save-ticket').textContent = '正在生成…'; $('ticket-message').textContent = '';
    const ticket = current, version = generation, mode = access(), identity = portal.identityVersion();
    const stillCurrent = () => unchanged(version, mode.key, identity) && current === ticket && isValid(current);
    try {
      await document.fonts.ready;
      if (!stillCurrent()) return;
      const [photo, qr] = await Promise.all([imageSource(poster(ticket.event)), imageSource(ticket.qrDataUrl)]);
      if (!stillCurrent()) return;
      const canvas = document.createElement('canvas'); canvas.width = 1000; const ctx = canvas.getContext('2d');
      ctx.font = '600 56px Space, sans-serif'; const titles = wrap(ctx, ticket.event.title, 840);
      ctx.font = '27px Space, sans-serif';
      const fields = [['活动时间', date(ticket.event.startsAt)], ['活动地点', ticket.event.venue], ['详细地址', ticket.event.address], ['参与者', `${ticket.registration.name} · ${ticket.registration.phoneMasked}`]];
      if (ticket.order) fields.push(['报名费用', money(ticket.order.amountFen)]);
      const coverHeight = Math.max(600, 360 + titles.length * 68);
      canvas.height = coverHeight + 670 + fields.reduce((sum, field) => sum + 63 + wrap(ctx, field[1], 840).length * 37, 0) + 100;
      const height = canvas.height; ctx.fillStyle = '#f7f8ee'; ctx.fillRect(0, 0, 1000, height);
      const scale = Math.max(1000 / photo.width, coverHeight / photo.height);
      ctx.save(); ctx.beginPath(); ctx.rect(0, 0, 1000, coverHeight); ctx.clip(); ctx.drawImage(photo, (1000 - photo.width * scale) / 2, (coverHeight - photo.height * scale) * .05, photo.width * scale, photo.height * scale);
      const shade = ctx.createLinearGradient(0, 0, 0, coverHeight); shade.addColorStop(0, '#07101855'); shade.addColorStop(.4, '#07101811'); shade.addColorStop(1, '#071018ed'); ctx.fillStyle = shade; ctx.fillRect(0, 0, 1000, coverHeight); ctx.restore();
      ctx.fillStyle = '#fffef8'; ctx.font = '600 24px Space, sans-serif'; ctx.fillText('LEAN SCHOOL / OFFLINE', 80, 70);
      ctx.fillStyle = '#dfff00'; ctx.font = '24px Space, sans-serif'; ctx.fillText(cityNames[ticket.event.city] || 'LEAN SCHOOL', 80, coverHeight - 70 - titles.length * 68);
      ctx.fillStyle = '#fffef8'; ctx.font = '600 56px Space, sans-serif'; titles.forEach((line, index) => ctx.fillText(line, 80, coverHeight - 50 - (titles.length - 1 - index) * 68));
      ctx.fillStyle = '#1b2a21'; ctx.font = '22px Space, sans-serif'; ctx.fillText('ADMIT ONE', 80, coverHeight + 65); ctx.textAlign = 'right'; ctx.fillText(ticket.registration.status === 'checked_in' ? '已核销' : '待入场', 920, coverHeight + 65); ctx.textAlign = 'left';
      ctx.imageSmoothingEnabled = false; ctx.drawImage(qr, 300, coverHeight + 100, 400, 400); ctx.imageSmoothingEnabled = true;
      ctx.textAlign = 'center'; ctx.font = '22px Space, sans-serif'; ctx.fillText(ticket.registration.ticketNumber, 500, coverHeight + 550); ctx.font = '18px Space, sans-serif'; ctx.fillStyle = '#738067'; ctx.fillText('入场时向工作人员出示 · 请勿公开分享', 500, coverHeight + 590); ctx.textAlign = 'left';
      let y = coverHeight + 670;
      const line = (label, content, color = '#263327') => { ctx.font = '18px Space, sans-serif'; ctx.fillStyle = '#77806e'; ctx.fillText(label, 80, y); y += 38; ctx.font = '27px Space, sans-serif'; ctx.fillStyle = color; wrap(ctx, content, 840).forEach(value => { ctx.fillText(value, 80, y); y += 37; }); y += 25; };
      fields.forEach(field => line(...field));
      ctx.strokeStyle = '#d8dccd'; ctx.beginPath(); ctx.moveTo(80, height - 75); ctx.lineTo(920, height - 75); ctx.stroke(); ctx.font = '17px Space, sans-serif'; ctx.fillStyle = '#77806e'; ctx.fillText('薄肌学院', 80, height - 35); ctx.textAlign = 'right'; ctx.fillText('LESS NOISE. MORE LIFE.', 920, height - 35);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png')); if (!blob) throw new Error('无法保存图片，请使用浏览器打印保存。');
      if (!stillCurrent()) return;
      const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `LEAN-CLUB-${ticket.registration.ticketNumber.replace(/[^a-zA-Z0-9-]/g, '')}.png`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 60000);
      $('ticket-message').textContent = '入场券已生成。请在下载中查看并保存到相册。';
    } catch (error) { if (stillCurrent()) $('ticket-message').textContent = error.message || '图片保存失败，可使用浏览器打印保存入场券。'; }
    finally { if (unchanged(version, mode.key, identity)) { saving = false; $('save-ticket').disabled = false; $('save-ticket').textContent = '保存入场券'; } }
  }
  $('save-ticket').addEventListener('click', downloadTicket);
  document.addEventListener('visibilitychange', () => { clearTimeout(poll); if (document.visibilityState === 'visible') load(); });
  $('ticket-sign-in').addEventListener('click', () => portal.requireMember(() => load()));
  window.addEventListener('leanclub:identity', () => {
    clearView('账户状态已改变。');
    if (access().member) load(); else { $('loading-message').textContent = '账户状态已改变。请重新读取这张旧票。'; $('retry-ticket').hidden = false; $('back-events').hidden = false; }
  });
  window.addEventListener('hashchange', () => { clearView(); load(); });
  window.addEventListener('popstate', () => { clearView(); load(); });
  load();
})();
