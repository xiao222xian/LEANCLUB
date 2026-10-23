# 全量平台安装与回退

2026-10-24。入口为 `platform-install.py`，配套文件必须来自同一发布包。本机完成Python语法和17项隔离检查；完整JAR已在Windows独立新库/Redis以prod配置、仅DML权限数据库账号冷启动通过，health UP、学校2952、表集合645未变。实测Redis为Windows5.0.14.1，**Linux安装/systemd、生产Redis6.2+ ACL和目录权限尚未执行验证**。这些步骤仍需要目标服务器验证，不能将本文件视为已部署记录。

## 安装边界与前提

安装一个包含全部 22 个业务模块的 Java 单体、原 Vue 后台静态页面、独立 Redis 实例，以及现有 MySQL 内的新数据库/专用用户。官网 Python/SQLite、域名证书、Nginx 和旧域由外层官网发布流程处理。本工具不改现有全局 MySQL/Redis 配置，不运行 apt，不覆盖旧数据库。

目标为 Linux/systemd。先独立确认已安装 Java 25、MySQL 8.0.16+、Redis 6.2+、Python 3.11+ 与 Python bcrypt，工具路径 `/usr/bin/mysql`、`/usr/bin/redis-server`、`/usr/bin/systemctl`、`/usr/bin/systemd-analyze`。Java 应位于 `/opt` 或 `/usr` 下可被服务用户读取的目录，不放 `/root` 或 `/home`，因为服务启用了 ProtectHome。外部连接器和支付渠道没有生产配置时保持关闭，功能源码仍保留。

MySQL 管理连接默认使用本机 root socket 认证。若服务器需要其他管理认证，准备 root 所有、0600 的 MySQL client option file，然后每次给 `plan`、`prepare`、`init-db` 加 `--mysql-admin-file /root/private/mysql-admin.cnf`。不得将密码放 shell 参数或提交到 Git；该配置必须明确连接本机目标 MySQL。工具只记录版本和监听端口，不输出认证内容。Java连接仅把自身MySQL会话设为固定UTC+08:00，使用URL编码 `%2B08:00`，不依赖服务器已导入命名时区表，不改数据库全局时区。

下面路径是**发布包占位路径**，执行时替换为实际上传位置。准备以下显式输入：

- `/data/leanschool-release/backend/yudao-server.jar` 与 `release.json`：后者绑定 Java 源码提交和 JAR SHA256。
- `/data/leanschool-release/admin-ui.tar.gz`：归档内部是 `console/index.html`、`console/assets/...`。同时提供发布清单里的完整 SHA256。
- `/data/leanschool-release/private-seed/`：包含 `schema-manifest.json`、001～006 SQL 和 `site-content.ready.json`。SQL 来自已审查资料，不是运行库导出；目录0700、文件0600，不进入 Git。
- `/data/leanschool-release/deploy/`：此目录内公开工具和模板。

## 1. 只读预检

在已核实资源与服务的目标主机执行：

```bash
sudo python3 /data/leanschool-release/deploy/platform-install.py plan \
  --jar /data/leanschool-release/backend/yudao-server.jar \
  --manifest /data/leanschool-release/backend/release.json \
  --ui-archive /data/leanschool-release/admin-ui.tar.gz \
  --ui-sha256 <发布清单中的64位摘要> --ui-prefix console \
  --seed-dir /data/leanschool-release/private-seed \
  --java /opt/leanschool/jdk/bin/java \
  --database leanschool --db-user leanschool_app --redis-port 6382 \
  --java-xmx-mib 2304
```

该命令读取发布物摘要、Java版本、MySQL版本/端口、现有服务状态、可用内存和磁盘，不写数据库或启动服务。在 Windows 执行仅核验文件，并明确 `runtimeChecked=false`。2304 MiB 是候选堆上限，**不是已知服务器配置或最低容量结论**；应据实调整。`prepare` 要求可用内存至少为所选堆加1024 MiB，磁盘留有发布和回退空间；不足时停止，不抢占旧站资源。

## 2. 准备独立发布目录

将上条命令的 `plan` 改成 `prepare`，保持相同输入。它核对本机端口，建立专用 `leanschool` 系统账户、版本目录和 systemd 单元，但不启动服务、不初始化数据库。

首次准备随机生成以下独立凭据到 `/etc/leanschool/platform-secrets.json`，root0600：数据库密码、Redis密码、32字符字段加密密钥、两套不同用途的桥接签名密钥。它们派生出 root0600 的 `java.env` 和 `bridge-secrets.env`。以后发布复用这些值并保留运行配置，不重新生成或覆盖已人工启用的开关。不得 `cat`、打印、截图、提交或通过普通终端输出这些文件。字段加密密钥必须随受控备份保留，不能随意轮换。

安装路径：

| 内容 | 路径 / 权限 |
| --- | --- |
| Java版本 | `/opt/leanschool/java/releases/<源码提交>-<JAR摘要前12位>/`，目录0755、文件0644 |
| 管理页面版本 | `/opt/leanschool/ui/releases/<UI摘要前16位>/console/`，目录0755、文件0644 |
| 当前版本 | `/opt/leanschool/java/current` 与 `/opt/leanschool/ui/current` 软链接 |
| 私有初始化资料 | `/var/lib/leanschool/install/<schema摘要>/`，root0700/0600 |
| Java和Redis运行数据 | `/var/lib/leanschool/java`、`/var/lib/leanschool/redis`，服务账户0700 |
| 运行配置 | `/etc/leanschool/`，可遍历0711；secret仍root0600，生产YAML0644，RedisACL配置root:leanschool0640 |

Redis 使用127.0.0.1:6382、独立systemd、独立AOF目录与密码ACL，不接管已有6379服务。Java监听127.0.0.1:48080，健康端口48081。所有新目录权限显式设置，不依赖调用者 umask。

若首次 `prepare` 中途失败，可能留下部分专用文件/系统账户；再次遇到“unregistered”会拒绝继续。先核对状态和所有权，完成或恢复本次安装，不能通过删除判断条件强行覆盖已有安装。正常完成后的重复 `prepare` 可复用同一发布物；数据库身份和端口必须不变，现有运行配置保留，配置变更单独审查。

## 3. 独立初始化管理员和新数据库

管理员口令只输入私有文件或交互输入，不放命令参数。可在root shell中使用下面的提示输入，创建一次性文件；口令至少16字符、至少8种不同字符，UTF-8不超过72字节：

```bash
sudo python3 - <<'PY'
import getpass, os
from pathlib import Path
folder = Path('/root/leanschool-private')
folder.mkdir(mode=0o700, exist_ok=True)
os.chmod(folder, 0o700)
first = getpass.getpass('New administrator password: ')
second = getpass.getpass('Repeat password: ')
if first != second:
    raise SystemExit('Passwords differ; no file written')
fd = os.open(folder / 'admin-password.private', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, 'w', encoding='utf-8') as stream:
    stream.write(first)
PY
```

确定真实管理员用户名和租户到期日后执行，例如用户名 `opsadmin`、到期日按实际运营约定填写；工具不使用上游默认密码：

```bash
sudo python3 /data/leanschool-release/deploy/platform-install.py init-db \
  --admin-username opsadmin --tenant-expiry <YYYY-MM-DD> \
  --admin-password-file /root/leanschool-private/admin-password.private
```

此步骤要求目标schema不存在或完全为空，应用数据库用户名从未存在；先创建新schema和仅此schema的SELECT/INSERT/UPDATE/DELETE账户，再依次导入001～006以及独立bootstrap。bootstrap拒绝已有账号/租户/角色/OAuth客户端的身份表，事务创建 tenant1、管理员、super_admin和新OAuth客户端。没有演示账号、商户、短信或邮件通道。

工具验证645表、2952学校、1管理员、0支付/短信/邮件通道后写 `schema-ready.json`。再次对相同schema清单执行会核对表名并返回 alreadyInitialized，不重复写入。**MySQL DDL不能整体回滚**；若首次导入失败，部分新库、专用用户、私有bootstrap会保留，工具拒绝对非空目标重跑。应保留失败证据，由操作者明确选择修复新库或恢复本次安装前快照；工具不自动DROP、不覆盖账号口令。

管理员密码确认保存至密码管理器后，可在该独立路径删除一次性口令文件；保留受控加密备份所需的生产密钥。官网既有SQLite和投票数据不参与此初始化。

## 4. 启动和切换

```bash
sudo python3 /data/leanschool-release/deploy/platform-install.py activate
```

它切换Java current，启动专用Redis/Java，最多等待300秒健康结果UP；Java成功后才切换UI current。失败会恢复先前Java/UI链接并重启旧Java，首次安装则停止新Java。不会回退数据库。健康通过后服务开机自启；结果会明确业务验收仍待完成。

随后由官网发布流程将 Nginx `/console/` 映射到 `/opt/leanschool/ui/current/console/`，`/admin-api` 转发到Java，公众学校/API经已有白名单网关处理，旧域保留。执行 `nginx -t` 后再reload，公众不能直接访问48080/48081或内部健康接口。

检查本机健康与服务状态，并完成真实管理员登录/错误密码、权限、2952校查询、CMS草稿发布/回退、评论隐藏/恢复、官网旧域投票保留和新旧域Origin边界验收。业务后台所需真实组织、流程审批人、文件存储等通过原管理端配置，不导入演示客户/账套。外部支付、短信、AI等按正式渠道单独验收。

桥接密钥以 `/etc/leanschool/bridge-secrets.env` 为共享来源，部署程序直接读取并写入官网的root私有配置，禁止复制粘贴到聊天或命令行。官网程序就绪、双方签名一致并测试后，再启用Java的两项桥接开关。初次CMS是未发布草稿；发布操作仍走原后台，不伪造历史。

## 5. 后续升级与回退

新提交重复 `plan` → `prepare` → `activate`。schema摘要变更会拒绝直接启动，需独立审查增量迁移、停写策略和成对备份；不能用初始化工具代替升级工具。

```bash
sudo python3 /data/leanschool-release/deploy/platform-install.py rollback
```

回退只恢复上次记录的Java/UI current并重启Java。首次部署没有完整旧版本时会拒绝；不会删除/恢复业务库或配置。回退后再次核对健康与业务。存在不兼容数据迁移时，应按外层发布方案恢复成对MySQL/SQLite/素材快照；单独回退代码不构成数据恢复。

错误默认不输出子进程原文，以免泄露SQL或凭据。诊断时在受控服务器环境检查服务日志，先审查和脱敏再交接，不把完整环境文件或初始化SQL贴入聊天。
