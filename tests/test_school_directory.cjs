const { test } = require('node:test');
const assert = require('node:assert/strict');
const create = require('../web/creator/school-directory.js');
const school = (id, name, city = '北京市') => ({ id, name, city, level: '本科', clubCount: 0 });
function fixture({ fail = false } = {}) {
  const schools = Array.from({ length: 250 }, (_, i) => school(i + 1, '学校' + (i + 1)));
  schools[3] = school(4, '深圳大学', '深圳市');
  schools[120] = school(121, '中山大学', '广州市');
  schools[190] = school(191, '珠海大学夹具', '珠海市');
  schools[210] = school(211, '华南理工大学', '广州市');
  const regional = [schools[3], schools[120], schools[190], schools[210]];
  const calls = [];
  const api = async path => {
    const params = new URL(path, 'http://fixture').searchParams; calls.push(params);
    if (fail) { fail = false; throw new Error('offline'); }
    const matching = params.get('province') ? regional : params.get('keyword') ? schools.filter(s => s.name.includes(params.get('keyword'))) : schools;
    const size = Number(params.get('pageSize')), start = (Number(params.get('pageNo')) - 1) * size;
    return { list: matching.slice(start, start + size), total: matching.length };
  };
  return { directory: create(api), calls, schools };
}
test('default homepage promotes live Guangzhou and Shenzhen schools, not other cities', async () => {
  const { directory, calls } = fixture();
  const result = await directory.list({ pageSize: 6 });
  assert.deepEqual(result.list.slice(0, 3).map(s => s.name), ['中山大学', '深圳大学', '华南理工大学']);
  assert.equal(result.total, 250); assert.equal(calls.length, 2);
});
test('load-more preserves all records exactly once across priority and national pages', async () => {
  const { directory, schools } = fixture();
  const rows = [];
  for (let pageNo = 1; pageNo <= 13; pageNo++) rows.push(...(await directory.list({ pageNo, pageSize: 20 })).list);
  assert.equal(rows.length, 250);
  assert.deepEqual([...new Set(rows.map(s => s.id))].sort((a, b) => a - b), schools.map(s => s.id));
});
test('keyword search bypasses regional ordering and goes directly to the live API', async () => {
  const { directory, calls } = fixture();
  const result = await directory.list({ keyword: ' 深圳 ', pageSize: 6 });
  assert.equal(result.list[0].name, '深圳大学'); assert.equal(result.total, 1);
  assert.equal(calls.length, 1); assert.equal(calls[0].get('keyword'), '深圳');
});
test('concurrent default requests share initialization and recover from request failure', async () => {
  const { directory, calls } = fixture();
  const [home, page] = await Promise.all([directory.list({ pageSize: 6 }), directory.list()]);
  assert.equal(calls.length, 2); assert.equal(home.list[0].id, page.list[0].id);
  const retry = fixture({ fail: true });
  await assert.rejects(retry.directory.list());
  assert.equal((await retry.directory.list()).list[0].name, '中山大学');
});
