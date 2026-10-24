(() => {
  'use strict';
  const entry = document.querySelector('#open-group-invite');
  const memory = new Set();
  let busy = false, expiryTimer;
  const key = g => 'lean-group-seen:' + g.id + ':' + g.imageVersion;
  const seen = g => { try { return memory.has(key(g)) || localStorage.getItem(key(g)) === '1'; } catch { return memory.has(key(g)); } };
  function mark(g) { memory.add(key(g)); try { localStorage.setItem(key(g), '1'); } catch { /* In-memory fallback. */ } }
  function track(group, kind) {
    const eventId = Array.from(crypto.getRandomValues(new Uint8Array(16)), x => x.toString(16).padStart(2, '0')).join('');
    fetch('/api/campaign/group-qr/events', { method: 'POST', credentials: 'same-origin', keepalive: true, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: group.id, kind, eventId }) }).catch(() => {});
  }
  async function current() {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch('/api/campaign/group-qr', { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error('unavailable');
      const group = (await response.json()).group;
      clearTimeout(expiryTimer); entry.hidden = !group;
      const expire = () => {
        const remaining = group.expiresAt * 1000 - Date.now();
        if (remaining <= 0) entry.hidden = true;
        else expiryTimer = setTimeout(expire, Math.min(2147483647, remaining));
      };
      if (group?.expiresAt) expire();
      return group;
    } finally { clearTimeout(timer); }
  }
  async function open(automatic = false) {
    if (busy) return;
    busy = true; entry.disabled = true;
    try {
      const group = await current();
      if (!group || automatic && seen(group)) return;
      const opened = await window.LeanGroupInvite.open(group, {
        shouldOpen: () => !automatic || !seen(group),
        saved: () => track(group, 'save'), expired: () => { entry.hidden = true; }
      });
      if (opened) { mark(group); track(group, 'view'); }
      else if (!automatic) entry.textContent = '二维码暂不可用，重试';
    } catch { if (!automatic) entry.textContent = '暂未连接，点此重试'; }
    finally { busy = false; entry.disabled = false; }
  }
  entry.addEventListener('click', () => { entry.textContent = '加入活动群 ↗'; open(); });
  window.addEventListener('lean:vote-saved', () => open(true));
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') current().catch(() => {}); });
  current().catch(() => {});
})();
