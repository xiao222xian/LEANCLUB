# LC-PAY-01 支付生命周期本地验收

日期：2026-10-23（Asia/Shanghai）。范围：支付领域模型、持久化协调表、显式迁移、原活动及会员订单回归。这里只验证本地系统逻辑；企业短信、微信支付商户尚未开通，运行时仍未挂载支付适配器或自动任务。

## 本轮结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 支付生命周期及迁移专项 | 39 项通过，13.222 秒 | [原始日志](payment-core-targeted-20261023.log) |
| Python 全量回归 | 229 项，228 通过、1 跳过，79.602 秒 | [原始日志](payment-core-full-20261023.log) |

跳过项为 `test_filesystem_media_symlink_is_rejected`：当前 Windows 账户不能创建符号链接。没有将该项算成通过。日志是本机文件，按仓库现有 `*.log` 规则忽略，不随源码提交；本页保存可跟踪的验收摘要。

执行命令（工作目录为本仓库）：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_payment_lifecycle.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

全量覆盖原投票、反馈、活动、会员归属与签名桥接、管理桥接、公众网关、加密备份及本次支付核心。测试使用临时 SQLite、合成身份和进程内 `tests/payment_fake.py`，没有读取生产数据库或发送真实短信、收款、退款和通知。

## 已验证的业务约束

- `pending_payment / closing / paid / refund_pending` 均占位；读取接口不凭本地时间释放名额，只有可信关单、原子“从未发送”创建围栏或确认退款成功才能释放。
- 一个报名订单只绑定一个固定商户订单号、attempt、渠道订单号和报价快照。创建前持久化 `started`；创建异常、worker 崩溃或租约到期只查原 attempt，不因查无订单重新创建。
- 用户取消与活动取消优先于超时；取消和付款并发、旧 worker 晚回、关单后迟到付款不会恢复票券，需退款时立即进入 `refund_pending` 并保留占位。
- 付款必须匹配固定 provider、merchant、app、币种、金额、attempt、渠道交易号及可信来源。同商户同渠道交易号不能支付两张订单；同事件号不同正文拒绝，重复或乱序事件不重写已确认时间。
- 退款使用固定 refund_id；未知、失败和租约重试保留名额并查询原退款。只有可信退款成功释放；同 provider/merchant 的退款渠道引用不能释放两张订单。
- 晚付款发生在名额已补位之后，公开和管理活动数据给出 `capacityConflict / overbookedCount`，记录待核对异常，并阻止继续超卖。
- 网络调用不持有 SQLite 写事务；旧 worker 可提交真实资金证据，但不能确认或覆盖新租约，并服从最新取消意图。
- 原会员订单归属、跨会员隔离、一次提交幂等、核销资格及已核销二维码隐藏继续通过。旧匿名新报名 HTTP 接口为 410，原记录读取及原浏览器取消保留。

## 本次补强

复读现有独立审查后，补充了关单前的任务绑定校验：若适配器误把另一合法订单的“待付”查询结果返回给当前 worker，在发出任何关单调用前即拒绝。此前 `finish` 已能拒绝错绑定结果，本次进一步避免在 `execute` 阶段先产生关单副作用。新回归验证未发出 `close`，两笔原状态不变。

新增退款渠道引用冲突回归：第一张订单退款成功后，同作用域同退款渠道引用不能使第二张订单释放；第二张继续 `refund_pending` 并留审计异常。

## 迁移和运行边界

[迁移工具](../scripts/migrate_payments.py) 仅增添六张协调表及版本标记，不改原订单任何字段、归属、签名密钥或票券。四项专项验证：启动/只读预检不迁移；备份后迁移保留数据与票券、重复执行无改动；中途失败整体回滚且保留备份；发现半套表结构拒绝自动补齐。

本任务没有迁移或重启正在运行的数据库。根任务取得冻结信号后，仍需停写、显式备份迁移、启动原配置并做真实 API 回归。此处的迁移验收不能替代运行库切换记录。

`PaymentWorker` 是显式调用的单步组件，应用没有调度启动它；`CampaignEvents.payment_adapter` 仍为 `None`，`paymentReady` 为 false，真实报名收费保持关闭。芋道 PAY 适配、渠道验签/查单/关单证明、支付唤起参数、真实退款及定时补偿仍需按 [适配方案](../../../04-products/leanclub-admin/PAYMENT-ADAPTER-NEXT.md) 独立实现与验收，不能只填商户密钥即视为已接通。

## 本次核心文件指纹

| 文件 | SHA-256 |
| --- | --- |
| `backend/payment_lifecycle.py` | `e04682a9deb4f0d7e4ffdf34f982453bb3f67e85fa6d3aef77e856ed42cd609f` |
| `backend/payment_schema.py` | `52bf266d30dba93119ca85a92362093582693e54f1752955bee372d2be5f6c53` |
| `backend/campaign_events.py` | `92c5c51959a291c73366c6563283ae7a4b15d86e50efbd5d2d6dcbd4dd70466d` |
| `scripts/migrate_payments.py` | `9eb00dbb11110d5a1b606167ba86d440e0215a8da1977a384ac29d0a40d5a3f0` |
| `tests/payment_fake.py` | `19a81d986ad1c240e7a40b70730a791d939d351579ad74d7c68475247cf7b848` |
| `tests/test_payment_lifecycle.py` | `825fc69c2cef82dfce4d151757df8cc42f97ee3575eb903dcdc930b8c6abc02c` |
