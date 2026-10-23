# 既有 HTTPS 官网全栈升级

记录：2026-10-24。此文描述已实现、在临时库和模拟服务上验证的升级工具，不代表生产执行完成。实际发布提交、服务器结果和证书验收由部署记录确认。

入口为 `deploy/ubuntu/upgrade-fullstack-site.py`。仅升级 `/opt/bojizhuye` 官网与公共 Java API 网关，不安装 Java/MySQL/Redis，不签发证书，不复制本机业务数据，不建立管理员，也不生成通知或测试订单。

## 顺序与依赖

1. 先部署并启动已固定版本的 Java，确保 tenant 1 的公开站点接口和高校目录能返回成功结果；学校目录必须非空。先发布 `/opt/leanschool/ui/current/console/index.html` 及其 JS/CSS 资源，构建 base 必须为 `/console/`。两项不通过则旧站不停止。
2. Java 环境文件须有不同的 `LS_OPERATIONS_SECRET`、`LS_MEMBER_ORDERS_SECRET`，各为64位小写十六进制。真实文件必须 root 所有、权限0600或更严格；命令只传文件路径。脚本只映射这两项到 Python 的 `LEANCLUB_*_SECRET`，固定两个桥接租户为1，不复制 Java 数据库/Redis凭据。Java桥接开关可先关闭，由整套发布流程在 Python 就绪后另行启用并验证。
3. 上传同一 Git 提交的源码归档、独立核对的 SHA-256 及部署脚本。需要最新的 `install.py` 与升级器放在同一目录。升级器调用现有安装器的 `archive_digest`、`validate_archive` 与共用 `prepare_release`：校验 tar pax Git commit、路径、成员类型、运行库/凭据排除，再提取和安装固定 Python 依赖；不会调用 HTTP 安装器的 `main()`。
4. 检查旧 unit、ENV、`current`、Nginx 归属；确认5381监听者只有 `bojizhuye.service` 的 MainPID。准备新 release 时旧站继续工作。
5. 再次检查 Java/console；保存配置与旧 `current`，停止旧 Python 服务及其中的唯一 worker。检查服务停止、5381可绑定，且 `/proc` 中没有其他进程打开本站 DB/WAL/SHM，才通过 SQLite backup API 生成一致性备份。
6. 用新 release 的 `CampaignStore` 初始化新增表，但不导入 `server.py`、不启动 worker；随后依次显式执行 `migrate_member_orders.py`、`migrate_payments.py`。既有会员旧schema按原迁移器逐字段验证后重建，支付schema按原迁移器新增；已完成的schema为no-op。初始化或迁移失败时仍修复DB/WAL/SHM属主，便于旧服务恢复。
7. 保留原 ENV 的路径、端口、可信代理、初始化禁用和其他设置，仅合并明确的新主域/兼容域及桥接配置。保留旧网站unit；安装网关unit/env；切换 `current`，启动 Python 与5382网关，检查两个Host的健康、管理员已存在且公网初始化关闭、学校接口真实可读，再reload Nginx。
8. 用 `curl --resolve` 在本机进行旧域真实 SNI/TLS 校验，检查首页、投票、旧 `/admin`、`/console/` 与学校接口；不会关闭TLS验证，也不会写投票、留言或订单。最后启用网关开机启动。新域证书、vhost与公网检查属于独立步骤。

示例（占位项须替换为同一发布包的信息）：

```sh
sudo python3 /data/site-deploy/upgrade-fullstack-site.py \
  --archive /data/leanschool-site-<commit>.tar.gz \
  --sha256 <独立核对的64位SHA256> \
  --revision <40位小写Git提交> \
  --bridge-env /etc/leanschool/java.env
```

此工具固定现有生产布局：网站5381、网关5382、Java48080，SQLite与媒体在 `/var/lib/bojizhuye/`。遇到不同路径、租户、额外兼容域、未知配置标记、重复环境变量或非本项目网关配置会停止，不能用跳过检查的方式套用其他站点。

## 旧域与新域

Python和网关均明确配置：

```ini
CLUB_ORIGIN=https://www.leanschool.net
CLUB_COMPAT_ORIGINS=https://boji.tensorbot.xyz
```

每个Host只接受自己的Origin；新旧域共享数据和一个worker，但匿名Cookie仍各属本域。旧Cookie可在旧域改投；新域的新Cookie与原票同IP时不会取得改投权。详见 [双域来源边界](domain-compatibility.md) 和 [公共网关](public-gateway-production.md)。

升级器只对旧HTTPS server块加入独立管理片段：`/app-api/` 经5382的严格白名单代理，`/admin-api/`到48080，`/console/`使用 `/opt/leanschool/ui/current` 为root。缺失JS/CSS/字体/图片与assets/brand资源返回404，只有前端路由回退到console首页；隐藏文件拒绝。新增API location不记录可能带令牌的查询日志。旧证书、SSL配置、HTTP跳转、`/api/`与原 `/admin` 静态登录入口保留。旧版缺少的events/ticket HTML路由及check-in到console跳转按需补入。

首页搜索与新学校列表使用 `/app-api/leanclub/school/list`。失败展示错误和重试，不把旧目录冒充完整高校字典。历史滚动校名按钮仍查 `/api/club/schools`，历史 `?school=` 链接只访问经过ID校验的 `/api/club/profile/{id}`，明确标注“尚未绑定标准学校或完成新平台审核”。新标准学校使用 `?schoolId=`，两套ID不混用。

## 备份与失败恢复

备份在服务器私密目录 `/var/backups/bojizhuye-fullstack/<时间-随机标识>/`：目录0700，配置与SQLite备份0600。`before.sqlite3` 包含停止时已提交的WAL数据，并进行完整性、外键检查；原迁移器需要时还会在同目录生成自己的迁移前备份。备份**没有加密**，依赖服务器root私密权限，严禁放入Git、发布tar包或公网目录。若需离开服务器，必须另行使用受控加密备份流程。本工具不备份媒体内容，也不修改它；完整灾备还需覆盖媒体。

失败时先停新进程，再恢复本轮原代码symlink、ENV、unit、旧Nginx及原网关状态；恢复后的Nginx通过 `nginx -t` 才reload。原站必须在升级前处于active状态，因此正常回退会恢复旧网站进程。若停止进程、恢复控制文件/current或daemon-reload失败，保持已停止的服务停止，不用混合的新旧配置重新启动，也不reload Nginx，明确报告回退不完整并保留现场。

**不自动恢复数据库。** 新版本可能已经接受请求，复制旧库会丢掉这些写入。重新恢复库必须停所有写入者，先备份故障时现库，评估新增订单、评论及通知队列，再人工选择恢复策略；不能把业务数据库回档当代码回退。

固定生产 `4499b018296c26cee6e0b2d728241ba2149835d0` 没有活动/会员/公开评论模块。2026-10-24隔离实测：用该提交全部backend源码打开含新 `visitor=NULL` 会员订单的升级库，旧管理员登录、原Cookie改投、私密意见仍可用，会员订单所有列保持一致。**这只说明回退后旧站基础功能兼容**：旧代码不会提供新events/member/comments接口，Java桥接新功能会不可用；整套发布流程需同时关闭或隔离相关入口，不能宣称会员订单功能在旧版可用。任意其他历史版本及外部自定义schema未据此获得降级保证。

## 已验证边界

`tests/test_upgrade_fullstack.py` 的21项隔离测试覆盖：凭据映射与不泄漏、ENV重复拒绝、旧证书/管理入口/跳转保留、Nginx补丁幂等、console资源与挂载目录检查、Java非空目录就绪、唯一Python进程及占用DB拒绝、归档版本/摘要共用提取、WAL备份、无活动旧库初始化、含实际合成旧订单的显式迁移、依赖失败零停服、正常执行顺序、失败时属主修复、控制配置恢复而保留已写业务库，以及控制配置恢复不完整时禁止重启。

这些测试用临时数据与模拟systemd/Nginx。尚不代替Ubuntu上实际 `nginx -t`、systemd启动、外网HTTPS、用户登录与真实业务验收。脚本不会发送短信、消息或支付请求；恢复运行后的既有通知worker会继续处理原有合法队列。
