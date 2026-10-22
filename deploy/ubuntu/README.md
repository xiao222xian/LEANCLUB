# Ubuntu 单机部署包

支持首次部署和按精确 Git 提交升级。`--revision` 必须是40位小写提交SHA；省略时仍使用旧版本 `cb66b6a4d6417d3f047cb7ec636fa4f7aea2c2fb`，不会自动选择最新提交。目标是 `http://boji.tensorbot.xyz`，可将 IP `152.136.108.87` 的80端口请求301跳转至域名并保留路径与查询。当前工具仅配置HTTP，TLS需另行配置。实际已部署版本和验收范围以[部署记录](../../docs/deployment-152-136-108-87.md)为准；工具更新不代表新版已经上线。

## 部署边界

- 新建域名虚拟主机及可选的独立 IP 重定向 server block，不设置 `default_server`，不修改已有站点配置。若其他配置明确占用请求的域名或 IP `server_name`，脚本停止，让操作者先判断路由归属。用户关于停止其他应用的要求由运维另行核对服务清单后执行，本部署工具不停止无关服务。
- 后端只监听 `127.0.0.1:5381`，前端由 Nginx 提供；新的 systemd 服务为 `bojizhuye.service`。
- 源码与 venv 位于 `/opt/bojizhuye/releases/<固定提交>/`，`current` 指向该版本。数据库和媒体独立放在 `/var/lib/bojizhuye/`，后续发布保留它们。
- 首次部署时创建空库，不复制本机测试记录、管理员、Cookie、Webhook、密钥或上传记录。升级使用既有运行库，保留账号、密码摘要、会话、投票、访问、海报、机器人配置与队列，不执行清空、账号重置或配置迁移。三城默认海报已在源码中；脚本不创建管理员、不设置密码、不启用公网初始化。
- 不改变 SSH、代理、公司网络规则、云防火墙或其他服务。需要操作者先确认域名 DNS 指向目标服务器、80 端口由当前 Nginx 提供；若改端口，用 `--http-port` 同时生成正确来源地址。`--host` 仅接受严格 ASCII FQDN 或规范 IPv4，不能包含协议、端口、路径、通配符、尾点或 Nginx 语法。`--redirect-ip` 仅接受 IPv4，并始终创建 80 端口的重定向。

## 准备与审阅

所需系统程序：Python 3.10+、`python3-venv`、Nginx、systemd。先检查现有软件，只有缺少时再由操作者安装；脚本不会 `apt upgrade` 或重装 Nginx。

```bash
python3 --version
python3 -c 'import venv, ensurepip'
sudo nginx -t
sudo systemctl is-active nginx
sudo ss -lntp
```

私有源码由有权限的开发机制作 `git archive`，服务器不需要GitHub PAT。先完成代码审阅、提交和必要检查，再选择确切提交；archive不包含工作区尚未提交的修改。上传走获授权的SSH/SFTP或云控制台通道，不通过公开下载链接分发私有源码。

首次部署的旧包在本机忽略目录 `data/deployment-152-136-108-87/bojizhuye-cb66b6a.tar.gz`，生成方式如下。部署工具晚于该旧提交创建，旧源码包不含本目录，需要单独上传脚本及三个模板。

```bash
git archive --format=tar.gz --output=bojizhuye-cb66b6a.tar.gz cb66b6a4d6417d3f047cb7ec636fa4f7aea2c2fb
sha256sum bojizhuye-cb66b6a.tar.gz
```

升级时，将下例占位值替换为已审核的新提交，不要复用旧包或旧工具覆盖新包：

```bash
RELEASE_COMMIT='替换为已审核的40位小写提交SHA'
git archive --format=tar.gz --output="bojizhuye-${RELEASE_COMMIT}.tar.gz" "$RELEASE_COMMIT"
sha256sum "bojizhuye-${RELEASE_COMMIT}.tar.gz"
```

部署工具同时核对包SHA-256与archive的Git提交标记；后者必须与 `--revision` 完全一致。工具还拒绝链接、目录逃逸及运行数据文件。确认服务器收到的摘要与开发机一致，并使用支持 `--revision` 和数据库备份的当前工具。

可先生成三份配置供审阅，此操作不改变任何服务，Windows 上也可执行：

```bash
python3 deploy/ubuntu/install.py --host boji.tensorbot.xyz --redirect-ip 152.136.108.87 \
  --render-only /tmp/bojizhuye-config-review
```

## 执行

以下路径为上传后的示例，替换为实际绝对路径及开发机已核实的 SHA-256；不要把账号密码写入命令。

```bash
sudo python3 /tmp/bojizhuye-deploy/install.py \
  --archive /tmp/bojizhuye-cb66b6a.tar.gz \
  --sha256 <开发机核实的64位SHA256> \
  --revision cb66b6a4d6417d3f047cb7ec636fa4f7aea2c2fb \
  --host boji.tensorbot.xyz --redirect-ip 152.136.108.87 --http-port 80
```

升级时同时替换 `--archive`、`--sha256`、`--revision` 三项，三者必须对应同一提交；保留本站已采用的 `--host`、重定向和HTTP端口参数。

脚本执行顺序：

1. 校验archive、环境、配置归属、端口和Nginx域名/IP冲突，在新release安装Python venv依赖，旧站仍继续运行。
2. 保存本项目原配置及 `current` 状态，生成新配置并通过 `nginx -t`。
3. 若本项目正在运行，停止服务和通知worker。存在运行库时，通过SQLite backup API保存私密 `before.sqlite3`；源连接使用 `mode=ro`，源/目标连接均显式关闭，之后才允许新代码打开数据库。
4. 切换 `current`，重载systemd并启动后端，验证健康后reload Nginx；以域名Host检查网页/API和公网初始化禁用，核实IP301的Location，最后启用开机启动。

备份目录为 `/var/backups/bojizhuye-deploy/<时间-进程>/`，目录权限700、备份文件600。首次没有旧库时不会产生 `before.sqlite3`；升级的备份包含停止服务时的业务库，而不只是配置。检查结果不是公网交互、真实消息投递或整机重启验收。

失败时自动恢复本项目的原配置、`current`及原服务启用状态，只在旧Nginx配置验证通过后reload；**不会自动恢复数据库，也不删除新release或业务数据**。本轮意见箱迁移仅新增 `campaign_feedback` 及索引，兼容原有表结构，既有账号、投票、访问、海报和机器人配置继续保留；新增意见队列的类型仍需对应代码支持。更换为不兼容schema的版本时不能只依赖代码回退。

同摘要的完整版本可重复执行。不完整release或同提交下摘要不同会停止并保留现场，须先检查，不能盲目覆盖。部署时短暂停服，服务恢复后既有通知worker会继续处理合规的待发任务；安装器本身不生成测试消息或业务记录。

## 管理员与机器人

域名或 IP 的 HTTP 可以完成页面和 API 试运行，来源严格绑定 `--host` 指定的协议、地址、端口。启用 IP 重定向时，唯一写入来源为域名，不放宽 Origin 校验以接受 IP。Cookie 在 HTTP 下不带 `Secure`，不存在错误启用 HTTPS Cookie 导致无法登录的问题；但 HTTP 不加密密码、会话和机器人配置。管理初始化应通过云控制台终端等受保护的运维通道；正式管理建议切换 HTTPS。

需要管理员时由操作者另行执行，输入 12–128 位密码两次，以下命令不会预置或显示密码：

```bash
sudo -u bojizhuye env \
  CLUB_DB=/var/lib/bojizhuye/clubs.sqlite3 \
  CLUB_MEDIA_DIR=/var/lib/bojizhuye/media \
  /opt/bojizhuye/current/.venv/bin/python \
  /opt/bojizhuye/current/backend/manage.py create-admin --username admin
```

本机已有密码不是新库默认密码。新库飞书默认关闭，按业务决定后另行配置。升级不需要重复创建admin，现有密码和机器人启用状态保持；安装器不会主动排队测试或汇总消息。

## 验证与备份

```bash
sudo systemctl status bojizhuye --no-pager
sudo journalctl -u bojizhuye -n 40 --no-pager
curl -f http://127.0.0.1:5381/api/club/health
curl -f -H 'Host: boji.tensorbot.xyz' http://127.0.0.1/api/admin/session
curl -I -H 'Host: 152.136.108.87' http://127.0.0.1/city-vote
```

未创建管理员的新库 `/api/admin/session` 应返回 `setupRequired: true`、`setupAvailable: false`；已有管理员的升级库应为 `setupRequired: false`，公网初始化仍关闭。由不同公网网络验证投票去重前，先确认Nginx覆盖 `X-Real-IP`、后端仅信任 `127.0.0.1`。不要用正式投票或意见写入当作无副作用的健康检查。

运行数据不在Git中。升级器的 `before.sqlite3` 是业务数据库快照，未复制媒体目录；常规完整备份应同时覆盖 `/var/lib/bojizhuye/media`。可停服后备份整个状态目录，在线SQLite备份则使用backup API，并与海报文件匹配。运行库及备份可能含意见正文、管理员摘要和机器人凭据，均须私密保存。

手动恢复时先停服务和worker，另备份当前数据，再从选定 `before.sqlite3` 恢复；显式关闭SQLite连接，核对数据库/WAL/SHM属主及完整性、外键和媒体对应关系。启动前检查通知配置及队列，避免恢复历史待发任务后重复通知。整库恢复会覆盖备份之后的新增数据和配置，不应作为自动失败处理。当前工具没有自动数据库降级或媒体恢复操作。

正式域名 HTTPS 需要新证书与对应 Nginx 配置，并同步修改 `CLUB_ORIGIN` 为唯一准确的 HTTPS 来源；不应通过放宽来源验证同时兼容多个写入入口。
