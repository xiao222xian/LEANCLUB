const $ = s => document.querySelector(s);
const nf = new Intl.NumberFormat('en-US');
const rows = $('#school-rows'), search = $('#school-search'), region = $('#region');
const panel = $('.school-panel');
let openedSchool = null;
const shortNames = {'北京师范大学—香港浸会大学联合国际学院':'北师港浸大（UIC）','苏黎世联邦理工大学 & 苏黎世大学':'苏黎世联邦理工大学 & 苏黎世大学'};
const escapeHtml = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function refreshRegions(){
 const selected=region.value;
 region.querySelectorAll('option:not(:first-child)').forEach(option=>option.remove());
 [...new Set(schools.map(s=>s.region).filter(Boolean))].forEach(r=>{const option=document.createElement('option');option.value=r;option.textContent=r;region.append(option);});
 region.value=selected;
}
refreshRegions();
function render() {
 const q = search.value.trim().toLocaleLowerCase();
 const filtered = schools.filter(s => (!region.value || s.region === region.value) && `${s.name} ${s.en} ${s.alias}`.toLocaleLowerCase().includes(q));
 rows.innerHTML = filtered.map(s=>`<article class="campus-entry" role="listitem"><button class="school-button" data-school="${s.id}"><span class="entry-rank">${s.rank}.</span><span class="school-label"><strong>${escapeHtml(shortNames[s.name] || s.name)}</strong><small>${escapeHtml(s.en)}</small><span class="entry-tags"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M3 3h6l8 8-6 6-8-8z"/><circle cx="6.6" cy="6.6" r="1"/></svg>${escapeHtml(s.region)}<i>·</i>LEAN SCHOOL</span></span></button><div class="entry-actions"><div class="member-count" aria-label="人数 ${s.count}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6m2 3a5 5 0 0 1 3 4v2"/></svg><strong>${nf.format(s.count)}</strong><small>人数</small></div><button class="entry-button" data-school="${s.id}" aria-label="查看${escapeHtml(s.name)}俱乐部"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M6 18 18 6M6 6h12v12"/></svg><span>俱乐部</span></button></div></article>`).join('');
 $('#result-count').textContent = (q || region.value) ? `找到 ${filtered.length} 所学校与组织` : '';
 $('#empty-state').hidden = filtered.length > 0;
 $('#clear-search').hidden = !search.value;
 $('.search-box kbd').hidden = !!search.value;
}
function paintSchool(s, club, problem='', loading=false) {
 const qr=club && club.qr ? club.qr : 'lean-default-douyin.jpg';
 const contact=club ? `<section class="school-contact"><h3>负责人联系方式</h3><p>${escapeHtml(club.contact)}</p></section>` : '';
 $('#school-content').innerHTML = `<p class="eyebrow muted">YOUR CAMPUS / LEAN SCHOOL</p><h2 id="panel-title">${escapeHtml(shortNames[s.name] || s.name)}</h2>${s.en?`<p class="panel-en">${escapeHtml(s.en)}</p>`:''}${s.region?`<span class="panel-region">${escapeHtml(s.region)}</span>`:''}<div class="panel-stats"><div><span>排名</span><strong>#${s.rank}</strong></div><div><span>人数</span><strong>${nf.format(s.count)}<small>人</small></strong></div></div>${loading?`<div class="profile-loading" role="status" aria-live="polite"><span class="profile-placeholder"></span><span class="profile-placeholder short"></span><p>正在加载俱乐部信息…</p></div>`:problem?`<div role="alert" class="profile-error"><p>${escapeHtml(problem)}</p><button type="button" class="light-button" id="retry-profile">重新加载</button></div>`:contact+`<section class="contact-card club-douyin"><h3>了解俱乐部</h3><p class="douyin-subtitle">${club && club.qr?'本校俱乐部抖音':'抖音 · 官方账号'}</p><img class="douyin-code" decoding="async" src="${escapeHtml(qr)}" alt="俱乐部抖音二维码"><p class="douyin-hint">长按保存图片，打开抖音扫一扫</p>${club && club.qr?'':'<span class="default-account-note">本校暂未上传，当前展示联盟抖音账号</span>'}</section>`}`;
}
let profileRequest = null;
async function showSchool(s) {
 if(!s)return;
 profileRequest?.abort();
 const request=new AbortController();profileRequest=request;
 openedSchool=s;
 paintSchool(s,null,'',true);
 if(!panel.open)panel.showModal();
 const timer=setTimeout(()=>request.abort(),10000);
 try {
  const response=await fetch('/api/club/profile/'+encodeURIComponent(s.id),{cache:'no-store',signal:request.signal});
  const data=await response.json();
  if(profileRequest!==request||!panel.open)return;
  if(!response.ok){paintSchool(s,null,data.error||'暂时无法读取，请稍后再试。');return;}
  paintSchool(s,data.club);
 }catch(e){if(profileRequest===request&&panel.open)paintSchool(s,null,'联系方式暂时无法加载，请重试。');}
 finally{clearTimeout(timer);}
}
panel.addEventListener('close',()=>{profileRequest?.abort();profileRequest=null;openedSchool=null;});
$('#school-content').addEventListener('click',e=>{if(e.target.closest('#retry-profile')&&openedSchool)showSchool(openedSchool);});
rows.addEventListener('click', e=>{const button=e.target.closest('[data-school]');if(button)showSchool(schools.find(s=>s.id===button.dataset.school));});
search.addEventListener('input',render);region.addEventListener('change',render);
$('#clear-search').addEventListener('click',()=>{search.value='';render();search.focus();});
$('#reset-filters').addEventListener('click',()=>{search.value='';region.value='';render();search.focus();});
$('.close-panel').addEventListener('click',()=>panel.close());
panel.addEventListener('click',e=>{if(e.target===panel){const r=panel.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)panel.close();}});
document.querySelectorAll('[data-find]').forEach(b=>b.addEventListener('click',()=>{search.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth',block:'center'});search.focus({preventScroll:true});}));
document.addEventListener('keydown',e=>{if(e.key==='/' && !panel.open && !['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)){e.preventDefault();search.focus();}});
async function loadPublished(){
 try {
  const response=await fetch('/api/club/schools',{cache:'no-store'});
  if(!response.ok)throw new Error('unavailable');
  const {published}=await response.json();
  for(const club of published){
   let school=schools.find(s=>s.id===club.id);
   if(!school){school={id:club.id,name:club.name,en:'',region:'',alias:'',count:club.count};schools.push(school);}
   school.count=club.count;
   for(const field of ['en','region','alias'])if(typeof club[field]==='string')school[field]=club[field];
  }
  if(published.length){schools.sort((a,b)=>b.count-a.count);let previous=null,rank=0;schools.forEach((s,i)=>{if(s.count!==previous)rank=i+1;s.rank=rank;previous=s.count;});}
 }catch(e){/* Retain the existing directory if the API is temporarily unavailable. */}
 refreshRegions();render();
 const direct=new URLSearchParams(location.search).get('school');
 if(direct){const s=schools.find(s=>s.id===direct||s.name===direct);if(s)showSchool(s);}
}
render();loadPublished();
