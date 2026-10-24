# 薄肌设计：直接复用原页面

2026-10-24 最新用户决定：停止自研编辑器，仅接入 SiliRoot 原双字母设计页面。此前 Vue 扩展未通过用户视觉验收，src/ 和 tests/ 保留作历史，不作为当前发布入口。不要继续增加长名称、字族、组合标、样机或实验室。

当前源码在 original/，固定上游 677e8f8f1a3d7128d8ab831cae99d7e00ac567ce。原样保留 CSS、几何算法、六种关系、三参数、15秒五阶段、示例和SVG/PNG导出。HTML只调整资源路径、学院品牌与返回链接；generator只替换画布品牌文字。原SR图形继续作为原页面示例图标，不宣称为新创作学院标志。

```powershell
corepack pnpm install --frozen-lockfile
corepack pnpm test
corepack pnpm build
```

build按清单复制8个静态文件到 ../web/creator/design，零新增运行依赖，不加载此前Vue/Paper/字体/Worker。test运行上游234项几何检查，并校验CSS/几何字节、脚本仅品牌替换、工作区HTML未改。开发预览仍可 pnpm dev；整站用官网 scripts/preview.py，当前 http://127.0.0.1:4187/design/。

原始来源与文件摘要见 [original/upstream.json](original/upstream.json)。保留的Vue开发依赖仅用于历史代码，test:experimental不是当前页面验收。旧 [ENGINE-NOTES.md](ENGINE-NOTES.md) 为已停止方向的历史说明。

交付状态见工作区 04-products/lean-design/ORIGINAL-PAGE-20261024.md。只做本地切换，未部署。接入路由和发布边界仍见 [官网接入说明](../docs/lean-design-integration.md)。
