import {generateBoundary} from './boundary-geometry.js';

const $=selector=>document.querySelector(selector);
const state={letters:'SR',weight:24,overlap:44,tension:52,seed:0,selected:0,guides:true};
const strategies=[
  {id:'weave',name:'交错穿插',tag:'上下切开',logic:'上下交叉区分别切除后层实体，得到有真实留白的封闭轮廓。'},
  {id:'fusion',name:'轮廓熔合',tag:'区域合并',logic:'合并两个字母的实体，移除内部重叠边，仅保留最终内外轮廓。'},
  {id:'counter',name:'负形嵌入',tag:'相交挖空',logic:'挖去相交区域，把两个字母共同形成的空白保留为实际孔洞。'},
  {id:'axis',name:'共轴压合',tag:'紧密共形',logic:'缩小字母间距并合并轮廓，共用实体区域不再重复描线。'},
  {id:'embrace',name:'内外抱合',tag:'嵌套切除',logic:'缩小第二个字母，切开第一字母的相邻边缘，形成内外层次。'},
  {id:'tension',name:'斜向咬合',tag:'旋转相接',logic:'旋转字母后重新求交，切口和圆弧仍属于实际填色边界。'}
];
const FPS=30,DURATION=15,FRAMES=450;
const stages=[{name:'直边定位',start:0,time:1.5},{name:'构造圆展开',start:1.6,time:3.4},{name:'边缘成形',start:4.3,time:7.8},{name:'渐进填黑',start:8,time:10.5},{name:'完整标志',start:12.5,time:14}];
const playback={time:0,playing:true,loop:true,rate:1,last:performance.now()};
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
const cache=new Map();
const num=n=>Number(n.toFixed(6));
const point=p=>p.map(num).join(' ');
const clamp=x=>Math.max(0,Math.min(1,x));
const progress=(t,a,b)=>{const x=clamp((t-a)/(b-a));return x<.5?4*x*x*x:1-(-2*x+2)**3/2;};
const phaseAt=t=>stages.reduce((p,s,i)=>t>=s.start?i:p,0);
let serial=0,motionNodes=null;

function model(index) {
  const strategy=strategies[index];
  const key=JSON.stringify([state.letters,state.weight,state.overlap,state.tension,state.seed,strategy.id]);
  if(!cache.has(key)) {
    if(cache.size>64)cache.clear();
    cache.set(key,generateBoundary({...state,strategy:strategy.id}));
  }
  return cache.get(key);
}

function svgFor(index,{guides=false,download=false}={}) {
  const geometry=model(index),strategy=strategies[index],id=`boundary-${++serial}`;
  const path=geometry.paths.join(' ');
  const fill=`<g class="final-art" fill="currentColor" color="#121212"><path class="region-fill" d="${path}" fill-rule="evenodd"/></g>`;
  if(!guides)return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200"${download?' width="1280" height="800"':''} role="img" aria-label="${state.letters} ${strategy.name}">${fill}</svg>`;
  const defs=`<defs><clipPath id="${id}-workspace"><rect x="12" y="18" width="296" height="158"/></clipPath><linearGradient id="${id}-sweep" gradientUnits="userSpaceOnUse" x1="70" y1="25" x2="245" y2="170"><stop class="fill-start" offset="0" stop-color="white"/><stop class="fill-end" offset="0" stop-color="black"/></linearGradient><mask id="${id}-reveal" maskUnits="userSpaceOnUse" x="0" y="0" width="320" height="200"><rect width="320" height="200" fill="url(#${id}-sweep)"/></mask></defs>`;
  const grid=`<g class="process-grid" stroke="#cbd4d5" stroke-width=".175">${Array.from({length:19},(_,i)=>`<line x1="${47.5+i*12.5}" x2="${47.5+i*12.5}" y1="18" y2="175"/>`).join('')}${Array.from({length:13},(_,i)=>`<line x1="47.5" x2="272.5" y1="${20+i*12.5}" y2="${20+i*12.5}"/>`).join('')}</g>`;
  const lines=`<g class="construction phase-lines" stroke="#658486" stroke-width=".2125" fill="none">${geometry.lines.map(e=>`<path pathLength="1" d="M ${point(e.from)} L ${point(e.to)}"/>`).join('')}</g>`;
  const circles=`<g class="construction circle-construction" fill="none" stroke-width=".2125">${geometry.circles.map(([x,y,r])=>`<circle class="guide-ring" pathLength="1" cx="${num(x)}" cy="${num(y)}" r="${num(r)}" stroke="#748d8a" transform="rotate(-90 ${num(x)} ${num(y)})"/>`).join('')}</g>`;
  const joints=`<g class="construction phase-tangent" stroke="#517e74" stroke-width=".2">${geometry.joints.map(({point:p,tangent:t})=>`<line x1="${p[0]-t[0]*4}" y1="${p[1]-t[1]*4}" x2="${p[0]+t[0]*4}" y2="${p[1]+t[1]*4}"/><circle cx="${p[0]}" cy="${p[1]}" r=".55" fill="white"/>`).join('')}</g>`;
  const outline=`<g class="outline-art" fill="none" stroke="#303c3c" stroke-width=".3375" stroke-linejoin="round">${geometry.paths.map(d=>`<path d="${d}" pathLength="1"/>`).join('')}</g>`;
  const labels=`<g fill="#87918f" font-family="Helvetica Neue,Arial,sans-serif" font-size="3"><text x="13" y="12" letter-spacing=".75">LEAN SCHOOL / DESIGN PROCESS</text><text x="307" y="12" text-anchor="end">${geometry.circles.length} CIRCLES · ${geometry.lines.length} LINES</text><text class="process-stage-label" x="13" y="191">01 / 直边定位</text><text class="process-time-label" x="307" y="191" text-anchor="end">0.0 / 15.0 s</text></g>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200" role="img" aria-label="${state.letters} 封闭轮廓构造过程">${defs}<rect width="320" height="200" fill="white"/>${grid}<g clip-path="url(#${id}-workspace)">${lines}${circles}${joints}</g>${outline}<g class="fill-reveal" data-mask="url(#${id}-reveal)" mask="url(#${id}-reveal)">${fill}</g>${labels}</svg>`;
}

function bindMotionNodes() {
  const root=$('#hero-mark');
  motionNodes={grid:root.querySelector('.process-grid'),lines:[...root.querySelectorAll('.phase-lines path')],circles:[...root.querySelectorAll('.guide-ring')],outlines:[...root.querySelectorAll('.outline-art path')],joints:root.querySelector('.phase-tangent'),fill:root.querySelector('.final-art'),reveal:root.querySelector('.fill-reveal'),start:root.querySelector('.fill-start'),end:root.querySelector('.fill-end'),stage:root.querySelector('.process-stage-label'),time:root.querySelector('.process-time-label')};
}
function applyMotionFrame() {
  if(!motionNodes)return;
  const n=motionNodes,t=playback.time,fade=1-progress(t,8.1,10.4);
  if(n.grid)n.grid.style.opacity=String(progress(t,0,.8)*.28*fade);
  n.lines.forEach((path,i)=>{
    const p=progress(t,.25+i/Math.max(1,n.lines.length)*.6,1.4+i/Math.max(1,n.lines.length)*.6);
    path.style.strokeDasharray='1';path.style.strokeDashoffset=String(1-p);path.style.opacity=String(p*.75*fade);
  });
  n.circles.forEach((circle,i)=>{
    const p=progress(t,1.6+i/Math.max(1,n.circles.length)*1.25,2.95+i/Math.max(1,n.circles.length)*1.25);
    circle.style.strokeDasharray='1';circle.style.strokeDashoffset=String(1-p);circle.style.opacity=String(p*.55*fade);
  });
  n.outlines.forEach((path,i)=>{
    const delay=i/Math.max(1,n.outlines.length)*.55,p=progress(t,4.3+delay,7.2+delay);
    path.style.strokeDasharray='1';path.style.strokeDashoffset=String(1-p);path.style.opacity=String(p>0?1-progress(t,10.8,12.6):0);
  });
  if(n.joints)n.joints.style.opacity=String(progress(t,5.6,6.5)*(1-progress(t,8,9)));
  const filling=progress(t,8,12.3),shade=Math.round(163*(1-filling)+18*filling);
  n.fill.style.opacity=state.guides?String(progress(t,8,8.5)):'1';
  n.fill.style.color=state.guides?`rgb(${shade},${shade},${shade})`:'#121212';
  if(n.reveal) {
    n.start.setAttribute('offset',String(clamp(filling*1.3-.18)));
    n.end.setAttribute('offset',String(clamp(filling*1.3+.1)));
    n.reveal.setAttribute('mask',filling>=1?'none':n.reveal.dataset.mask);
  }
  const phase=phaseAt(t);
  if(n.stage)n.stage.textContent=`${String(phase+1).padStart(2,'0')} / ${stages[phase].name}`;
  if(n.time)n.time.textContent=`${t.toFixed(1)} / 15.0 s`;
  $('#timeline').value=String(Math.min(FRAMES,Math.round(t*FPS)));
  $('#motion-time').value=`${t.toFixed(1)} / 15.0 s`;
  $('#playback').textContent=playback.playing?'暂停动画':'播放动画';
  document.querySelectorAll('.motion-chapters button').forEach((b,i)=>b.setAttribute('aria-pressed',String(i===phase)));
}
function restartMotion() {
  playback.time=reduced.matches?DURATION:0;playback.playing=!reduced.matches;playback.last=performance.now();applyMotionFrame();
}
function render() {
  const value=$('#letters').value.toUpperCase();
  $('#letters').value=value;
  const valid=/^[A-Z]{2}$/.test(value);
  $('#input-error').hidden=valid;$('#letters').setAttribute('aria-invalid',String(!valid));
  document.querySelectorAll('[data-letters]').forEach(button=>button.setAttribute('aria-pressed',String(valid&&button.dataset.letters===value)));
  document.querySelectorAll('.exports button').forEach(b=>b.disabled=!valid);
  if(!valid)return;
  state.letters=value;
  for(const key of ['weight','overlap','tension']) {
    state[key]=Number($(`#${key}`).value);$(`#${key}-value`).value=`${state[key]}${key==='weight'?'':'%'}`;
  }
  try {
    const geometry=model(state.selected);
    const main=svgFor(state.selected,{guides:state.guides});
    const variants=strategies.map((strategy,index)=>`<button class="variant" type="button" data-index="${index}" aria-pressed="${index===state.selected}" aria-label="${strategy.name}">${svgFor(index)}<span class="variant-meta"><span>${String(index+1).padStart(2,'0')} ${strategy.name}</span><span>${strategy.tag}</span></span></button>`).join('');
    $('#hero-mark').innerHTML=main;$('#variant-grid').innerHTML=variants;
    $('#selected-index').textContent=String(state.selected+1).padStart(2,'0');
    $('#selected-title').textContent=strategies[state.selected].name;
    $('#logic').textContent=strategies[state.selected].logic;
    $('#geometry-count').textContent=`${geometry.circles.length} 个边缘圆 · ${geometry.lines.length} 条直边 · ${geometry.contours.length} 个封闭轮廓`;
    $('#geometry-error').hidden=true;
    bindMotionNodes();restartMotion();
  } catch(error) {
    console.error(error);
    $('#geometry-error').hidden=false;
    document.querySelectorAll('.exports button').forEach(b=>b.disabled=true);
  }
}
let renderFrame=0;
function scheduleRender(){cancelAnimationFrame(renderFrame);renderFrame=requestAnimationFrame(render);}
function download(name,blob) {
  const link=document.createElement('a'),url=URL.createObjectURL(blob);link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
$('#variant-grid').addEventListener('click',event=>{const button=event.target.closest('[data-index]');if(button){state.selected=Number(button.dataset.index);render();}});
$('#letters').addEventListener('input',scheduleRender);
document.querySelector('.letter-examples').addEventListener('click',event=>{
  const button=event.target.closest('[data-letters]');
  if(!button)return;
  $('#letters').value=button.dataset.letters;
  for(const [key,value] of Object.entries({weight:24,overlap:44,tension:52}))$(`#${key}`).value=value;
  state.seed=0;state.selected=0;state.guides=true;
  $('#toggle-guides').setAttribute('aria-pressed','true');
  $('#toggle-guides').textContent='查看纯黑结果';
  render();
  if(matchMedia('(max-width: 900px)').matches)$('#hero-mark').scrollIntoView({block:'center',behavior:reduced.matches?'instant':'smooth'});
});
for(const key of ['weight','overlap','tension'])$(`#${key}`).addEventListener('input',scheduleRender);
$('#regenerate').addEventListener('click',()=>{state.seed++;render();});
$('#playback').addEventListener('click',()=>{if(playback.time>=DURATION)playback.time=0;playback.playing=!playback.playing;playback.last=performance.now();applyMotionFrame();});
$('#replay').addEventListener('click',()=>{playback.time=0;playback.playing=true;playback.last=performance.now();applyMotionFrame();});
$('#loop').addEventListener('click',event=>{playback.loop=!playback.loop;event.currentTarget.setAttribute('aria-pressed',String(playback.loop));});
$('#rate').addEventListener('change',event=>{playback.rate=Number(event.currentTarget.value);});
$('#timeline').addEventListener('input',event=>{playback.playing=false;playback.time=Number(event.currentTarget.value)/FPS;applyMotionFrame();});
document.querySelectorAll('.motion-chapters button').forEach(button=>button.addEventListener('click',()=>{playback.playing=false;playback.time=Number(button.dataset.time);applyMotionFrame();}));
$('#toggle-guides').addEventListener('click',event=>{state.guides=!state.guides;event.currentTarget.setAttribute('aria-pressed',String(state.guides));event.currentTarget.textContent=state.guides?'查看纯黑结果':'显示边缘构造';render();});
$('#download-svg').addEventListener('click',()=>download(`${state.letters}-monogram.svg`,new Blob([svgFor(state.selected,{download:true})],{type:'image/svg+xml'})));
$('#download-png').addEventListener('click',()=>{
  const image=new Image(),url=URL.createObjectURL(new Blob([svgFor(state.selected,{download:true})],{type:'image/svg+xml'}));
  image.onload=()=>{const canvas=document.createElement('canvas');canvas.width=1280;canvas.height=800;canvas.getContext('2d').drawImage(image,0,0);canvas.toBlob(blob=>{if(blob)download(`${state.letters}-monogram.png`,blob);},'image/png');URL.revokeObjectURL(url);};
  image.onerror=()=>{URL.revokeObjectURL(url);$('#geometry-error').textContent='图片导出失败，请重试。';$('#geometry-error').hidden=false;};image.src=url;
});
render();
let visible=true;
function tick(now) {
  const delta=Math.max(0,Math.min(.1,(now-playback.last)/1000));playback.last=now;
  if(playback.playing&&visible&&!document.hidden) {
    playback.time+=delta*playback.rate;
    if(playback.time>=DURATION){if(playback.loop)playback.time%=DURATION;else{playback.time=DURATION;playback.playing=false;}}
    applyMotionFrame();
  }
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
new IntersectionObserver(([entry])=>{visible=entry.isIntersecting;playback.last=performance.now();},{threshold:.15}).observe($('#hero-mark'));
