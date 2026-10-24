import {parts as labParts} from '../logo-geometry.js';

// Geometry is carried as oriented line/circle edges throughout the Boolean
// operation. Sampling is never used to draw or fit the resulting boundary.
const TAU=Math.PI*2, EPS=1e-7;
const sub=(a,b)=>[a[0]-b[0],a[1]-b[1]];
const dot=(a,b)=>a[0]*b[0]+a[1]*b[1];
const cross=(a,b)=>a[0]*b[1]-a[1]*b[0];
const norm=v=>Math.hypot(...v);
const unit=v=>{const n=norm(v);return v.map(x=>x/n);};
const near=(a,b,t=1e-5)=>norm(sub(a,b))<t;
const mod=x=>(x%TAU+TAU)%TAU;
const number=x=>Number(x.toFixed(6));
const xy=p=>p.map(number).join(' ');
const line=(from,to)=>({from,to});

function arc(from,to,circle,positive=true) {
  const start=Math.atan2(from[1]-circle[1],from[0]-circle[0]);
  const end=Math.atan2(to[1]-circle[1],to[0]-circle[0]);
  const sweep=positive?mod(end-start):-mod(start-end);
  return {from,to,circle,start,sweep};
}
function at(edge,t) {
  if(!edge.circle)return edge.from.map((v,i)=>v+(edge.to[i]-v)*t);
  const [x,y,r]=edge.circle,a=edge.start+edge.sweep*t;
  return [x+r*Math.cos(a),y+r*Math.sin(a)];
}
function tangent(edge,t) {
  if(!edge.circle)return unit(sub(edge.to,edge.from));
  const angle=edge.start+edge.sweep*t,sign=Math.sign(edge.sweep);
  return [-Math.sin(angle)*sign,Math.cos(angle)*sign];
}
function reverse(edge) {
  return edge.circle?{...edge,from:edge.to,to:edge.from,start:edge.start+edge.sweep,sweep:-edge.sweep}:{from:edge.to,to:edge.from};
}
function parameter(edge,p) {
  if(!edge.circle) {
    const v=sub(edge.to,edge.from);
    if(Math.abs(cross(v,sub(p,edge.from)))>1e-5*norm(v))return null;
    return dot(sub(p,edge.from),v)/dot(v,v);
  }
  const [cx,cy,r]=edge.circle;
  if(Math.abs(norm(sub(p,[cx,cy]))-r)>1e-4)return null;
  if(near(p,edge.from))return 0;
  if(near(p,edge.to))return 1;
  const a=Math.atan2(p[1]-cy,p[0]-cx);
  return (edge.sweep>0?mod(a-edge.start):mod(edge.start-a))/Math.abs(edge.sweep);
}
function insideLoop(loop,p) {
  // Exact horizontal-ray crossings, with a tiny deterministic vertex offset.
  const y=p[1]+1.234e-9;
  let winding=0;
  for(const edge of loop) {
    if(!edge.circle) {
      const [a,b]=[edge.from,edge.to];
      if((a[1]>y)!==(b[1]>y)) {
        const x=a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1]);
        if(x>p[0])winding+=b[1]>a[1]?1:-1;
      }
    } else {
      const [cx,cy,r]=edge.circle,v=(y-cy)/r;
      if(Math.abs(v)>=1)continue;
      // Split into y-monotone arcs. A half-open y interval counts a shared
      // vertex once, including rays exactly through a circle's left seam.
      const cuts=[0,1];
      for(const a of [Math.PI/2,Math.PI*1.5]) {
        const t=(edge.sweep>0?mod(a-edge.start):mod(edge.start-a))/Math.abs(edge.sweep);
        if(t>EPS&&t<1-EPS)cuts.push(t);
      }
      cuts.sort((a,b)=>a-b);
      for(let i=0;i<cuts.length-1;i++) {
        const a=at(edge,cuts[i]),b=at(edge,cuts[i+1]);
        if((a[1]>y)===(b[1]>y))continue;
        const sign=Math.cos(edge.start+edge.sweep*(cuts[i]+cuts[i+1])/2)>0?1:-1;
        const x=cx+sign*r*Math.sqrt(1-v*v);
        if(x>p[0])winding+=b[1]>a[1]?1:-1;
      }
    }
  }
  return winding!==0;
}
function region(loops) {
  return {edges:loops.flat(),contains:p=>loops.reduce((v,loop)=>v!==insideLoop(loop,p),false)};
}
const polygon=points=>region([points.map((p,i)=>line(p,points[(i+1)%points.length]))]);
const rect=(x,y,w,h)=>polygon([[x,y],[x+w,y],[x+w,y+h],[x,y+h]]);
function disk(cx,cy,r) {
  const a=[cx+r,cy],b=[cx-r,cy];
  return region([[arc(a,b,[cx,cy,r]),arc(b,a,[cx,cy,r])]]);
}
function operation(a,b,predicate) {
  return {edges:[...a.edges,...b.edges],contains:p=>predicate(a.contains(p),b.contains(p))};
}
const union=(...shapes)=>shapes.reduce((a,b)=>operation(a,b,(x,y)=>x||y));
const difference=(a,b)=>operation(a,b,(x,y)=>x&&!y);
const intersection=(a,b)=>operation(a,b,(x,y)=>x&&y);
const xor=(a,b)=>operation(a,b,(x,y)=>x!==y);
function bar(a,b,width,endWidth=width) {
  const n=unit([a[1]-b[1],b[0]-a[0]]);
  return polygon([a.map((v,i)=>v+n[i]*width/2),b.map((v,i)=>v+n[i]*endWidth/2),b.map((v,i)=>v-n[i]*endWidth/2),a.map((v,i)=>v-n[i]*width/2)]);
}
function transform(shape,{x=0,y=0,scale=1,angle=0}={}) {
  const a=angle*Math.PI/180,c=Math.cos(a),s=Math.sin(a);
  const point=p=>[x+scale*(p[0]*c-p[1]*s),y+scale*(p[0]*s+p[1]*c)];
  return {edges:shape.edges.map(edge=>{
    const from=point(edge.from),to=point(edge.to);
    return edge.circle?{...edge,from,to,circle:[...point(edge.circle),edge.circle[2]*scale],start:edge.start+a}:line(from,to);
  }),contains:p=>shape.contains([(c*(p[0]-x)+s*(p[1]-y))/scale,(-s*(p[0]-x)+c*(p[1]-y))/scale])};
}
function dilation(shape,amount) {
  if(Math.abs(amount)<.001)return shape;
  const d=Math.abs(amount),bands=[];
  for(const edge of shape.edges) {
    bands.push(disk(...edge.from,d));
    if(!edge.circle)bands.push(bar(edge.from,edge.to,d*2));
    else {
      const [cx,cy,r]=edge.circle;
      const polar=(radius,t)=>[cx+radius*Math.cos(edge.start+t*edge.sweep),cy+radius*Math.sin(edge.start+t*edge.sweep)];
      const outer=r+d,inner=Math.max(.001,r-d),a=polar(outer,0),b=polar(outer,1),c=polar(inner,1),e=polar(inner,0);
      bands.push(region([[arc(a,b,[cx,cy,outer],edge.sweep>0),line(b,c),arc(c,e,[cx,cy,inner],edge.sweep<0),line(e,a)]]));
    }
  }
  return amount>0?union(shape,...bands):difference(shape,union(...bands));
}

function labLetter(letter) {
  const matching=labParts.filter(p=>letter==='S'?p.name.startsWith('S'):p.name==='R');
  // Retain the Lab's actual circular edge model and intentional sharp tips.
  const loops=matching.map(part=>part.arcs.map(e=>e.circle?arc(e.from,e.to,e.circle,e.add):line(e.from,e.to)));
  return transform(region(loops),{scale:140/490,x:letter==='S'?-6*140/490:-232*140/490,y:-6*140/490});
}

function glyph(letter,weight) {
  const w=weight*.8;
  const stem=()=>rect(8,0,w,140);
  const ring=(cx,cy,r)=>difference(disk(cx,cy,r),disk(cx,cy,r-w));
  const bowl=(y,r)=>intersection(ring(32,y,r),rect(26,y-r,90,r*2));
  const diagonal=(a,b)=>bar(a,b,w*.83,w);
  switch(letter) {
    case 'S':case 'R':return dilation(labLetter(letter),(weight-24)*.18);
    case 'A':return union(diagonal([9,140],[48,0]),diagonal([48,0],[92,140]),rect(25,90,50,w*.7));
    case 'B':return union(stem(),bowl(35,35),bowl(103,37));
    case 'C':return difference(ring(50,70,70),polygon([[50,70],[130,16],[130,124]]));
    case 'D':return union(stem(),intersection(ring(24,70,70),rect(24,0,90,140)));
    case 'E':return union(stem(),rect(8,0,80,w*.8),rect(8,61,66,w*.7),rect(8,140-w*.8,80,w*.8));
    case 'F':return union(stem(),rect(8,0,80,w*.8),rect(8,61,66,w*.7));
    case 'G':return union(difference(ring(50,70,70),polygon([[50,70],[130,16],[130,94]])),rect(54,72,55,w*.8));
    case 'H':return union(stem(),rect(77,0,w,140),rect(8,62,88,w*.75));
    case 'I':return union(rect(40,0,w,140),rect(20,0,60,w*.65),rect(20,140-w*.65,60,w*.65));
    case 'J':return union(rect(65-w,0,w,103),difference(intersection(ring(30,103,35),rect(-10,103,80,50)),rect(-30,90,25,20)));
    case 'K':return union(stem(),diagonal([20,78],[89,0]),diagonal([47,57],[94,140]));
    case 'L':return union(stem(),rect(8,140-w*.8,86,w*.8));
    case 'M':return union(stem(),rect(86-w,0,w,140),diagonal([18,0],[50,74]),diagonal([50,74],[77,0]));
    case 'N':return union(stem(),rect(84-w,0,w,140),diagonal([18,0],[74,140]));
    case 'O':return ring(50,70,70);
    case 'P':return union(stem(),bowl(39,39));
    case 'Q':return union(ring(50,70,70),diagonal([62,100],[107,145]));
    case 'T':return union(rect(8,0,90,w*.85),rect(53-w/2,0,w,140));
    case 'U':return union(rect(4,0,w,94),rect(96-w,0,w,94),intersection(ring(50,94,46),rect(0,94,100,50)));
    case 'V':return union(diagonal([6,0],[49,140]),diagonal([49,140],[93,0]));
    case 'W':return union(diagonal([0,0],[23,140]),diagonal([23,140],[50,62]),diagonal([50,62],[77,140]),diagonal([77,140],[100,0]));
    case 'X':return union(diagonal([8,0],[92,140]),diagonal([92,0],[8,140]));
    case 'Y':return union(diagonal([6,0],[50,75]),diagonal([94,0],[50,75]),rect(50-w/2,70,w,70));
    case 'Z':return union(rect(5,0,90,w*.8),rect(5,140-w*.8,90,w*.8),diagonal([88,8],[12,132]));
    default:throw new Error(`Unsupported letter ${letter}`);
  }
}

function intersections(a,b) {
  if(!a.circle&&!b.circle) {
    const u=sub(a.to,a.from),v=sub(b.to,b.from),den=cross(u,v);
    if(Math.abs(den)<EPS)return [a.from,a.to,b.from,b.to];
    const t=cross(sub(b.from,a.from),v)/den;
    return [a.from.map((x,i)=>x+t*u[i])];
  }
  if(!a.circle||!b.circle) {
    const segment=a.circle?b:a,circle=a.circle?a.circle:b.circle;
    const v=sub(segment.to,segment.from),o=sub(segment.from,circle),A=dot(v,v),B=2*dot(v,o),C=dot(o,o)-circle[2]**2;
    const d=B*B-4*A*C;
    if(d<-EPS)return [];
    return [(-B-Math.sqrt(Math.max(0,d)))/(2*A),(-B+Math.sqrt(Math.max(0,d)))/(2*A)].map(t=>at(segment,t));
  }
  const [x,y,r]=a.circle,[u,v,s]=b.circle,d=Math.hypot(u-x,v-y);
  if(d<EPS)return [a.from,a.to,b.from,b.to];
  if(d>r+s+EPS||d<Math.abs(r-s)-EPS)return [];
  const q=(r*r-s*s+d*d)/(2*d),h=Math.sqrt(Math.max(0,r*r-q*q)),nx=(u-x)/d,ny=(v-y)/d;
  return [[x+q*nx-h*ny,y+q*ny+h*nx],[x+q*nx+h*ny,y+q*ny-h*nx]];
}
function edgeBounds(edge) {
  const points=[edge.from,edge.to];
  if(edge.circle)for(let i=0;i<4;i++) {
    const a=i*Math.PI/2,p=[edge.circle[0]+edge.circle[2]*Math.cos(a),edge.circle[1]+edge.circle[2]*Math.sin(a)],t=parameter(edge,p);
    if(t!==null&&t>=0&&t<=1)points.push(p);
  }
  return [Math.min(...points.map(p=>p[0])),Math.min(...points.map(p=>p[1])),Math.max(...points.map(p=>p[0])),Math.max(...points.map(p=>p[1]))];
}

export function resolveBoundary(shape) {
  const edges=shape.edges.filter(e=>norm(sub(e.to,e.from))>EPS),cuts=edges.map(()=>[0,1]),boxes=edges.map(edgeBounds);
  for(let i=0;i<edges.length;i++)for(let j=i+1;j<edges.length;j++) {
    const a=boxes[i],b=boxes[j];
    if(a[0]>b[2]+EPS||b[0]>a[2]+EPS||a[1]>b[3]+EPS||b[1]>a[3]+EPS)continue;
    for(const point of intersections(edges[i],edges[j])) {
      const x=parameter(edges[i],point),y=parameter(edges[j],point);
      if(x!==null&&y!==null&&x>=-EPS&&x<=1+EPS&&y>=-EPS&&y<=1+EPS) {
        cuts[i].push(Math.max(0,Math.min(1,x)));cuts[j].push(Math.max(0,Math.min(1,y)));
      }
    }
  }
  const result=[],unique=new Set();
  edges.forEach((edge,index)=>{
    const ts=cuts[index].sort((a,b)=>a-b).filter((v,i,a)=>!i||v-a[i-1]>1e-8);
    for(let i=0;i<ts.length-1;i++) {
      const [t0,t1]=[ts[i],ts[i+1]],middle=at(edge,(t0+t1)/2),direction=tangent(edge,(t0+t1)/2),offset=[-direction[1]*.00002,direction[0]*.00002];
      const left=shape.contains(middle.map((v,j)=>v+offset[j])),right=shape.contains(middle.map((v,j)=>v-offset[j]));
      if(left===right)continue;
      let part=edge.circle?{...edge,from:at(edge,t0),to:at(edge,t1),start:edge.start+edge.sweep*t0,sweep:edge.sweep*(t1-t0)}:line(at(edge,t0),at(edge,t1));
      if(!left)part=reverse(part);
      const key=[...part.from,...part.to,...at(part,.5)].map(n=>Number(n.toFixed(5))).join(',');
      if(!unique.has(key)&&!result.some(e=>near(e.from,part.from)&&near(e.to,part.to)&&near(at(e,.5),at(part,.5)))){unique.add(key);result.push(part);}
    }
  });
  const loops=[],remaining=new Set(result);
  while(remaining.size) {
    const first=remaining.values().next().value,loop=[first];remaining.delete(first);
    while(!near(loop.at(-1).to,first.from)) {
      const last=loop.at(-1),next=[...remaining].find(e=>near(e.from,last.to));
      if(!next)throw new Error('The geometry produced an open contour');
      loop.push(next);remaining.delete(next);
    }
    // Boolean intersections split otherwise continuous edges. Rejoin those
    // fragments so construction markers denote real geometric transitions.
    let changed=true;
    while(changed&&loop.length>2) {
      changed=false;
      for(let i=0;i<loop.length;i++) {
        const j=(i+1)%loop.length,a=loop[i],b=loop[j];
        const sameCircle=a.circle&&b.circle&&near(a.circle,b.circle,1e-6)&&Math.abs(a.circle[2]-b.circle[2])<1e-6&&Math.sign(a.sweep)===Math.sign(b.sweep)&&Math.abs(a.sweep+b.sweep)<TAU-1e-6;
        const sameLine=!a.circle&&!b.circle&&dot(tangent(a,1),tangent(b,0))>1-1e-10;
        if(!sameCircle&&!sameLine)continue;
        const merged=sameCircle?{...a,to:b.to,sweep:a.sweep+b.sweep}:line(a.from,b.to);
        if(j===0){loop[i]=merged;loop.shift();}else loop.splice(i,2,merged);
        changed=true;break;
      }
    }
    loops.push(loop);
  }
  return loops;
}
export function contourPath(loop) {
  return `M ${xy(loop[0].from)} `+loop.map(e=>e.circle?`A ${number(e.circle[2])} ${number(e.circle[2])} 0 ${Math.abs(e.sweep)>Math.PI+EPS?1:0} ${e.sweep>0?1:0} ${xy(e.to)}`:`L ${xy(e.to)}`).join(' ')+' Z';
}

const glyphCache=new Map();
function resolvedGlyph(letter,weight) {
  const key=`${letter}:${weight}`;
  if(!glyphCache.has(key))glyphCache.set(key,region(resolveBoundary(glyph(letter,weight))));
  return glyphCache.get(key);
}
export function generateBoundary({letters,weight=24,overlap=44,tension=52,strategy='weave',seed=0}) {
  const energy=(tension-50)/50,variation=Math.sin(seed*2.4)*3;
  const first=resolvedGlyph(letters[0],weight),second=resolvedGlyph(letters[1],weight);
  // The base S/R profiles are the Lab's own closed-edge primitives. Other
  // letters are regions made from bars, circles, and circular counterforms.
  let x=63-(overlap-44)*.55+variation,y=0,scale=1,angle=0;
  if(strategy==='axis')x-=22;
  if(strategy==='embrace'){scale=.78;x-=12;y=18;}
  if(strategy==='tension'){angle=5+energy*4;x+=5;}
  if(strategy==='counter')x-=16;
  if(strategy==='fusion')x-=8;
  const A=transform(first,{angle:strategy==='tension'?-angle:0});
  const B=transform(second,{x,y,scale,angle});
  let combined;
  if(strategy==='counter')combined=xor(A,B);
  else if(strategy==='fusion'||strategy==='axis')combined=union(A,B);
  else {
    // An actual empty cut is removed from the lower layer. It survives export
    // as a transparent hole, not a white stroke laid over the letter.
    const gap=1.6+Math.max(0,energy)*2;
    const coverB=transform(dilation(second,gap),{x,y,scale,angle});
    if(strategy==='weave') {
      const coverA=transform(dilation(first,gap));
      const upper=rect(-300,-300,900,382),lower=rect(-300,82,900,600);
      combined=union(difference(A,intersection(coverB,upper)),difference(B,intersection(coverA,lower)));
    } else combined=union(difference(A,coverB),B);
  }
  const loops=resolveBoundary(combined);
  const bounds=loops.flat().map(edgeBounds);
  const left=Math.min(...bounds.map(b=>b[0])),top=Math.min(...bounds.map(b=>b[1])),right=Math.max(...bounds.map(b=>b[2])),bottom=Math.max(...bounds.map(b=>b[3]));
  const size=Math.min(166/(right-left),132/(bottom-top));
  const fitted=transform(region(loops),{scale:size,x:160-(left+right)*size/2,y:98-(top+bottom)*size/2});
  let cursor=0;
  const contours=loops.map(loop=>{const result=fitted.edges.slice(cursor,cursor+loop.length);cursor+=loop.length;return result;});
  const circles=[],lines=[],joints=[];
  for(const loop of contours)loop.forEach((edge,i)=>{
    if(edge.circle) {
      if(!circles.some(c=>near(c,edge.circle,.001)&&Math.abs(c[2]-edge.circle[2])<.001))circles.push(edge.circle);
    } else lines.push(edge);
    const next=loop[(i+1)%loop.length],a=tangent(edge,1),b=tangent(next,0);
    if((edge.circle||next.circle)&&dot(a,b)>.99999)joints.push({point:edge.to,tangent:a});
  });
  return {contours,circles,lines,joints,paths:contours.map(contourPath),contains:fitted.contains};
}
