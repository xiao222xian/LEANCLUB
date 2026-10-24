// Shared source for the site, design lab, Remotion composition and brand exports.
  const W=520, H=505;
  const measuredParts=[
    {name:'S 上段',start:[289,6],arcs:[
      {to:[289,101]},{to:[125,101]},
      {to:[63,55],circle:[136.289,19.236,81.549],add:true},
      {to:[87,157],circle:[119.967,95.419,69.85],add:false},
      {to:[172,200],circle:[336.695,-231.071,461.461],add:false},
      {to:[235,275],circle:[114.779,312.026,125.794],add:true},
      {to:[201,377],circle:[134.673,298.224,102.98],add:true},
      {to:[165,291],circle:[137.186,353.178,68.115],add:false},
      {to:[81,250]},
      {to:[18,177],circle:[146.249,130.004,136.589],add:true},
      {to:[65,52],circle:[135.906,149.997,120.959],add:true},
      {to:[175,6],circle:[164.729,135.961,130.366],add:true},
      {to:[289,6]}]},
    {name:'R',start:[232,157],arcs:[
      {to:[386,157]},
      {to:[461,237],circle:[371.406,246.957,90.146],add:true},
      {to:[374,324],circle:[377.001,240.001,84.053],add:true},
      {to:[368,324]},{to:[513,496]},{to:[418,496]},{to:[276,325]},
      {to:[352,310],circle:[293.084,211.525,114.754],add:false},
      {to:[336,218],circle:[318.858,268.373,53.209],add:false},
      {to:[232,157],circle:[-357.561,1281.308,1269.508],add:false}]},
    {name:'S 下段',start:[64,362],arcs:[
      {to:[190,387],circle:[143.816,289.748,107.661],add:false},
      {to:[102,441],circle:[-255.255,-239.897,768.929],add:true},
      {to:[63,496],circle:[181.402,538.63,125.842],add:false},
      {to:[6,435]},
      {to:[64,362],circle:[-69.842,315.201,141.788],add:false}]}
  ];
  // A biarc joins two endpoint tangents using two exact circular arcs.
  // Sharing each design vertex's tangent makes all intended smooth joins G1 continuous.
  const unit=v=>{const n=Math.hypot(...v);return v.map(x=>x/n);};
  const dot=(a,b)=>a[0]*b[0]+a[1]*b[1];
  const cross=(a,b)=>a[0]*b[1]-a[1]*b[0];
  const sub=(a,b)=>[a[0]-b[0],a[1]-b[1]];
  function originalTangent(arc,point) {
    if(!arc.circle) return unit(sub(arc.to,arc.from));
    const radial=sub(point,arc.circle),sign=arc.add?1:-1;
    return unit([-radial[1]*sign,radial[0]*sign]);
  }
  function circularArc(from,to,tangent) {
    const chord=sub(to,from),normal=[-tangent[1],tangent[0]],den=2*dot(chord,normal);
    if(Math.abs(den)<1e-8) return {from,to};
    const radius=dot(chord,chord)/den;
    const center=[from[0]+radius*normal[0],from[1]+radius*normal[1]];
    return {from,to,circle:[...center,Math.abs(radius)],add:radius>0};
  }
  function biarc(from,to,t0,t1) {
    const v=sub(to,from),sum=[t0[0]+t1[0],t0[1]+t1[1]];
    const a=2*(1-dot(t0,t1)),b=2*dot(v,sum),c=-dot(v,v);
    const d=Math.abs(a)<1e-9?-c/b:(-2*c)/(b+Math.sqrt(b*b-4*a*c));
    const join=[(from[0]+to[0]+d*(t0[0]-t1[0]))/2,(from[1]+to[1]+d*(t0[1]-t1[1]))/2];
    const first=circularArc(from,join,t0),reverse=circularArc(to,join,[-t1[0],-t1[1]]);
    const second={...reverse,from:join,to,add:!reverse.add};
    if(first.circle&&second.circle&&Math.hypot(first.circle[0]-second.circle[0],first.circle[1]-second.circle[1])<1e-5) return [{...first,to}];
    return [first,second];
  }
  const smoothJoints=[];
  const parts=measuredParts.map(part=>{
    let from=part.start;
    const source=part.arcs.map(arc=>{const edge={...arc,from};from=arc.to;return edge;});
    const tangents=source.map((arc,i)=>{
      const previous=source[(i+source.length-1)%source.length];
      const incoming=originalTangent(previous,arc.from),outgoing=originalTangent(arc,arc.from);
      // Large direction changes identify intentional tips/corners, not smooth joins.
      if((!previous.circle&&!arc.circle)||dot(incoming,outgoing)<Math.cos(Math.PI/3)) return null;
      const tangent=!previous.circle?incoming:!arc.circle?outgoing:unit([incoming[0]+outgoing[0],incoming[1]+outgoing[1]]);
      return tangent;
    });
    const arcs=source.flatMap((arc,i)=>{
      if(!arc.circle) return [arc];
      const t0=tangents[i]||originalTangent(arc,arc.from),t1=tangents[(i+1)%source.length]||originalTangent(arc,arc.to);
      return biarc(arc.from,arc.to,t0,t1);
    });
    // Generated biarc midpoints are smooth as well as the adjusted design vertices.
    arcs.forEach((arc,i)=>{
      const next=arcs[(i+1)%arcs.length];
      if(!arc.circle&&!next.circle) return;
      const t0=originalTangent(arc,arc.to),t1=originalTangent(next,next.from);
      const angle=Math.atan2(Math.abs(cross(t0,t1)),dot(t0,t1))*180/Math.PI;
      if(angle<.001) smoothJoints.push({point:arc.to,tangent:t0,error:angle});
    });
    return {...part,arcs};
  });

const number=value=>Number(value.toFixed(8));
const pathFor=part=>'M '+part.start.map(number).join(' ')+part.arcs.map(arc=>arc.circle
  ?' A '+number(arc.circle[2])+' '+number(arc.circle[2])+' 0 0 '+(arc.add?1:0)+' '+arc.to.map(number).join(' ')
  :' L '+arc.to.map(number).join(' ')).join('')+' Z';
export {W,H,parts,smoothJoints,pathFor};
