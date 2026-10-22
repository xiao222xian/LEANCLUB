# 活动报名用户侧验收 · 2026-10-23

本轮在本机 `http://127.0.0.1` 的独立 QA 数据库与合成支付适配器上验收。所有演示活动、称呼、手机号、订单和二维码均为本地测试资料。没有真实收款、退款或飞书消息，没有创建生产活动；正式支付渠道未配置时仍禁止下单。

## 实际浏览器流程

- 城市投票页新增“线下见”，保留投票、意见箱与原有导航。主页未增加入口。
- 无已发布活动时展示全屏三城人像空态，不编造时间或场地。
- 活动详情显示发布数据、费用、剩余名额、时间地点和可展开安排；桌面人物移至右侧，保留完整头脸，移动端居中展示。
- 本地浏览器实际填写称呼、合成手机号与告知同意后创建订单。订单为 `pending_payment`；页面没有二维码，二维码 `src` 为空，也没有可用的保存入场券按钮。
- 后端协作者仅在 QA 库使用可信测试 fixture 将该单设为合成到账。用户侧刷新后显示 `paid` 入场券、短票号、二维码及脱敏手机号。此步骤不代表已接通任何真实支付渠道。
- 实际点击“保存入场券”，浏览器生成并下载 PNG；已从 Downloads 读取实际文件并检查图片。最终版本为 1000 × 1870 像素、866911 字节，海报头脸完整，费用、二维码、时间地点、脱敏参与者与页脚完整。生成与排版在浏览器本地进行，无第三方二维码或截图服务。
- 电子票提供取消/退款申请确认层；已付取消文案明确为申请退款，不宣称退款完成。模拟状态回归验证申请后立即隐藏入场二维码。

## 响应式与视觉证据

使用真实页面的 390 / 320 CSS 像素 iframe 验收，手机活动主按钮、价格、时间地点、城市海报与电子票二维码均可见，未出现水平滚动条。独立票页还读取到 `innerWidth=390, scrollWidth=373`（含垂直滚动条）。原浏览器 viewport override 在长页面截图上出现工具超时与黑色裁切，已恢复默认视口，改用真实窄 iframe 留证；临时验收 HTML 已删除。

- [无活动空态](evidence/events/public-empty-desktop.png)
- [桌面活动详情，最终构图](evidence/events/public-event-desktop.png)
- [窄屏报名弹层](evidence/events/public-register-narrow.png)（此张宿主缩放后实际约 433 CSS 像素）
- [390 / 320 活动与 390 电子票](evidence/events/public-mobile-390-320.png)
- [待支付订单，未显示入场码](evidence/events/public-order-pending-desktop.png)
- [合成到账后的票面与下载提示](evidence/events/public-ticket-desktop.png)
- [实际下载的最终入场券 PNG](evidence/events/public-saved-ticket.png)

## 自动状态回归

在项目根目录运行：

```text
node tests/test_events_public.cjs
node --check web/creator/events/common.js
node --check web/creator/events/app.js
node --check web/creator/ticket/app.js
```

纯 Node VM 状态回归通过，覆盖：六种支付状态的二维码/保存按钮门控；缺失订单、活动取消、取消票、外部二维码地址拒显；已核销展示；支付未配置禁用报名；活动文本只用 textContent；告知同意；失败保留草稿；同内容失败重试复用 requestId；修改内容重新生成 requestId；待支付使用 orderToken 跳转；退款申请文案；弹层冻结 expectedPriceFen，价格变更 409 后保留内容，用户主动查看新价后需要重新同意；并发 API 先等待唯一身份 bootstrap，CMS 发布海报供活动和票面使用。

未验收范围：真实支付回调、支付 SDK / 微信内浏览器支付、实际退款回调、真实手机相册下载兼容性。当前验证浏览器为桌面 Edge 与窄屏 CSS 布局。
