(() => {
  'use strict';
  const portal = window.LeanClubPortal, form = document.querySelector('#club-form');
  const submit = document.querySelector('#apply-submit'), status = document.querySelector('#form-error');
  let school = null, submitting = false;
  function selectSchool(item) {
    school = item;
    document.querySelector('#selected-school').textContent = item?.name || '选择你的学校';
    if (item && !form.elements.name.value.trim()) form.elements.name.value = '薄肌学院 · ' + item.name;
  }
  document.querySelector('#choose-school').addEventListener('click', () => portal.chooseSchool(school?.name || '', selectSchool));
  if (new URLSearchParams(location.hash.slice(1)).has('edit')) {
    document.querySelector('#legacy-application-note').hidden = false;
    history.replaceState(null, '', location.pathname + location.search);
  }
  const schoolId = new URLSearchParams(location.search).get('schoolId');
  if (schoolId && /^\d+$/.test(schoolId)) {
    portal.api('/leanclub/school/get?id=' + encodeURIComponent(schoolId)).then(selectSchool).catch(() => { status.textContent = '预选学校暂时无法读取，请重新选择。'; });
  }
  const updateButton = () => { if (!submitting) submit.textContent = portal.isSignedIn() ? '提交申请 ↗' : '登录并提交申请 ↗'; };
  updateButton(); window.addEventListener('leanclub:identity', updateButton);
  form.addEventListener('submit', event => {
    event.preventDefault(); status.textContent = ''; status.classList.remove('is-error');
    if (submitting || !form.reportValidity()) return;
    if (!school) { status.textContent = '请先选择学校。'; document.querySelector('#choose-school').focus(); return; }
    portal.requireMember(async () => {
      if (submitting || !form.reportValidity() || !school) return;
      submitting = true; submit.disabled = true; submit.textContent = '正在提交…';
      try {
        const data = {
          schoolId: school.id, name: form.elements.name.value.trim(), introduction: form.elements.introduction.value.trim(),
          contactName: form.elements.contactName.value.trim(), contactValue: form.elements.contactValue.value.trim(),
          publicContactConsent: form.elements.publicContactConsent.checked
        };
        await portal.api('/leanclub/club/apply', { auth: true, body: data });
        form.hidden = true; document.querySelector('#success').hidden = false;
        document.querySelector('#success').scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion:reduce)').matches ? 'instant' : 'smooth' });
      } catch (error) { status.textContent = error.message; status.classList.add('is-error'); }
      finally { submitting = false; submit.disabled = false; updateButton(); }
    });
  });
})();
