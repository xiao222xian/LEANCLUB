import assert from 'node:assert/strict';
import {generateBoundary} from '../boundary-geometry.js';

let count=0;
for(const letters of ['SR','AI','OG','NX','TL','BQ','CD','EF','HJ','KM','PU','VW','YZ']) {
  for(const weight of [14,24,38]) {
    for(const strategy of ['weave','fusion','counter','axis','embrace','tension']) {
      const geometry=generateBoundary({letters,weight,strategy,overlap:weight===14?68:24,tension:weight===38?100:0});
      assert(geometry.paths.length,`${letters} has contours`);
      assert(!geometry.paths.join('').includes('NaN'));
      for(const loop of geometry.contours)loop.forEach((edge,i)=>{
        const next=loop[(i+1)%loop.length];
        assert(Math.hypot(edge.to[0]-next.from[0],edge.to[1]-next.from[1])<1e-5,'closed edges');
        if(edge.circle)for(const p of [edge.from,edge.to])assert(Math.abs(Math.hypot(p[0]-edge.circle[0],p[1]-edge.circle[1])-edge.circle[2])<1e-5,'true circle arc');
      });
      count++;
    }
  }
}
console.log(`Passed ${count} geometry configurations: closed contours and exact circular arcs.`);
