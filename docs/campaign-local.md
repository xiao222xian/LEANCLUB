# 城市投票与内容管理 · 本地运行

从仓库根目录运行，需要 Python 3.10+、Pillow 12.3.0 和 qrcode 8.2：

加入海报管理之前的历史版本使用 Python 3.9+、无需第三方依赖；当前 README 已同步为 Python 3.10+，安装依赖以 `requirements.txt` 为准。

```bash
python -m pip install -r requirements.txt
python scripts/start-local.py
```

打开 <http://127.0.0.1:4180/>。主页保留学校搜索、学校榜单和加入入口，新增城市投票与管理登录。

- 投票：<http://127.0.0.1:4180/city-vote>
- 管理：<http://127.0.0.1:4180/admin>
- 活动开发版：<http://127.0.0.1:4180/events>；核销：<http://127.0.0.1:4180/check-in>。收款渠道未配置前不接受订单，详见[付费活动方案](events-plan.md)。
- 首次在管理页自行设置管理员账号与密码。账号可用 3–40 位字母或数字开头的用户名（可含 `.`、`_`、`-`），也可用最长 254 位 ASCII 邮箱；密码为 12–128 位。邮箱只作为登录标识，不发送验证邮件。没有预置账号或默认密码。

2026-10-22 本机已有的管理员已按用户指定重置，旧会话撤销；此次运行配置不改变新数据库的首次创建规则，也不在代码中内置默认凭据。

启动器仅监听 `127.0.0.1`，同时启动网页代理 4180 和 API 5381，设置一致的 `CLUB_ORIGIN`。按 Ctrl+C 停止本次创建的两个子进程；不会停止原有的其他服务。端口占用时可修改：

显式本地启动模式兼容同协议、同端口的 `localhost` 与 `127.0.0.1` 地址。生产环境仍仅接受配置的来源。缺少 Origin 时只接受核实同源的 Referer；显式跨源、`Origin: null` 或不可信 Fetch Metadata 不会回退放行。

```bash
python scripts/start-local.py --port 4181 --api-port 5382
python scripts/start-local.py --db ./data/review/local.sqlite3
```

默认数据库是 `data/local.sqlite3`，替换的海报在同目录下的 `media/`，不放在公开网页目录中。更换 `--db` 时媒体仍放在指定数据库目录下的 `media/`。这是一套本地数据，不会连接或修改线上数据库。

Windows 需要关闭启动终端后仍可访问时，使用隐藏后台进程启动：

```powershell
./scripts/start-local-background.ps1
./scripts/stop-local-background.ps1
```

可用 `-Python` 指定 Python 可执行文件，`-Port`、`-ApiPort`、`-Database` 指定端口和数据库。运行记录与日志位于忽略的 `data/runtime/`，不包含请求体或密码。停止脚本会核对保存的进程及所属路径，仅停止本次启动的服务；已有端口占用时不会接管其他进程。

## 飞书群投票通知

管理员登录后打开“飞书通知”，保存自定义机器人 Webhook、可选签名密钥，再开启新增投票通知。只有新增有效票自动发送三城票数卡片，改投、重复提交和取消确认不推送。可用“立即推送汇总”发送当前票数，或“发送测试消息”检查连接；自动通知关闭时仍可手动发送。发送走持久队列，失败自动重试，不阻塞投票。

详细设置、关键词、重试与配置变更行为见[飞书通知说明](feishu-notifications.md)。本机已按用户授权配置机器人。Webhook 和签名密钥存于私有运行数据库，备份同样需要限制访问。定时汇总为后续方向，当前没有定时发送任务。

## 数据和备份

向代码仓库同步数据库时使用[加密备份工具](database-backup.md)，本次用户指定的本地运行库备份与恢复校验见[备份索引](../backups/README.md)。密钥和解密后的数据库仍留在被Git忽略的私有目录，不覆盖运行库、不自动启动通知。该工具为独立可选依赖，需Python3.11+，不会改变网站Python3.10+的运行要求。

访问数以首页文档首次变为可见时提交的事件计算；重新加载页面是新的访问。后台访问不会计入首页。投票必须经弹窗确认，按服务端识别的 IP 去重，同一 IP 仅一票，原匿名浏览器可改选；清理 Cookie 或更换浏览器不会新增票，但原投票无法再由新浏览器改选。共享 Wi-Fi/NAT 的设备可能共用同一个公网 IP，票数不可解释为实名独立人数。

IP 经过规范化和数据库专用随机密钥 HMAC 后保存，不存原始 IP。该密钥随数据库备份，迁移时不能重新生成。升级保留旧票；以前从未记录来源 IP 的历史票不能凭空追溯，只能在原浏览器下次提交时补绑定。

后台概览可见时每 10 秒自动刷新，页面重新可见、获取焦点或收到同源投票成功通知时也会刷新。统计直接读取已保存的票，取消弹窗不计票，网络失败不显示成功。

备份时先停止两个本地服务，再复制数据库及同目录下的 `media/`；若存在同名 `-wal`、`-shm` 文件一并保留。恢复时将这组文件放回同一目录，再以对应 `--db` 路径启动。不要只备份网页代码而漏掉上传海报。

## 服务配置

| 环境变量 | 作用 |
| --- | --- |
| `CLUB_DB` | SQLite 数据库路径 |
| `CLUB_MEDIA_DIR` | 后台上传海报目录 |
| `CLUB_PORT` | API 本机监听端口，默认 5381 |
| `CLUB_ORIGIN` | 前端的准确来源，含协议及实际使用端口，不含路径或末尾斜线，如 `http://127.0.0.1:4180` |
| `PREVIEW_PORT` | 本地网页代理端口，默认 4180 |
| `CLUB_ALLOW_LOCAL_SETUP` | 本地首次管理员初始化开关，启动器显式设置为 `1` |
| `CLUB_TRUSTED_PROXY_IPS` | 生产环境可信代理 IP 列表；仅对列表内代理信任 `X-Real-IP`，默认不信任 |

生产环境不使用本地启动器，不启用 `CLUB_ALLOW_LOCAL_SETUP`。用 HTTPS 同源反向代理将 `/api/` 全部转发到本机 API，保留每一条 `Set-Cookie`、Cookie、Origin、Referer、Sec-Fetch-Site、CSRF 请求头及图片响应类型。不要由代理替客户端补写 Origin。Nginx 需用 `X-Real-IP=$remote_addr` 覆盖客户端同名头，并将真实代理地址配置到 `CLUB_TRUSTED_PROXY_IPS`，避免全站共用本机 IP 限流。网页路由需要将 `/city-vote`、`/city-vote/` 映射到 `city-vote/index.html`，`/admin`、`/admin/` 映射到 `admin/index.html`；保留已有 `/schools` 与 `/join`。网页根目录只指向 `web/creator/`。

Nginx 的 API 路由配置 `client_max_body_size 9m;`，否则默认 1 MiB 限制会提前拦截海报上传；后端仍严格限制单张原始图片不超过 8 MiB。`CLUB_ORIGIN` 必须与浏览器来源一致，访问地址与配置的端口也必须一致。本地网页初始化仅在显式开启、来源 hostname 为 `127.0.0.1`／`localhost`／`::1` 且数据库尚无管理员时可用，已有管理员不能再次通过该入口创建。

在生产主机使用与 API 相同的 `CLUB_DB` 和 `CLUB_MEDIA_DIR` 创建管理员或重置密码：

```bash
python backend/manage.py create-admin --username admin
python backend/manage.py reset-password --username admin
```

密码通过终端交互输入两次，不放在命令参数、源码或日志里。公网 HTTPS、反向代理、域名和实体手机扫码仍需部署时单独验证。
