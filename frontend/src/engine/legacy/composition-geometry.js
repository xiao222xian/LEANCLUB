import { primitiveKit as p, resolveBoundary, contourPath, generateBoundary } from './boundary-geometry.js';

const expandedGlyphs=new Map();
const resolved=shape=>{
  let failure;
  // This retry is confined to the new compositor. The original uppercase-pair
  // engine always uses its unchanged default probe. No coordinate is displaced:
  // we classify the same exact arc/line fragments at another normal distance.
  for(const sideOffset of [.00002,.000002,.0002,.002]) {
    try{return p.region(resolveBoundary(shape,{sideOffset}));}catch(error){failure=error;}
  }
  throw failure;
};
const bounds=shape=>{
  const boxes=shape.edges.map(p.edgeBounds);
  return [Math.min(...boxes.map(b=>b[0])),Math.min(...boxes.map(b=>b[1])),Math.max(...boxes.map(b=>b[2])),Math.max(...boxes.map(b=>b[3]))];
};
const center=b=>[(b[0]+b[2])/2,(b[1]+b[3])/2];
function expanded(letter,weight,gap) {
  const key=`${letter}:${weight}:${gap.toFixed(4)}`;
  if(!expandedGlyphs.has(key)) {
    if(expandedGlyphs.size>=260)expandedGlyphs.delete(expandedGlyphs.keys().next().value);
    const dilation=p.dilation(p.resolvedGlyph(letter,weight),gap);
    // Normally simplify the exact offset once. At an exact tangent degeneracy,
    // retain its original analytic region and classify it with the final Boolean
    // operation instead; this is the same shape, not a different layout.
    try { expandedGlyphs.set(key,resolved(dilation)); }
    catch { expandedGlyphs.set(key,dilation); }
  }
  return expandedGlyphs.get(key);
}
function glyphPlacement(character,index,weight,{x=0,y=0,scale=1,angle=0}={}) {
  const source=p.resolvedGlyph(character,weight),box=bounds(source),cx=(box[0]+box[2])/2,cy=(box[1]+box[3])/2;
  const origin=p.transform(source,{x:-cx,y:-cy}),transform={x,y,scale,angle};
  return {character,index,shape:p.transform(origin,transform),source,cx,cy,transform};
}
function coverFor(entry,weight,gap) {
  return p.transform(p.transform(expanded(entry.character,weight,gap),{x:-entry.cx,y:-entry.cy}),entry.transform);
}
function solidAnchor(shape) {
  const b=bounds(shape),c=center(b),points=[];
  for(let row=1;row<16;row++)for(let col=1;col<16;col++) {
    const point=[b[0]+(b[2]-b[0])*col/16,b[1]+(b[3]-b[1])*row/16];
    if(shape.contains(point))points.push(point);
  }
  points.sort((a,b)=>Math.hypot(a[0]-c[0],a[1]-c[1])-Math.hypot(b[0]-c[0],b[1]-c[1]));
  return points[0]||[b[0]+1,c[1]];
}
function singleMark(entry,{strategy,weight,tension,spacing,seed}) {
  const base=entry.shape,b=bounds(base),[cx,cy]=center(b),gap=1.8+tension*.02;
  if(strategy==='fusion')return base;
  if(strategy==='counter') {
    const at=solidAnchor(base);
    return p.difference(base,p.disk(at[0],at[1],Math.max(2.2,weight*.23)));
  }
  if(strategy==='axis')return p.difference(base,p.rect(cx-gap/2,b[1]-3,gap,b[3]-b[1]+6));
  if(strategy==='embrace')return p.difference(base,p.transform(base,{scale:.72,x:cx*.28+weight*.12,y:cy*.28}));
  const upper=p.intersection(base,p.rect(b[0]-10,b[1]-10,b[2]-b[0]+20,cy-b[1]+10-gap/2));
  const lower=p.intersection(base,p.rect(b[0]-10,cy+gap/2,b[2]-b[0]+20,b[3]-cy+10));
  const offset=2+spacing*.035+Math.sin(seed*2.4);
  if(strategy==='weave')return p.union(p.transform(upper,{x:-offset}),p.transform(lower,{x:offset}));
  const angle=4+tension*.06;
  return p.union(p.transform(upper,{x:-offset,angle:-angle}),p.transform(lower,{x:offset,angle}));
}
function composeCluster(characters,start,options) {
  const {weight,overlap,tension,spacing,seed,strategy}=options;
  const count=characters.length,energy=(tension-50)/50,density=(overlap-24)/44;
  const variation=Math.sin(seed*2.4)*3;
  const widths=characters.map(character=>{const b=bounds(p.resolvedGlyph(character,weight));return b[2]-b[0];});
  const average=widths.reduce((a,b)=>a+b,0)/widths.length;
  const step=Math.max(12,average*(.64-density*.23)+spacing*.24+variation);
  const rise=58-density*14+spacing*.1;
  const anchors=count===1?[[0,0]]:count===2?[[-step/2,0],[step/2,0]]:count===3?[[-step/2,-rise/2],[step/2,-rise/2],[0,rise/2]]:[[-step/2,-rise/2],[step/2,-rise/2],[-step/2,rise/2],[step/2,rise/2]];
  const entries=characters.map((character,i)=>{
    let [x,y]=anchors[i],scale=1,angle=0;
    if(strategy==='weave'){x*=.86;y+=(i%2?1:-1)*6;}
    if(strategy==='counter'){x*=.82;y*=.82;}
    if(strategy==='axis'){x=(i-(count-1)/2)*(9+spacing*.15);y=(i-(count-1)/2)*(20-density*6);scale=1-i*.045;}
    if(strategy==='embrace') {
      if(i===0){x=-step*.22;y=-rise*.16;scale=1.22;}
      else {scale=.62+(i%2)*.08;x=step*(i%2?.42:-.18);y=rise*(i<2?.13:.68);}
    }
    if(strategy==='tension') {angle=(i-(count-1)/2)*(9+energy*5);x*=1.05;y+=(i%2?1:-1)*5;}
    return glyphPlacement(character,start+i,weight,{x,y,scale,angle});
  });
  let combined;
  if(count===1)combined=singleMark(entries[0],options);
  else if(strategy==='fusion'||strategy==='axis')combined=p.union(...entries.map(entry=>entry.shape));
  else if(strategy==='counter')combined=entries.map(entry=>entry.shape).reduce((a,b)=>p.xor(a,b));
  else {
    const gap=1.6+Math.max(0,energy)*2,covers=entries.map(entry=>coverFor(entry,weight,gap)),pieces=entries.map(entry=>entry.shape);
    for(let i=0;i<count;i++)for(let j=i+1;j<count;j++) {
      const a=bounds(entries[i].shape),b=bounds(entries[j].shape);
      if(a[0]>b[2]+gap||b[0]>a[2]+gap||a[1]>b[3]+gap||b[1]>a[3]+gap)continue;
      if(strategy==='weave') {
        const split=(Math.max(a[1],b[1])+Math.min(a[3],b[3]))/2;
        const upper=p.rect(-1000,-1000,2000,1000+split),lower=p.rect(-1000,split,2000,1000),swap=(i+j)%2===0;
        pieces[i]=p.difference(pieces[i],p.intersection(covers[j],swap?lower:upper));
        pieces[j]=p.difference(pieces[j],p.intersection(covers[i],swap?upper:lower));
      } else pieces[i]=p.difference(pieces[i],covers[j]);
    }
    combined=p.union(...pieces);
  }
  const shape=resolved(combined);
  if(!shape.edges.length)throw new Error('该组合没有保留有效轮廓，请调整重叠');
  return {shape,entries};
}
function fitAndDescribe(clusters) {
  const placementShapes=clusters.flatMap(cluster=>cluster.entries.map(entry=>entry.shape));
  // Each compact cluster is already resolved. Disjoint cluster support boxes
  // avoid a quadratic global Boolean pass for long names without dropping input.
  const contours=clusters.flatMap(cluster=>cluster.shape.contours),combined=p.region(contours),b=bounds(combined);
  const size=Math.min(230/(b[2]-b[0]),148/(b[3]-b[1]));
  const fit={scale:size,x:160-(b[0]+b[2])*size/2,y:98-(b[1]+b[3])*size/2};
  const fitted=p.transform(combined,fit);
  let cursor=0;
  const loops=contours.map(loop=>{const next=fitted.edges.slice(cursor,cursor+loop.length);cursor+=loop.length;return next;});
  const circles=[],lines=[],joints=[];
  for(const loop of loops)loop.forEach((edge,i)=>{
    if(edge.circle) {
      if(!circles.some(c=>p.near(c,edge.circle,.001)&&Math.abs(c[2]-edge.circle[2])<.001))circles.push(edge.circle);
    }else lines.push(edge);
    const next=loop[(i+1)%loop.length],a=p.tangent(edge,1),t=p.tangent(next,0);
    if((edge.circle||next.circle)&&p.dot(a,t)>.99999)joints.push({point:edge.to,tangent:a});
  });
  return {contours:loops,circles,lines,joints,paths:loops.map(contourPath),contains:fitted.contains,placementShapes:placementShapes.map(shape=>p.transform(shape,fit)),groupCount:clusters.length};
}
export function generateComposition(options) {
  const {letters}=options;
  // Keep upstream floating-point order and fitting for every uppercase pair.
  if(/^[A-Z]{2}$/.test(letters))return {...generateBoundary(options),groupCount:1};
  const count=Math.ceil(letters.length/4),clusters=[];
  let cursor=0;
  for(let index=0;index<count;index++) {
    const size=Math.ceil((letters.length-cursor)/(count-index));
    clusters.push(composeCluster([...letters.slice(cursor,cursor+size)],cursor,options));
    cursor+=size;
  }
  if(count>1) {
    const columns=count<=3?count:Math.ceil(Math.sqrt(count)),rows=Math.ceil(count/columns),pitch=190,gap=12;
    clusters.forEach((cluster,index)=>{
      const b=bounds(cluster.shape),c=center(b),scale=Math.min((pitch-gap)/(b[2]-b[0]),(pitch-gap)/(b[3]-b[1]));
      const row=Math.floor(index/columns),column=index%columns,occupied=Math.min(columns,count-row*columns);
      const stagger=options.strategy==='tension'?(column-(occupied-1)/2)*20:0;
      const matrix={scale,x:(column-(occupied-1)/2)*pitch-c[0]*scale,y:(row-(rows-1)/2)*pitch-c[1]*scale+stagger};
      cluster.shape=p.transform(cluster.shape,matrix);
      for(const entry of cluster.entries)entry.shape=p.transform(entry.shape,matrix);
    });
  }
  return fitAndDescribe(clusters);
}
export function describePlacement(shape) {
  const b=bounds(shape);
  return {path:(shape.contours||resolveBoundary(shape)).map(contourPath).join(' '),bounds:[b[0],b[1],b[2]-b[0],b[3]-b[1]]};
}
export function describeBounds(contours) {
  const b=bounds(p.region(contours));
  return [b[0],b[1],b[2]-b[0],b[3]-b[1]];
}
