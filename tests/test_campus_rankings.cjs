const test = require('node:test');
const assert = require('node:assert/strict');
const {prepare, filter} = require('../web/creator/campus-rankings.js');
test('only published records rank; seed counts never resurrect schools', () => {
  const result = prepare([{id:'one',name:'测试大学',count:10}], [{id:'one',name:'测试大学',count:999,region:'中国大陆'}, {id:'gone',name:'已退出',count:500}]);
  assert.equal(result.length,1); assert.equal(result[0].count,10); assert.equal(result[0].region,'中国大陆');
});
test('ties retain competition ranks while filters keep global ranks', () => {
  const result = prepare([{id:'a',name:'甲大学',count:30,region:'美国'}, {id:'b',name:'乙大学',count:20,region:'中国大陆',alias:'GZ'}, {id:'c',name:'丙大学',count:20,region:'中国香港'}, {id:'d',name:'丁大学',count:10,region:''}]);
  assert.deepEqual(result.map(x=>x.rank),[1,2,2,4]);
  assert.equal(filter(result,'china','gz')[0].rank,2);
  assert.equal(filter(result,'china','').length,2);
  assert.equal(filter(result,'overseas','').length,1);
});
test('renamed schools do not inherit old identity metadata', () => {
  const result=prepare([{id:'a',name:'新学校',count:4}], [{id:'a',name:'旧学校',en:'wrong',region:'美国'}]);
  assert.equal(result[0].en,''); assert.equal(result[0].region,'');
});
