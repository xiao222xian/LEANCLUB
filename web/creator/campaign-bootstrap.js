(() => {
  'use strict';
  // Starts in <head>, independently of visitor identity and the page's larger scripts.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  window.LeanCampaignPresentation = fetch('/api/campaign/presentation', {
    credentials: 'omit', cache: 'default', priority: 'high', signal: controller.signal
  }).then(response => response.ok ? response.json() : null)
    .catch(() => null).finally(() => clearTimeout(timer));
  window.LeanCampaignPresentation.then(data => {
    const group = data?.group;
    if (!group || group.expiresAt && group.expiresAt * 1000 <= Date.now()) return null;
    const url = new URL(group.imageUrl, location.origin);
    if (url.origin !== location.origin || !/^\/api\/campaign\/group-qr\/[a-f0-9]{32}\/\d+$/.test(url.pathname)) return null;
    window.LeanCampaignQRWarmup = { url: url.href, promise: (async () => {
      const abort = new AbortController(), timeout = setTimeout(() => abort.abort(), 15000);
      try {
        const response = await fetch(url.href, { credentials: 'same-origin', cache: 'default', priority: 'high', signal: abort.signal });
        if (!response.ok) return null;
        const blob = await response.blob();
        return blob.type === 'image/png' ? { blob, loadedAt: Date.now() } : null;
      } catch { return null; }
      finally { clearTimeout(timeout); }
    })() };
  }).catch(() => {});
})();
