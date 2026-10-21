(() => {
 const connection = navigator.connection;
 const warmed = new Set();
 function warm(path) {
  if (connection?.saveData || /(^|-)2g$/.test(connection?.effectiveType || '') || warmed.has(path)) return;
  warmed.add(path);
  const link = document.createElement('link');
  link.rel = 'prefetch'; link.href = path; link.as = 'document'; document.head.append(link);
 }
 const onIntent = event => {
  const anchor = event.target.closest?.('a[href]');
  if (!anchor) return;
  const url = new URL(anchor.href);
  if (url.origin === location.origin && ['/', '/schools'].includes(url.pathname) && url.pathname !== location.pathname) warm(url.pathname);
 };
 document.addEventListener('pointerover', onIntent, {passive:true});
 document.addEventListener('focusin', onIntent);
 const next = location.pathname === '/' || location.pathname.includes('creator-home') ? '/schools' : location.pathname === '/schools' || location.pathname.includes('school-ranking') ? '/' : null;
 if (next) {
  const preload = () => {
   warm(next);
   const assets = next === '/schools' ? ['/lean-school-ranking.css?v=directory-8','/lean-ranking-data.js','/lean-school-ranking.js?v=metadata-20261021'] : ['/lean-creator-home.css?v=campuses-20261021','/lean-creator-home.js?v=metadata-20261021'];
   if (!connection?.saveData && !/2g/.test(connection?.effectiveType || '')) assets.forEach(href => fetch(href, {cache:'force-cache'}).catch(() => {}));
  };
  const schedule = () => { if ('requestIdleCallback' in window) requestIdleCallback(preload,{timeout:2000}); else setTimeout(preload,800); };
  if (document.readyState === 'complete') schedule(); else window.addEventListener('load',schedule,{once:true});
 }
})();
