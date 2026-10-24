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
    <div class="group-qr-frame"><img class="group-qr-image" alt="活动微信群二维码" draggable="false"></div>
    <a class="group-save" download="薄肌学院活动群.png">保存二维码 <span aria-hidden="true">↓</span></a>`;
  document.body.append(dialog);
  const image = dialog.querySelector('.group-qr-image'), save = dialog.querySelector('.group-save');
  let focus, overflow, version = 0, expiryTimer, onSave;
  dialog.querySelector('.group-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', e => {
    const r = dialog.getBoundingClientRect();
    if (e.target === dialog && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)) dialog.close();
  });
  dialog.addEventListener('close', () => {
    version++; clearTimeout(expiryTimer); document.documentElement.style.overflow = overflow ?? '';
    image.removeAttribute('src'); save.removeAttribute('href'); onSave = null;
    if (focus?.isConnected) focus.focus({ preventScroll: true });
  });
  save.addEventListener('click', () => onSave?.());
  window.LeanGroupInvite = {
    async open(group, { preview = false, saved, expired, shouldOpen } = {}) {
      if (dialog.open) return false;
      const current = ++version, url = new URL(group.imageUrl, location.href);
      if (url.origin !== location.origin || !['http:', 'https:', 'blob:'].includes(url.protocol)) return false;
      const preload = new Image(); preload.src = url.href;
      let timer;
      try { await Promise.race([preload.decode(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('image timeout')), 6000); })]); }
      catch { return false; }
      finally { clearTimeout(timer); }
      if (current !== version || document.querySelector('dialog[open]') || document.visibilityState === 'hidden' || shouldOpen && !shouldOpen()) return false;
      if (!preview && group.expiresAt && group.expiresAt * 1000 <= Date.now()) { expired?.(); return false; }
      focus = document.activeElement; overflow = document.documentElement.style.overflow;
      document.documentElement.style.overflow = 'hidden';
      image.src = url.href; save.href = url.href; onSave = saved;
      image.onerror = () => { if (dialog.open) dialog.close(); expired?.(); };
      dialog.querySelector('.group-preview-label').hidden = !preview;
      dialog.showModal();
      const checkExpiry = () => {
        if (group.expiresAt && group.expiresAt * 1000 <= Date.now()) { dialog.close(); expired?.(); }
        else if (group.expiresAt) expiryTimer = setTimeout(checkExpiry, Math.min(60000, group.expiresAt * 1000 - Date.now()));
      };
      if (!preview) checkExpiry();
      return true;
    },
    close: () => { version++; if (dialog.open) dialog.close(); }
  };
})();
