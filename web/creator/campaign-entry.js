(() => {
  'use strict';
  const posters = new Map([...document.querySelectorAll('[data-campaign-city]')]
    .map(link => [link.dataset.campaignCity, link]));
  if (!posters.size) return;

  // Read shared campaign artwork without recording a vote-page visit.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 6000);
  window.leanCampaignReady = fetch('/api/campaign', {credentials: 'same-origin', cache: 'no-store', signal: controller.signal})
    .then(response => response.ok ? response.json() : Promise.reject())
    .then(data => {
      if (!Array.isArray(data.cities)) return;
      for (const city of data.cities) {
        const link = posters.get(city.id);
        if (!link || typeof city.posterUrl !== 'string') continue;
        const url = new URL(city.posterUrl, location.origin);
        if (url.origin !== location.origin || !/^\/(?:api\/media\/|city-vote\/assets\/)/.test(url.pathname)) continue;
        const image = link.querySelector('img');
        const fallback = image.src;
        image.addEventListener('error', () => { image.src = fallback; }, {once: true});
        const x = Number(city.positionX);
        const y = Number(city.positionY);
        if (Number.isFinite(x)) {
          const safeX = Math.max(0, Math.min(100, x));
          link.style.setProperty('--poster-x', `${safeX}%`);
          // Center default portraits in narrow columns; honor any edited focal point.
          const defaultX = city.id === 'guangzhou' ? 65 : 50;
          const mobileX = city.isDefault && safeX === defaultX ? ({guangzhou: 40, zhuhai: 38}[city.id] ?? safeX) : safeX;
          link.style.setProperty('--entry-mobile-x', `${mobileX}%`);
        }
        if (Number.isFinite(y)) link.style.setProperty('--poster-y', `${Math.max(0, Math.min(100, y))}%`);
        image.src = url.href;
      }
    })
    .catch(() => {})
    .finally(() => clearTimeout(timeout));

  const header = document.querySelector('.masthead');
  const menu = header?.querySelector('.menu');
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !header?.classList.contains('menu-open')) return;
    header.classList.remove('menu-open');
    menu.setAttribute('aria-expanded', 'false');
    menu.focus();
  });
  document.addEventListener('pointerdown', event => {
    if (!header?.classList.contains('menu-open') || header.contains(event.target)) return;
    header.classList.remove('menu-open');
    menu.setAttribute('aria-expanded', 'false');
  });
})();
