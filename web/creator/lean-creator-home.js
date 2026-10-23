// Preserve the photography and marquees; school identity lives in the public portal.
(() => {
  'use strict';
  const header = document.querySelector('.masthead');
  const menu = header.querySelector('.menu');
  const closeMenu = () => { header.classList.remove('menu-open'); menu.setAttribute('aria-expanded', 'false'); };
  menu.addEventListener('click', () => { const open = header.classList.toggle('menu-open'); menu.setAttribute('aria-expanded', String(open)); });
  header.querySelector('nav').addEventListener('click', event => { if (event.target.closest('a,button')) closeMenu(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeMenu(); });
  let legacyDirectory = null;
  document.querySelectorAll('[data-school]').forEach(button => button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      if (!legacyDirectory) legacyDirectory = fetch('/api/club/schools', { cache: 'no-store', signal: AbortSignal.timeout(6000) })
        .then(response => response.ok ? response.json() : Promise.reject())
        .then(data => Array.isArray(data.schools) ? data.schools : []);
      const schools = await legacyDirectory;
      const existing = schools.find(school => school.name === button.dataset.school);
      location.assign(existing ? '/schools?school=' + encodeURIComponent(existing.id) : '/schools?keyword=' + encodeURIComponent(button.dataset.school));
    } catch (_) {
      legacyDirectory = null;
      location.assign('/schools?keyword=' + encodeURIComponent(button.dataset.school));
    } finally { button.disabled = false; }
  }));
  for (const [selector, section, label] of [['.marquee-toggle', '.campus-marquee', '学校滚动'], ['.voice-toggle', '.rolling-voices', '人物滚动']]) {
    document.querySelector(selector)?.addEventListener('click', event => {
      const paused = document.querySelector(section).classList.toggle('paused');
      event.currentTarget.setAttribute('aria-pressed', String(paused));
      event.currentTarget.setAttribute('aria-label', (paused ? '继续' : '暂停') + label);
    });
  }
})();
