(() => {
  'use strict';
  if (!['/', '/index.html', '/lean-creator-home.html'].includes(location.pathname)) return;
  let sent = false;
  let campaignReady = false;
  let wasVisible = document.visibilityState === 'visible';
  function recordPageView() {
    wasVisible = wasVisible || document.visibilityState === 'visible';
    if (sent || !wasVisible || !campaignReady) return;
    sent = true;
    document.removeEventListener('visibilitychange', recordPageView);
    const bytes = new Uint8Array(16);
    if (window.crypto?.getRandomValues) crypto.getRandomValues(bytes);
    else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map(b => b.toString(16).padStart(2, '0')).join('');
    const eventId = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    fetch('/api/analytics/page', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({page: 'home', eventId}),
      keepalive: true,
    }).catch(() => {});
  }
  document.addEventListener('visibilitychange', recordPageView);
  // Campaign GET may establish the same anonymous visitor cookie. Sequence the
  // first analytics write after it so two simultaneous responses cannot race.
  Promise.resolve(window.leanCampaignReady).finally(() => {
    campaignReady = true;
    recordPageView();
  });
})();
