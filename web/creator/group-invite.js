(() => {
  'use strict';
  const dialog = document.createElement('dialog');
  dialog.className = 'group-invite';
  dialog.setAttribute('aria-labelledby', 'group-invite-title');
  dialog.setAttribute('aria-describedby', 'group-invite-subtitle');
  dialog.innerHTML = `<span class="group-preview-label" hidden>预览</span>
    <button type="button" class="group-close" aria-label="关闭活动群弹窗" autofocus>×</button>
    <div class="group-brand"><img src="/city-vote/assets/lean-emblem.svg" alt="">LEAN SCHOOL</div>
    <h2 class="group-title" id="group-invite-title">一起运动，<br>群里见。</h2>
    <p class="group-subtitle" id="group-invite-subtitle">扫码加入薄肌学院活动群</p>
    <div class="group-qr-frame"><img class="group-qr-image" alt="活动微信群二维码" draggable="false" hidden><span class="group-loading" role="status">正在加载二维码…</span><button type="button" class="group-retry" hidden>重新加载 ↻</button></div>
    <a class="group-save" download="薄肌学院活动群.png" hidden>保存二维码 <span aria-hidden="true">↓</span></a>`;
  document.body.append(dialog);
  const image = dialog.querySelector('.group-qr-image'), save = dialog.querySelector('.group-save');
  const loading = dialog.querySelector('.group-loading'), retryButton = dialog.querySelector('.group-retry');
  let focus, overflow, version = 0, expiryTimer, onSave, onRetry, prepared, retryAfterClose;
  function clear() {
    if (prepared) { prepared.controller.abort(); if (prepared.objectUrl) URL.revokeObjectURL(prepared.objectUrl); prepared = null; }
  }
  function prepare(group) {
    const url = new URL(group.imageUrl, location.href);
    if (url.origin !== location.origin || !['http:', 'https:'].includes(url.protocol)) return Promise.reject(new Error('invalid image URL'));
    if (prepared?.url === url.href) return prepared.promise;
    clear();
    const item = { url: url.href, controller: new AbortController() };
    prepared = item;
    item.promise = (async () => {
      const timer = setTimeout(() => item.controller.abort(), 15000);
      try {
        const warmup = window.LeanCampaignQRWarmup;
        const warm = warmup?.url === url.href ? await warmup.promise : null;
        let blob = warm && Date.now() - warm.loadedAt < 30000 ? warm.blob : null;
        if (prepared !== item || item.controller.signal.aborted) throw new Error('image changed');
        if (!blob) {
          const response = await fetch(url.href, { credentials: 'same-origin', cache: 'default', priority: 'high', signal: item.controller.signal });
          if (!response.ok) throw new Error('image unavailable');
          blob = await response.blob();
        }
        if (blob.type !== 'image/png' || prepared !== item) throw new Error('image unavailable');
        item.objectUrl = URL.createObjectURL(blob);
        const decoded = new Image(); decoded.src = item.objectUrl;
        await decoded.decode();
        if (prepared !== item) throw new Error('image changed');
        return item.objectUrl;
      } catch (error) { if (prepared === item) clear(); throw error; }
      finally { clearTimeout(timer); }
    })();
    return item.promise;
  }
  dialog.querySelector('.group-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', e => {
    const r = dialog.getBoundingClientRect();
    if (e.target === dialog && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)) dialog.close();
  });
  dialog.addEventListener('close', () => {
    version++; clearTimeout(expiryTimer); document.documentElement.style.overflow = overflow ?? '';
    image.removeAttribute('src'); save.removeAttribute('href'); onSave = null; onRetry = null;
    if (!prepared?.objectUrl || dialog.dataset.preview === 'true') clear();
    if (focus?.isConnected) focus.focus({ preventScroll: true });
    const retry = retryAfterClose; retryAfterClose = null; retry?.();
  });
  save.addEventListener('click', () => onSave?.());
  retryButton.addEventListener('click', () => { retryAfterClose = onRetry; dialog.close(); });
  window.LeanGroupInvite = {
    prepare,
    invalidate: clear,
    reset: () => { if (dialog.open) dialog.close(); clear(); },
    async open(groupOrPromise, { preview = false, saved, expired, shouldOpen, retry } = {}) {
      if (dialog.open) return false;
      if (document.querySelector('dialog[open]') || document.visibilityState === 'hidden') return false;
      const current = ++version;
      focus = document.activeElement; overflow = document.documentElement.style.overflow;
      document.documentElement.style.overflow = 'hidden';
      image.hidden = true; save.hidden = true; retryButton.hidden = true; loading.hidden = false;
      loading.textContent = '正在加载二维码…';
      dialog.dataset.preview = String(preview);
      onSave = saved; onRetry = retry || (() => window.LeanGroupInvite.open(groupOrPromise, { preview, saved, expired, shouldOpen }));
      dialog.querySelector('.group-preview-label').hidden = !preview;
      dialog.showModal();
      try {
        const group = await groupOrPromise;
        if (current !== version || !dialog.open) return false;
        if (shouldOpen && !shouldOpen(group)) { dialog.close(); return false; }
        if (!group || !preview && group.expiresAt && group.expiresAt * 1000 <= Date.now()) {
          loading.textContent = '活动群暂未开放'; retryButton.hidden = false; expired?.(); return false;
        }
        const checkExpiry = () => {
          if (group.expiresAt && group.expiresAt * 1000 <= Date.now()) { dialog.close(); expired?.(); }
          else if (group.expiresAt) expiryTimer = setTimeout(checkExpiry, Math.min(60000, group.expiresAt * 1000 - Date.now()));
        };
        if (!preview) checkExpiry();
        const source = await prepare(group);
        if (current !== version || !dialog.open) return false;
        if (!preview && group.expiresAt && group.expiresAt * 1000 <= Date.now()) { dialog.close(); expired?.(); return false; }
        image.src = source; save.href = source;
        image.hidden = false; save.hidden = false; loading.hidden = true;
        return true;
      } catch {
        if (current === version && dialog.open) { loading.textContent = '二维码暂未加载'; retryButton.hidden = false; }
        return false;
      }
    },
    close: () => { version++; if (dialog.open) dialog.close(); }
  };
})();
