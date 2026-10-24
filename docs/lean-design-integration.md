# 薄肌设计：官网接入与发布

2026-10-24补发：此前未提交的接入工作本轮统一提交并纳入com部署，详见[补发记录](design-schools-20261024.md)。下文“本地/未发布”描述保留此前阶段背景；com使用独立edge发布工具和路由，未重跑旧服务器首次接入脚本。

> 最新路线：用户要求直接复用原页面。frontend/build改为复制original中的8个静态文件，/design不再加载Vue运行包；下文路由与发布边界仍有效。此前Vue实现和验证为历史。

更新：2026-10-24（Asia/Shanghai）。本轮是本地开发与验证，未推送、部署或修改生产 CMS、数据库。

## 代码与访问边界

- 公众 Vue 工程位于 `frontend/`；Vite 的生产 base 为 `/design/`，输出到 `web/creator/design/`。
- 官网首页静态导航增加“薄肌设计”；CMS 快照导航也保留该产品入口。最多显示六个导航链接，保留前五个有效 CMS 项并为设计保留一项；若设计原本位于前六项则保留它的位置。同域 `/design`、`/design/` 及带查询的设计入口归一、去重，标签统一为“薄肌设计”。会员按钮继续由原会员界面管理。
- 原会员脚本只增加 `/design` 路径白名单；没有增加会员、支付、作品库等 API。首页不加载 Vue、设计字形库或布尔运算代码，原图片、布局和滚动内容代码未调整。
- `scripts/preview.py` 与新旧域 Nginx 模板均直接提供 `/design` 和 `/design/`；资产位于 `/design/assets/`。未知文件和路径返回 404，不将缺失 JS 伪装成页面 HTML。
- `upgrade-fullstack-site.py` 会在旧域受管配置块补齐缺失设计路由，原有路由保持原值，重复执行不会重复插入。

## 本地构建与检查

在 `frontend/` 执行：

```powershell
corepack pnpm install --frozen-lockfile
corepack pnpm test
corepack pnpm build
```

然后在官网仓库根目录执行：

```powershell
python scripts/check-design-build.py
node --test tests/test_design_navigation.cjs
.venv/Scripts/python.exe -m unittest discover -s tests -p test_design_integration.py
.venv/Scripts/python.exe -m unittest discover -s tests -p test_ci_release.py
.venv/Scripts/python.exe -m unittest discover -s tests -p test_upgrade_fullstack.py
```

构建目录纳入 Git；修改 Vue、字形引擎、静态字体或构建配置后，重新构建并一并提交 `web/creator/design/` 的新增、修改和删除。不要手改压缩后的产物。`scripts/check-design-build.py` 校验生产 HTML、资源前缀、缺失资产、符号链接和非公开文件；旧的固定官网提交没有设计模块时允许继续打包。

## CI 与发布

现有 `website` job 在公众前端工程存在时执行冻结安装、模块测试和生产构建；锁文件缺失会失败。随后检查整个设计产物的 Git 状态，包括新增的 hash 文件，阻止源码和提交的产物不一致。保留原网站 Python/Node 检查、Java 和管理端 jobs，未添加生产发布动作。

现有两套打包器已经把 `web/` 纳入 `website.tar.gz`，继续使用该结构。新增产物检查在打包前运行；服务器只需现有 Python/Nginx 运行环境，不在服务器安装前端开发依赖。Vue 源码保留在官网 Git 仓库，`frontend/`、`node_modules/` 不进入官网运行包。

**首发涉及 Nginx 路由模板变化，需要单独安排受审的配置与可信发布工具升级。** 现有日常发布器会拒绝 `FIXED_SITE_FILES` 中 Nginx 模板差异，这是预期保护，不能直接用普通 publish 绕过。Git 中更新 `upgrade-fullstack-site.py` 也不会自动替换服务器可信工具目录。配置升级、`nginx -t`、两域 `/design` 页面/资产/缺失资产的 HTTPS 检查及回退验证，应在正式发布时执行；本轮未执行。

构建 workflow 在私有仓库三个分支各有副本；此次只修改官网工作区版本。下次发布前需按现有 `daily-release.md` 维护流程同步 workflow 副本，否则后端/管理端分支触发的旧 workflow 不会运行新增 Vue 产物校验。

## 已执行的接入验证

- 导航测试覆盖 CMS 排序、六项上限、设计位于第八项、重复链接、非法链接、文本安全、CMS 空值和失败时静态回退、会员按钮保留。
- HTTP 测试使用临时构建夹具和本地随机端口，验证 `/design`、`/design/`、查询字符串、HEAD、JS/CSS、资源不存在与路径穿越；不依赖线上服务。
- 打包测试使用合成 Git 仓库，确认设计 HTML/JS 进入官网归档、Vue 源码不进入运行包，并保留 Git 提交来源及摘要校验。
- 旧域升级测试验证新增设计路由和重复应用幂等。Nginx 实际运行与真实云构建未在本轮接入测试中执行，不能将本地断言当作生产验收。

2026-10-24补充实际浏览器验证：本机4187提供真实生产产物，CMS发布内容加载后首页仍显示设计入口；900px导航和390px展开菜单检查通过，手机菜单实际进入 `/design/`。工作台桌面/手机、大小写及多字母输入、草稿刷新、构造播放与真实SVG/PNG透明导出已检查。截图位于 `docs/evidence/lean-design-20261024/`；这不是线上、真机或性能基准验收。
