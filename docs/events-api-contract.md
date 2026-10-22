# 付费活动报名接口（第一版，2026-10-23）

所有接口同源 JSON，错误 `{error:中文说明}`；管理员写操作沿用 `X-CSRF-Token`。活动默认草稿，无自动创建的公开示范活动。收款渠道未配置时 `paymentReady=false,registrationOpen=false`，后端拒绝创建订单；不存在公开模拟付款成功接口。

## 活动

`event`: `id,title,subtitle,description,city,venue,address,startsAt,endsAt,registrationClosesAt,capacity,status,posterCity,priceFen,currency,createdAt,updatedAt,registeredCount,checkedInCount,pendingCount,refundPendingCount,remaining,registrationOpen,paymentReady`。

- 城市 `shenzhen|zhuhai|guangzhou`；活动状态 `draft|published|closed|cancelled`。
- 时间带时区 ISO 8601，后端返回 UTC Z。结束晚于开始，截止不晚于开始；首次公开需要未来开始/截止时间。
- `priceFen` 整数分 1–10000000，币种输出固定 `CNY`，订单保留创建时金额快照。修改活动售价不影响历史订单。
- 容量 1–10000。`registeredCount` 仅已付款（含已核销），`pendingCount` 仅未过期待支付；`refundPendingCount` 为待退款且仍保留名额；remaining=容量减三者。待支付保留30分钟或报名截止前，取更早时间，过期释放。
- 标题2–60，副标题0–100，说明0–5000，场地2–100，地址2–200。创建完整填写上述编辑字段（currency等只读不提交），status缺省draft，subtitle/description缺省空。更新支持部分字段。
- 已有订单不能退回草稿。取消活动使待支付订单取消、已付订单进入待退款；所有票即时失效。已取消活动不能重新启用。

## 订单与入场券

`registration`: `id,ticketNumber,name,phoneMasked,status,createdAt,checkedInAt,cancelledAt,paymentStatus,amountFen,currency,paymentExpiresAt,paidAt`。管理员额外返回 `phone`。`status=registered|checked_in|cancelled` 仅用于现场状态；是否成功报名必须看paymentStatus。

`order`: `id,status,amountFen,currency,expiresAt,paidAt`，status为 `pending_payment|paid|expired|cancelled|refund_pending|refunded`。当前收款适配器尚未选定，不接受客户声明paid。

`ticket` 响应：`{event,registration,order,orderToken,orderUrl,ticketToken,ticketUrl,checkInUrl,qrDataUrl,canCancel}`。

- orderToken为高熵HMAC凭证，orderUrl/ticketUrl为同源 `/ticket#ticket=<orderToken>`。
- 未付、过期、取消、退款状态 `ticketToken/checkInUrl/qrDataUrl=null`；仅paid生成PNG二维码。原二维码在退款申请/取消活动后也无法核销。
- 二维码内容同源 `/check-in#ticket=<token>`。全部token放fragment，不放query，不写日志；二维码由本地qrcode==8.2生成，无外部服务。
- 原报名浏览器在未核销、活动开始前可取消。未付直接取消并释放；已付转refund_pending，票立即失效，待实际退款确认后才refunded并释放名额。退款申请不等于退款完成。
- 首版未接支付适配器。后续渠道需要校验真实到账、金额、交易唯一性；超时不等于支付侧已关单。迟到到账需对账/关单/退款异常处理，不可静默丢失或在无名额时发票。

## 公开接口

- `GET /api/campaign/events` → `{events}`，仅非草稿，按开始时间。
- `GET /api/campaign/events/{id}` → `{event}`，草稿404。
- `POST /api/campaign/events/{id}/register` body `{requestId:UUID,name,phone,consent:true,expectedPriceFen}` → ticket加`duplicate`，首次201，幂等200。expectedPriceFen必须是弹层打开时用户看到并确认的整数分报价，事务内与当前价格不一致返回409要求刷新确认；原request幂等重放优先返回原价订单。姓名2–30，手机号中国大陆11位；consent表示确认价格和取消说明、同意必要活动联络与现场核验。仅同访客可恢复原订单，不能凭手机号跨浏览器取票。同活动同手机只能一份有效订单。
- `POST /api/campaign/events/ticket` body `{orderToken}`（也兼容`{ticketToken:orderToken}`）→ ticket。持凭证只看脱敏票面。
- `GET /api/campaign/events/mine` → `{tickets}`，当前浏览器订单，含待支付/取消，最新在前，最多100。
- `POST /api/campaign/events/cancel` body `{orderToken}`（同样兼容ticketToken）→ ticket。仅原报名浏览器，取消/退款申请幂等。

## 管理接口

- `GET /api/admin/events` → `{events}`含草稿。
- `POST /api/admin/events` → `{event}`，201。
- `POST /api/admin/events/{id}` → `{event}`，200。
- `GET /api/admin/events/{id}/registrations?page=1&status=all` → `{event,items,total,page,pageSize:20,totalPages,stats}`。status可以all、registered、checked_in或任意支付状态。items为管理员registration。
- `stats` 包含 `registered`（已付款未入场）、`checkedIn`、`total`，以及六种支付状态同名键（pending_payment/paid/expired/cancelled/refund_pending/refunded）。paid含已核销，不要与registered相加。
- `POST /api/admin/events/check-in/preview` body `{code}` → `{event,registration,eligible,reason}`。code可为token、人可读LC票号或本站完整checkInUrl。只有paid、未核销、非取消活动、开始前2小时至结束时间内 eligible=true。
- `POST /api/admin/events/check-in` body `{code,requestId:UUID}` → `{event,registration,duplicate}`。原子核销，重复duplicate=true显示原核销时间。扫码仅预览，管理员点击确认才入场，不提供GET核销。

前端所有用户/活动文字使用textContent。HTTP下相机可能不可用，提供手输票号/扫码器粘贴。手机号只对管理端和必要活动履约使用，不上公共列表或推送。本轮保留已有飞书意见/投票推送，不新加报名推送。

二维码依赖核实来源：[PyPI qrcode 8.2](https://pypi.org/project/qrcode/8.2/)，BSD许可证，2026-10-23核实。真实支付及退款尚未接入，不能宣称已验收真实收款。
