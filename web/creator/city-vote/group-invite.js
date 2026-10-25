(() => {
  'use strict';
  const entry = document.querySelector('#open-group-invite');
  const label = entry.querySelector('.group-entry-label');
  const entryLabel = label.textContent;
  const memory = new Set();
  let busy = false, expiryTimer, pending, activeImage, lastGroup, checkedAt = 0, revision = 0;
  const key = g => 'lean-group-seen:' + g.id + ':' + g.imageVersion;
  const seen = g => { try { return memory.has(key(g)) || localStorage.getItem(key(g)) === '1'; } catch { return memory.has(key(g)); } };
  function mark(g) { memory.add(key(g)); try { localStorage.setItem(key(g), '1'); } catch { /* In-memory fallback. */ } }
  function track(group, kind) {
    const eventId = Array.from(crypto.getRandomValues(new Uint8Array(16)), x => x.toString(16).padStart(2, '0')).join('');
    fetch('/api/campaign/group-qr/events', { method: 'POST', credentials: 'same-origin', keepalive: true, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: group.id, kind, eventId }) }).catch(() => {});
  }
  function accept(group) {
    lastGroup = group; checkedAt = Date.now(); revision++;
    clearTimeout(expiryTimer); entry.hidden = !group;
    if (activeImage !== group?.imageUrl) {
      window.LeanGroupInvite.invalidate();
      if (!busy) window.LeanGroupInvite.close();
    }
    activeImage = group?.imageUrl;
    if (!group) return null;
    const expire = () => {
      const remaining = group.expiresAt * 1000 - Date.now();
      if (remaining <= 0) { entry.hidden = true; window.LeanGroupInvite.reset(); }
      else expiryTimer = setTimeout(expire, Math.min(2147483647, remaining));
    };
    if (group.expiresAt) expire();
    if (!entry.hidden) window.LeanGroupInvite.prepare(group).catch(() => {});
    return entry.hidden ? null : group;
  }
  function current() {
    if (pending) return pending;
    pending = fetchCurrent().finally(() => { pending = null; });
    return pending;
  }
  async function fetchCurrent() {
    const started = revision;
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch('/api/campaign/presentation', { credentials: 'omit', cache: 'default', priority: 'high', signal: controller.signal });
      if (!response.ok) throw new Error('unavailable');
      const group = (await response.json()).group;
      return started === revision ? accept(group) : lastGroup;
    } finally { clearTimeout(timer); }
  }
  async function open(automatic = false, freshGroup) {
    if (busy) return;
    if (automatic && freshGroup !== undefined && (!freshGroup || seen(freshGroup))) { accept(freshGroup); return; }
    busy = true; entry.disabled = true; entry.setAttribute('aria-busy', 'true'); label.textContent = '正在打开…';
    let selected;
    try {
      const lookup = freshGroup !== undefined ? accept(freshGroup) : Date.now() - checkedAt < 30000 ? lastGroup : current();
      const group = Promise.resolve(lookup).then(value => { selected = value; return value; });
      group.catch(() => {}); // A dialog already open elsewhere may decline this request.
      // The modal opens synchronously, even while a stale configuration refreshes.
      const opened = await window.LeanGroupInvite.open(group, {
        shouldOpen: value => !automatic || Boolean(value && !seen(value)),
        saved: () => track(selected, 'save'), expired: () => { entry.hidden = true; }, retry: () => { checkedAt = 0; open(); }
      });
      if (opened) { mark(selected); track(selected, 'view'); }
    } catch { label.textContent = '点此重试'; }
    finally { busy = false; entry.disabled = false; entry.removeAttribute('aria-busy'); label.textContent = entryLabel; }
  }
  entry.addEventListener('click', () => open());
  for (const event of ['pointerenter', 'focus']) entry.addEventListener(event, () => { if (Date.now() - checkedAt >= 20000) current().catch(() => {}); });
  window.addEventListener('lean:campaign-ready', event => {
    if (event.detail?.group === undefined) current().catch(() => {});
    else accept(event.detail.group);
  });
  window.addEventListener('lean:vote-saved', event => open(true, event.detail?.group));
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') current().catch(() => {}); });
})();
