# 会员订单 Python 实施与隔离验证

日期：2026-10-23。范围：LC-MO-01 / LC-MO-02 的 Python 部分。当前生产与运行库未由本实现任务修改，重启、真实 Java API 联调和本地运行库迁移由主任务统一执行。

## 已实现

- `campaign_events.py`：显式 `(member_tenant_id, member_user_id)` 归属、严格输入、同会员业务命令幂等、跨设备本人列表/详情/取消。名称/手机是联系字段，不用来认领订单。
- `member_orders_schema.py`：新空库直接采用当前结构；旧库启动仍使用原结构，不自动重建。旧库的会员接口在显式迁移前返回503。
- `scripts/migrate_member_orders.py`：停写确认、私有一致备份、单事务重建、原字段逐行比较、外键/完整性校验、重复无操作；未知额外字段、依赖外键/视图/触发器要求先人工审查。
- `member_orders_bridge.py`：独立LCM1用途签名、固定四动作、固定租户/MEMBER类型、原始正文绑定、60秒时窗、持久nonce、32KiB请求/2MiB响应限制、每会员每分钟30次写限制。只接受已认证服务调用，不接受客户端memberId。
- `server.py`：仅新增固定私有路由 `/internal/leanclub/member-orders`；无密钥503，不设置假支付、免费绕过或认领接口。
- 旧匿名秘密票券保留，但旧mine/ticket/cancel不读取或控制会员归属单。member详情不返回orderToken/ticketToken；分页不返回二维码/秘密链接。会员票通过登录后的 `/ticket?order=<id>` 查询，核销仍复用原员工业务。

环境变量：`LEANCLUB_MEMBER_ORDERS_SECRET` 为64位小写hex，HMAC按该文本UTF-8字节使用；`LEANCLUB_MEMBER_ORDERS_TENANT` 默认为1。独立密钥不能与运营员工域混用。签名canonical为 `LCM1\nPOST\n/internal/leanclub/member-orders\n{timestamp}\n{nonce}\n{tenant}\nMEMBER\n{actor}\n{sha256(rawBody)}`，无末尾换行。头除已有Timestamp/Nonce/Tenant/Actor/Signature外，还必须有唯一 `X-LC-Actor-Type: MEMBER`。

## 显式迁移

从官网仓库目录执行，先只检查；备份必须在网页目录之外，推荐使用已忽略的私有runtime目录：

```powershell
& .\.venv\Scripts\python.exe scripts/migrate_member_orders.py --db data/integration-v5/local.sqlite3 --backup-dir E:\work\natie\05-lab\yudao\runtime\backups\member-orders
```

确认本领域服务和其他写入者已停止后才应用：

```powershell
& .\.venv\Scripts\python.exe scripts/migrate_member_orders.py --db data/integration-v5/local.sqlite3 --backup-dir E:\work\natie\05-lab\yudao\runtime\backups\member-orders --apply --confirm-service-stopped
```

脚本返回备份路径及行数，不打印联系人、订单内容或秘密。备份包含原完整业务数据，应按私有数据库管理，不提交Git。应用启动不会帮旧库执行这一步。若备份期间其他连接写入，脚本拒绝继续；`--confirm-service-stopped` 是操作人确认，不是自动停服务工具。

迁移保留原订单ID、票号、visitor、金额/付款引用及 `events_ticket_secret`。任何校验失败均回滚源库，保留私有备份。已有新会员单后不能直接换回不识别归属的旧程序；需恢复迁移前备份或使用兼容版本，不能删字段冒充无损回滚。

## 实测结果

本轮四组共 **62项通过、0项失败**，均使用临时数据库或随机本地HTTP端口：

| 测试 | 数量 | 重点 |
| --- | --- | --- |
| `test_member_orders.py` | 20 | 新归属、跨设备、同手机号隔离、类型/字段严格、并发同请求、与匿名订单竞争最后名额、旧票隔离、分页、取消/退款申请、迁移/回滚/不自动迁移 |
| `test_member_orders_bridge.py` | 13 | Java/Python共同签名向量、用途/类型/密钥隔离、重复头、时窗、持久重放拒绝、上限、限频、结果不明后用原业务key恢复、实际私有HTTP路由 |
| 原 `test_campaign_events.py` | 19 | 原活动/匿名报名/报价/容量/票券/核销及HTTP回归 |
| 原 `test_operations_bridge.py` | 10 | 原员工运营签名、权限动作边界、活动CAS及脱敏回归 |

首轮迁移测试发现Windows下SQLite上下文管理器不会自动关闭连接，导致4项临时文件清理失败；已显式关闭备份与测试连接，重跑20项全部通过。没有把初始失败隐藏为首次全过。

证据：[member-orders-python.json](../../../04-products/leanclub-admin/evidence/business-v5/member-orders-python.json)。测试中的paid和`.ready=True`仅为隔离进程内替身，用于验证账号归属/票券状态，不是运行时接口，不产生真实付款。

## 保留边界

- 实际服务仍无支付适配器；新报名返回409，`paymentReady=false`。用户已确认短信和支付都尚未开通，当前继续开发系统，不要求商户密钥。
- 本切片没有改原超时/退款状态模型。真实收费前必须按 [MEMBER-ORDERS-NEXT.md](../../../04-products/leanclub-admin/MEMBER-ORDERS-NEXT.md) 实现closing占位、渠道查单/关单、到账/退款确认、持久任务和补偿；不能把现有按时间释放的测试当真实渠道竞态已解决。
- 历史匿名认领后置，无手机号自动归属、无普通员工代为改属入口。
- 本记录不替代主任务的运行库迁移、会员真实API验收、浏览器验收、正式短信/支付或生产发布记录。
