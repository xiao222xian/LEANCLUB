/* Default discovery order; all school records and club counts still come from the live directory. */
((root) => {
  'use strict';
  function create(api) {
    const size = 100;
    const featured = ['中山大学', '深圳大学', '华南理工大学', '南方科技大学', '暨南大学', '深圳技术大学',
      '广州大学', '香港中文大学（深圳）', '华南师范大学', '深圳职业技术大学'];
    let state = null, queue = Promise.resolve();
    const fetchPage = (options) => api('/leanclub/school/list?' + new URLSearchParams(options));
    function valid(data) {
      if (!Array.isArray(data?.list) || !Number.isSafeInteger(data.total) || data.total < 0 ||
          data.list.some(s => !s || !/^\d+$/.test(String(s.id)) || typeof s.name !== 'string')) {
        throw new Error('学校目录暂时无法读取，请重试。');
      }
      return data;
    }
    function append(target, rows) {
      rows.forEach(school => {
        const key = String(school.id);
        if (!target.seen.has(key)) { target.seen.add(key); target.rows.push(school); }
      });
    }
    function rank(school) {
      const position = featured.indexOf(school.name);
      return position >= 0 ? position : featured.length + (school.level === '本科' ? 0 : 1);
    }
    async function initialize() {
      const [regional, national] = (await Promise.all([
        fetchPage({ province: '广东省', pageNo: '1', pageSize: String(size) }),
        fetchPage({ pageNo: '1', pageSize: String(size) })
      ])).map(valid);
      // This is a regional shortlist, not a download of all national pages.
      if (regional.total > 1000) throw new Error('学校目录已更新，请重新加载。');
      const remaining = await Promise.all(Array.from({ length: Math.max(0, Math.ceil(regional.total / size) - 1) },
        (_, i) => fetchPage({ province: '广东省', pageNo: String(i + 2), pageSize: String(size) }).then(valid)));
      const preferred = [regional, ...remaining].flatMap(page => page.list)
        .filter(school => /^(广州|深圳)市?$/.test(school.city || ''))
        .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name, 'zh-CN') || Number(a.id) - Number(b.id));
      const next = { rows: [], seen: new Set(), total: national.total, nextPage: 2, loadedAt: Date.now() };
      append(next, preferred); append(next, national.list);
      state = next;
    }
    async function defaults(pageNo, pageSize) {
      if (!state || pageNo === 1 && Date.now() - state.loadedAt > 300000) await initialize();
      const end = pageNo * pageSize;
      while (state.rows.length < Math.min(end, state.total)) {
        const data = valid(await fetchPage({ pageNo: String(state.nextPage), pageSize: String(size) }));
        state.nextPage += 1;
        state.total = data.total;
        append(state, data.list);
        if (!data.list.length) { state.total = state.rows.length; break; }
      }
      return { list: state.rows.slice((pageNo - 1) * pageSize, end), total: state.total };
    }
    function list({ keyword = '', province = '', pageNo = 1, pageSize = 20 } = {}) {
      keyword = keyword.trim();
      if (!Number.isSafeInteger(pageNo) || pageNo < 1 || !Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 100) {
        return Promise.reject(new Error('学校分页参数无效。'));
      }
      if (keyword || province) return fetchPage({ keyword, ...(province ? { province } : {}), pageNo: String(pageNo), pageSize: String(pageSize) }).then(valid);
      const result = queue.then(() => defaults(pageNo, pageSize));
      queue = result.catch(() => {});
      return result;
    }
    return { list };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = create;
  else root.LeanSchoolDirectory = create((...args) => root.LeanClubPortal.api(...args));
})(typeof window === 'undefined' ? globalThis : window);
