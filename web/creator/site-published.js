/* Fixed editorial template; only a published server snapshot can change public content. */
(() => {
  'use strict';
  const portal = window.LeanClubPortal;
  if (!portal) return;
  const { node, safeHref } = portal;
  function text(selector, value) {
    if (typeof value === 'string') document.querySelectorAll(selector).forEach(el => { el.textContent = value; });
  }
  function href(selector, value) {
    const safe = safeHref(value);
    if (!safe) return;
    document.querySelectorAll(selector).forEach(el => { el.href = safe; el.rel = 'noopener noreferrer'; });
  }
  function image(selector, value, mobileValue = '') {
    const safe = safeHref(value, true), mobile = safeHref(mobileValue, true);
    const target = document.querySelector(selector); if (!safe || !target) return;
    const preload = new Image(); preload.referrerPolicy = 'no-referrer';
    preload.addEventListener('load', () => {
      const picture = target.closest('picture');
      if (picture) {
        picture.querySelectorAll('source').forEach(el => el.remove());
        if (mobile) { const source = node('source'); source.media = '(max-width: 760px)'; source.srcset = mobile; picture.prepend(source); }
      }
      target.removeAttribute('srcset'); target.src = safe; target.referrerPolicy = 'no-referrer';
    }, { once: true });
    preload.src = mobile && matchMedia('(max-width: 760px)').matches ? mobile : safe;
  }
  function voices(list) {
    const section = document.querySelector('.rolling-voices'), track = section?.querySelector('.voice-track');
    if (!section || !track || !Array.isArray(list)) return;
    section.hidden = !list.length;
    if (!list.length) return;
    const group = node('div', 'voice-group');
    list.slice(0, 12).forEach(item => {
      if (typeof item.name !== 'string' || typeof item.quote !== 'string') return;
      const card = node('article', 'voice-card'), avatar = node('div', 'alan-avatar');
      const src = safeHref(item.image, true);
      if (src) { const img = node('img'); img.src = src; img.alt = item.name; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer'; img.width = 58; img.height = 58; avatar.append(img); }
      else { avatar.classList.add('portal-voice-initial'); avatar.textContent = item.name.slice(0, 1); }
      const bottom = node('div', 'voice-bottom'); bottom.append(node('span', 'voice-name', item.name));
      const source = safeHref(item.source);
      if (source) { const link = node('a', 'portal-voice-source', '来源 ↗'); link.href = source; link.target = '_blank'; link.rel = 'noopener noreferrer'; bottom.append(link); }
      card.append(avatar, node('p', 'voice-text', item.quote), bottom); group.append(card);
    });
    const mirror = group.cloneNode(true); mirror.setAttribute('aria-hidden', 'true'); mirror.inert = true;
    track.replaceChildren(group, mirror);
  }
  window.leanSitePublishedReady = portal.api('/leanclub/site/published').then(snapshot => {
    if (!snapshot) { document.body.dataset.contentState = 'built-in'; return; }
    const data = snapshot.content;
    if (!data || data.schemaVersion !== 1 || typeof data.hero !== 'object') throw new Error('Invalid published schema');
    const hero = data.hero;
    text('.hero h1', hero.title); text('.hero-tagline', hero.subtitle); text('[data-hero-eyebrow]', hero.eyebrow);
    image('.hero-photo', hero.image, hero.mobileImage);
    text('[data-hero-primary]', hero.primaryLabel); href('[data-hero-primary]', hero.primaryHref);
    text('[data-hero-secondary]', hero.secondaryLabel); href('[data-hero-secondary]', hero.secondaryHref);
    if (Array.isArray(data.navigation)) {
      const nav = document.querySelector('#home-navigation');
      if (nav) {
        nav.querySelectorAll('a').forEach(el => el.remove());
        const items = document.createDocumentFragment();
        data.navigation.slice(0, 6).forEach(item => { const safe = safeHref(item.href); if (!safe || !item.label) return; const link = node('a', '', item.label); link.href = safe; link.rel = 'noopener noreferrer'; items.append(link); });
        nav.prepend(items);
      }
    }
    if (data.campaign) {
      document.querySelectorAll('.campaign-entry,.hero-campaign').forEach(el => { el.hidden = data.campaign.enabled === false; });
      text('#campaign-entry-title,.hero-campaign-title', data.campaign.title);
      text('.campaign-entry-description', data.campaign.description);
      text('.campaign-entry-action,.hero-campaign-action', data.campaign.buttonLabel);
      href('.campaign-entry-action,.hero-campaign', data.campaign.href);
    }
    if (data.schoolSection) {
      const section = document.querySelector('.campus-marquee'); if (section) section.hidden = data.schoolSection.enabled === false;
      text('[data-school-title]', data.schoolSection.title); text('[data-school-description]', data.schoolSection.description); text('[data-school-button]', data.schoolSection.buttonLabel);
    }
    voices(data.voices);
    if (data.footer) {
      text('.portal-footer-brand', data.footer.brand); text('[data-footer-description]', data.footer.description); text('[data-footer-copyright]', data.footer.copyright);
      text('[data-footer-contact]', data.footer.contactLabel); href('[data-footer-contact]', data.footer.contactHref);
      // Shared CMS content cannot assert that another domain has been filed.
      window.LeanSchoolFiling?.render();
    }
    document.body.dataset.contentState = 'published'; document.body.dataset.contentVersion = String(snapshot.version);
  }).catch(() => { document.body.dataset.contentState = 'unavailable'; });
})();
