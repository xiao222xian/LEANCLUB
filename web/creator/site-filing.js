/* Filing belongs to a verified host, not to the shared brand or CMS snapshot. */
(() => {
  'use strict';
  const verifiedHosts = Object.freeze({
    'boji.tensorbot.xyz': '辽ICP备2026010731号-1'
  });
  function render() {
    const footer = document.getElementById('site-filing');
    if (!footer) return;
    const label = verifiedHosts[window.location.hostname] || '';
    const link = footer.querySelector('a');
    link.textContent = label;
    footer.hidden = !label;
    if (!label && document.body.dataset.siteFiling === 'vote') delete document.body.dataset.siteFiling;
  }
  window.LeanSchoolFiling = Object.freeze({ render });
  render();
})();
