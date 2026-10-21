// Homepage uses the published directory and opens the live school detail.
const header = document.querySelector('.masthead');
const fallbackSchools = [["西交利物浦大学", "xjtlu 西浦 Xi’an Jiaotong-Liverpool University", "group-01"], ["北京师范大学—香港浸会大学联合国际学院", "uic 北师港浸大 Beijing Normal University–Hong Kong Baptist University United International College (UIC)", "group-02"], ["威斯康星大学麦迪逊分校", "uw madison University of Wisconsin–Madison", "group-03"], ["纽约大学", "nyu New York University", "group-04"], ["澳门科技大学", " Macau University of Science and Technology", "group-05"], ["宁波诺丁汉大学", " University of Nottingham Ningbo China", "group-06"], ["加州大学洛杉矶分校", "ucla University of California, Los Angeles", "group-07"], ["加州大学圣塔芭芭拉分校", "ucsb University of California, Santa Barbara", "group-08"], ["华南农业大学", "scau 华农 South China Agricultural University", "group-09"], ["南加州大学", "usc University of Southern California", "group-10"], ["帝国理工学院", "icl ic Imperial College London", "group-11"], ["罗格斯大学", " Rutgers University", "group-12"], ["海南大学", " Hainan University", "group-13"], ["温州肯恩大学", " Wenzhou-Kean University", "group-14"], ["都柏林大学学院", " University College Dublin", "group-15"], ["悉尼大学", " The University of Sydney", "group-16"], ["多伦多大学", "uoft 多大 University of Toronto", "group-17"], ["浙江师范大学", " Zhejiang Normal University", "group-18"], ["加州大学圣地亚哥分校", "ucsd University of California, San Diego", "group-19"], ["南洋理工大学", "ntu Nanyang Technological University", "group-20"], ["伦敦大学学院", "ucl University College London", "group-21"], ["波士顿大学", "bu Boston University", "group-22"], ["曼彻斯特大学", "曼大 The University of Manchester", "group-23"], ["温州商学院", " Wenzhou Business College", "group-24"], ["中原科技学院", " Zhongyuan Institute of Science and Technology", "group-25"], ["康奈尔大学", "cornell 康大 Cornell University", "group-26"], ["河南科技大学", " Henan University of Science and Technology", "group-27"], ["华南师范大学", "scnu 华师 South China Normal University", "group-28"], ["范德堡大学", " Vanderbilt University", "group-29"], ["长安大学", " Chang’an University", "group-30"], ["苏黎世联邦理工大学 & 苏黎世大学", "eth uzh ETH Zurich & University of Zurich", "group-31"], ["浙江水利水电学院", " Zhejiang University of Water Resources and Electric Power", "group-32"], ["约克大学", " York University", "group-33"], ["上海海湾三校联合", " Shanghai Haiwan Three-School Alliance", "group-34"], ["明尼苏达大学", " University of Minnesota", "group-35"], ["伦敦艺术大学", " University of the Arts London", "group-36"], ["澳大利亚国立大学", " Australian National University", "group-37"], ["昆士兰大学薄肌俱乐部", "uq UQ Boji Club (The University of Queensland)", "group-38"], ["上海交通大学", "sjtu 上交 Shanghai Jiao Tong University", "group-39"], ["杜伦大学", " Durham University", "group-40"], ["东南大学", "seu Southeast University", "group-41"], ["成都大学", " Chengdu University", "group-42"], ["河内大学", " Hanoi University", "group-43"], ["华威大学", " The University of Warwick", "group-44"], ["宁波卫校", " Ningbo Health School", "group-45"], ["大连医科大学", " Dalian Medical University", "group-46"], ["上海工程技术大学", " Shanghai University of Engineering Science", "group-47"], ["福建农林大学", " Fujian Agriculture and Forestry University", "group-48"], ["西南财经大学", "swufe 西财 Southwestern University of Finance and Economics", "group-49"]];
let schools = [...fallbackSchools], directoryRequest = null, lastLoaded = 0;
const normalize = value => value.normalize('NFKC').toLocaleLowerCase().replace(/[\s’'—–-]+/g, '');
function matchingSchools(value) {
 const q = normalize(value);
 return schools.filter(([name,alias]) => normalize(name+' '+alias).includes(q))
  .sort((a,b) => Number(normalize(b[0])===q)-Number(normalize(a[0])===q));
}
async function loadSchools() {
 if(directoryRequest) return directoryRequest;
 if(Date.now()-lastLoaded<30000) return;
 directoryRequest = (async()=>{
  try {
   const response=await fetch('/api/club/schools',{cache:'no-store',signal:AbortSignal.timeout(8000)});
   if(!response.ok)throw new Error('directory unavailable');
   const data=await response.json();
   const merged=new Map(data.schools.map(s=>[s.id,[s.name,(s.alias||'')+' '+(s.en||''),s.id]]));
   for(const club of data.published) {
    const existing=merged.get(club.id);
    merged.set(club.id,[club.name,`${existing?.[1]||''} ${club.en||''} ${club.alias||''}`,club.id]);
   }
   schools=[...merged.values()];lastLoaded=Date.now();
   heroStatus.textContent='';
  } catch(e) {heroStatus.textContent='学校列表暂未更新，稍后重试；仍可搜索已有学校。';}
  finally {directoryRequest=null;if(document.activeElement===heroInput)renderSuggestions();}
 })();
 return directoryRequest;
}
function openSchool(name) {
 const school=schools.find(s=>s[0]===name);
 if(school)location.assign('/schools?school='+encodeURIComponent(school[2]));
}
document.addEventListener('click',event=>{
 const button=event.target.closest('button');if(!button)return;
 if(button.classList.contains('menu')){
  const open=header.classList.toggle('menu-open');button.setAttribute('aria-expanded',String(open));return;
 }
 if(button.dataset.school){openSchool(button.dataset.school);return;}
 if(button.dataset.open==='schools'){
  header.classList.remove('menu-open');document.querySelector('.menu').setAttribute('aria-expanded','false');
  heroInput.scrollIntoView({block:'center',behavior:'instant'});heroInput.focus();loadSchools();
 }
});
header.querySelectorAll('nav a').forEach(a=>a.addEventListener('click',()=>{header.classList.remove('menu-open');document.querySelector('.menu').setAttribute('aria-expanded','false');}));
for(const [selector,section,label] of [['.marquee-toggle','.campus-marquee','学校滚动'],['.voice-toggle','.rolling-voices','人物滚动']]){
 const button=document.querySelector(selector);
 button?.addEventListener('click',()=>{const paused=document.querySelector(section).classList.toggle('paused');button.setAttribute('aria-pressed',String(paused));button.setAttribute('aria-label',(paused?'继续':'暂停')+label);});
}
// Hero search uses the same controlled school directory and detail flow.
const heroForm = document.querySelector('.hero-search');
const heroInput = document.querySelector('#hero-school');
const heroStatus = document.querySelector('.hero-search-status');
const suggestions = document.createElement('div');
suggestions.id = 'hero-school-suggestions';
suggestions.className = 'hero-school-suggestions';
suggestions.setAttribute('role', 'listbox');
suggestions.setAttribute('aria-label', '匹配学校');
suggestions.hidden = true;
document.body.append(suggestions);
let heroMatches = [], heroActive = -1;
function hideSuggestions() {
 suggestions.hidden = true;
 heroInput.setAttribute('aria-expanded', 'false');
 heroInput.removeAttribute('aria-activedescendant');
 heroActive = -1;
}
function positionSuggestions() {
 if (suggestions.hidden) return;
 const box = heroInput.getBoundingClientRect();
 const height = Math.min(235, Math.max(110, innerHeight - box.bottom - 20));
 suggestions.style.width = box.width + 'px';
 suggestions.style.left = box.left + 'px';
 suggestions.style.top = (innerHeight - box.bottom > 135 ? box.bottom + 8 : Math.max(8, box.top - 243)) + 'px';
 suggestions.style.maxHeight = height + 'px';
}
function renderSuggestions() {
 const query = heroInput.value.trim().toLocaleLowerCase();
 heroMatches = matchingSchools(query).slice(0, 8);
 heroActive = -1;
 heroInput.removeAttribute('aria-activedescendant');
 suggestions.replaceChildren();
 heroMatches.forEach(([name], index) => {
  const item = document.createElement('div');
  item.id = 'hero-school-option-' + index;
  item.className = 'hero-school-option';
  item.setAttribute('role', 'option');
  item.setAttribute('aria-selected', 'false');
  item.textContent = name;
  item.addEventListener('mousedown', event => event.preventDefault());
  item.addEventListener('click', () => {
   heroInput.value = name;
   heroStatus.textContent = '';
   hideSuggestions();
   heroForm.querySelector('button').focus();
  });
  suggestions.append(item);
 });
 if (!heroMatches.length) {
  const empty = document.createElement('p');
  empty.className = 'hero-school-empty';
  empty.textContent = '暂未找到，试试学校全称或英文简称。';
  suggestions.append(empty);
 }
 suggestions.hidden = false;
 heroInput.setAttribute('aria-expanded', 'true');
 positionSuggestions();
}
heroInput.addEventListener('input', () => { heroStatus.textContent = ''; renderSuggestions(); });
heroInput.addEventListener('focus', () => {renderSuggestions();loadSchools();});
heroInput.addEventListener('keydown', event => {
 if (event.isComposing) return;
 if (event.key === 'Escape') { hideSuggestions(); return; }
 if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
  event.preventDefault();
  if (suggestions.hidden) renderSuggestions();
  if (!heroMatches.length) return;
  heroActive = (heroActive + (event.key === 'ArrowDown' ? 1 : -1) + heroMatches.length) % heroMatches.length;
  [...suggestions.children].forEach((node, index) => node.setAttribute('aria-selected', String(index === heroActive)));
  const active = suggestions.children[heroActive];
  heroInput.setAttribute('aria-activedescendant', active.id);
  active.scrollIntoView({block:'nearest'});
 }
 if (event.key === 'Enter' && !suggestions.hidden && heroActive >= 0) {
  event.preventDefault();
  heroInput.value = heroMatches[heroActive][0];
  hideSuggestions();
  heroForm.requestSubmit();
 }
});
heroForm.addEventListener('submit', async event => {
 event.preventDefault();
 await loadSchools();
 const query = heroInput.value.trim().toLocaleLowerCase();
 if (!query) {
  heroStatus.textContent = '先输入你的学校名称。';
  heroInput.focus();
  return;
 }
 const matches = matchingSchools(query);
 const exact = schools.find(([name]) => normalize(name) === normalize(query));
 if (exact || matches.length === 1) {
  const name = (exact || matches[0])[0];
  heroInput.value = name;
  heroStatus.textContent = '';
  hideSuggestions();
  openSchool(name);
 } else {
  heroStatus.textContent = matches.length ? '请从搜索结果中选择你的学校。' : '暂未找到这所学校，试试其他名称。';
  heroInput.focus();
  renderSuggestions();
 }
});
document.addEventListener('pointerdown', event => {
 if (!heroForm.contains(event.target) && !suggestions.contains(event.target)) hideSuggestions();
});
heroForm.addEventListener('focusout', () => {
 requestAnimationFrame(() => { if (!heroForm.contains(document.activeElement)) hideSuggestions(); });
});
addEventListener('scroll', hideSuggestions, {passive:true});
addEventListener('resize', positionSuggestions);

loadSchools();
