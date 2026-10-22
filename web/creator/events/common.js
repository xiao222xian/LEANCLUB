(() => {
  'use strict';
  const cityNames = { shenzhen: '深圳', zhuhai: '珠海', guangzhou: '广州' };
  const formatter = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
  let bootstrapPromise = null;
  const publishedPosters = new Map();
  async function rawApi(path, body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(path, { method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store', signal: controller.signal, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) });
      let data;
      try { data = await response.json(); } catch { throw new Error('暂时无法连接，请稍后重试。'); }
      if (!response.ok) { const error = new Error(typeof data.error === 'string' ? data.error : '操作未完成，请稍后重试。'); error.status = response.status; throw error; }
      return data;
    } catch (error) { if (error.name === 'AbortError' || error instanceof TypeError) throw new Error('网络有些不稳定，内容已保留，请重试。'); throw error; }
    finally { clearTimeout(timeout); }
  }
  async function identity() {
    if (!bootstrapPromise) bootstrapPromise = rawApi('/api/campaign').then(data => { (data.cities || []).forEach(city => publishedPosters.set(city.id, city)); return data; }).catch(error => { bootstrapPromise = null; throw error; });
    return bootstrapPromise;
  }
  async function api(path, body) { await identity(); return rawApi(path, body); }
  function randomId() {
    const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const h = [...bytes].map(b => b.toString(16).padStart(2, '0')).join('');
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  }
  function date(value) { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? '时间待公布' : formatter.format(parsed); }
  function poster(event) { const city = event.posterCity || event.city; const raw = publishedPosters.get(city)?.posterUrl || event.posterUrl; return typeof raw === 'string' && raw.startsWith('/') && !raw.startsWith('//') ? raw : `/city-vote/assets/alan-v1/${cityNames[city] ? city : 'shenzhen'}.webp`; }
  function money(cents) { return new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY', minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(Number(cents || 0) / 100); }
  function dialog(element, opener) {
    let returnFocus;
    element.addEventListener('click', e => { if (e.target !== element) return; const r = element.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) element.close(); });
    element.addEventListener('close', () => { document.body.classList.remove('has-dialog'); (returnFocus || opener)?.focus(); });
    return { open() { returnFocus = document.activeElement; element.showModal(); document.body.classList.add('has-dialog'); element.querySelector('[tabindex="-1"]')?.focus(); }, close() { element.close(); } };
  }
  function ticketUrl(token) { return `/ticket#ticket=${encodeURIComponent(token)}`; }
  function text(tag, content, className) { const node = document.createElement(tag); node.textContent = content; if (className) node.className = className; return node; }
  window.LeanEvents = { api, randomId, date, poster, dialog, ticketUrl, text, cityNames, money };
})();
