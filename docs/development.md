# 开发与部署

## 本地运行

需要 Python 3.10+。Python 依赖固定为 Pillow 12.3.0（海报解码、校验及重新编码）和 qrcode 8.2（本地生成票券二维码）。在仓库根目录运行：

```bash
python -m pip install -r requirements.txt
python scripts/start-local.py
```

打开：

- 首页：<http://127.0.0.1:4180/>
- 学校榜单：<http://127.0.0.1:4180/schools>
- 填报：<http://127.0.0.1:4180/join>
- 城市投票：<http://127.0.0.1:4180/city-vote>
- 管理后台：<http://127.0.0.1:4180/admin>
- 线下活动：<http://127.0.0.1:4180/events>
- 电子票：从订单进入 `/ticket#ticket=凭证`
- 工作人员核销：<http://127.0.0.1:4180/check-in>

上述地址和同端口的 `localhost` 均可用于显式本地启动；生产仅使用配置的来源。启动器只绑定本机，网页默认 4180、API 默认 5381；用 `--port`、`--api-port`、`--db` 可更换端口或数据库。Ctrl+C 只停止本次启动的子进程，不停止端口上原有服务。

首次打开后台由用户设置用户名或邮箱及密码；没有默认用户名或密码。后台提供投票分布、首页／投票页访问统计，以及三城海报替换、焦点调整和恢复默认。现有高校搜索、排名、详情与加入流程保持原有功能，活动模块使用独立的 `campaign_*` 表。

**当前固定提交 `4499b018296c26cee6e0b2d728241ba2149835d0` 已在自有域名 [boji.tensorbot.xyz](http://boji.tensorbot.xyz/) 进行 HTTP 试运行。** 2026-10-22 22:44从首次部署的 `cb66b6a4d6417d3f047cb7ec636fa4f7aea2c2fb` 升级，停服后通过SQLite backup API保存私密快照 `/var/backups/bojizhuye-deploy/20261022-224422-2480647/before.sqlite3`；保留既有账号、数据、海报与机器人配置。安装器的nginx-t、健康和域名路由检查通过；独立公网读取确认六份投票/管理HTML、CSS、JS与固定源码包字节一致，健康200。升级没有修改TLS，仍按用户安排等待证书。

首次部署和21:44维护是历史记录：原新库admin已初始化并按用户指定更新摘要，通用12位规则未变；维护前全库备份后清空9票及IP占用，保留47条访问事件，迁移机器人配置，维护当轮没有发送测试消息。Edge公网真实登录与飞书设置页已通过，后续正常访问使统计继续增长。意见箱升级后已提交一条明确标注的上线验收意见，通知投递正在核对；HTTPS、投票/海报交互、恢复演练和真机仍未完成验收，不等于上游网站已更新。路径、安装与恢复记录见[本次部署记录](deployment-152-136-108-87.md)；可审阅工具见[Ubuntu 部署说明](../deploy/ubuntu/README.md)。通用配置、初始化、统计口径与备份步骤见[活动模块运行说明](campaign-local.md)。

## 测试

付费活动模块是本地开发版，尚未部署或接通实际收款。无渠道配置时服务端拒绝下单，测试只使用隔离库合成收款，不提供公开测试付款接口。后台活动默认草稿；价格、订单状态、容量、退款申请和核销范围见[活动方案](events-plan.md)与[接口契约](events-api-contract.md)。

“有个想法”入口、后台意见筛选及飞书纯文本提醒已随 `4499b01` 部署，见[意见箱说明](feedback.md)中的API、幂等、限流与数据范围；隔离测试、生产文件校验与真实通知核对进度见[意见箱验收](feedback-qa-20261022.md)，不将本地模拟发送视为群投递成功。

```bash
python -m unittest discover -s tests -v
```

## 目录

```text
web/creator/     当前网站HTML、CSS、JavaScript与图片
  city-vote/     三城全屏投票页及默认海报
  admin/         登录、投票/访问统计、海报内容管理
  events/        活动详情、付费报名、我的订单
  ticket/        订单状态、支付后电子票及本地PNG保存
  check-in/      工作人员登录、票号/扫码预览与核销
backend/        HTTP API、SQLite存储及学校元数据
scripts/        本地预览工具
tests/         单元测试
docs/           开发、学校信息与图片来源记录
  city-vote-assets/  三城海报完整生成记录、字体来源、SHA-256清单
```

## 数据与部署

仓库包含49个原始学校/组织条目的基础目录，以及51条新增学校的英文名、地区和搜索别名。新增学校元数据只有在对应学校已有提交记录时才合并展示，不会自动创建用户提交。

**运行数据库明文、联系方式、用户上传二维码、修改密钥和服务器凭据不作为可读内容提交到仓库。** 仓库可保存不含凭据的部署说明与有限检查记录。按用户2026-10-23要求，本地运行库与上传海报另保存为[认证加密备份](../backups/README.md)，密钥仅留本机；线上库和QA演示库没有同步。本地首次运行会创建独立空数据库 `data/local.sqlite3`，上传海报位于 `data/media/`；这些明文运行文件被 Git 忽略，本地提交不会自动更新线上网站。备份与恢复必须同时保留数据库和媒体目录，不能仅备份静态网页。

群人数由俱乐部负责人提供，不代表独立会员人数或学校在校人数。当前表单允许同名学校重复提交覆盖资料，未上传二维码则保留已有图片；此版本没有负责人身份认证或审核流程。部署者需要按自己的使用场景决定是否增加认证与审核。

生产运行使用 HTTPS 反向代理，把整个 `/api/` 转发至本机 API 端口，保留 Cookie、Origin、CSRF 头、独立的每条 Set-Cookie 及实际响应类型。Nginx 设置 `client_max_body_size 9m;`，后端继续限制上传原图不超过 8 MiB。用可信代理覆盖客户端 IP，并配置 `CLUB_TRUSTED_PROXY_IPS`，不能信任访客直接传入的 `X-Real-IP`。

保留 `/schools` → `lean-school-ranking.html`、`/join` → `school-submit.html`；`/city-vote`、`/admin`、`/events`、`/ticket`、`/check-in` 及各自尾斜线路径映射到对应目录的 `index.html`。本地代理和部署Nginx模板已同步，活动路径尚未部署。网页根目录只使用 `web/creator/`，数据库和媒体保存在网页目录之外。`CLUB_ORIGIN` 设置为准确的前端来源，含协议与实际端口，不含路径或尾斜线。

生产环境不使用 `scripts/start-local.py`，不启用 `CLUB_ALLOW_LOCAL_SETUP`。设置与 API 相同的 `CLUB_DB`、`CLUB_MEDIA_DIR` 后，通过终端交互创建管理员：

```bash
python backend/manage.py create-admin --username admin
```

需重置密码时运行 `python backend/manage.py reset-password --username admin`。不把密码放在命令参数、源码或日志中。公网部署、HTTPS、反向代理、真实手机和微信扫码需部署时进一步验收，本地验证不等于这些环境已经验收。

## 素材说明

学校标识、人物照片及其他第三方素材的权利属于各自权利人，仓库公开不代表授予这些素材额外使用权。学校标识用于识别俱乐部对应学校，不表示学校官方合作或背书。

校徽来源与署名见[图片来源](../web/creator/images/campus-logo-sources.txt)、[校徽清单](campus-marquee-assets.json)。学校英文名与地区核对见[来源记录](school-metadata-sources.md)。

当前三城站姿海报及本地字体的来源、原始生成提示词、文件尺寸与 SHA-256 见[投票素材记录](city-vote-assets/README.md)。当前 `alan-v1` 按用户指定，以主站已有邵艾伦人物图和两张公开照片为参考合成；人物相似度、纹身与背景细节并非照片的精准复制，仍待视觉反馈。它们不代表本人在三城的现场实拍、实际活动或已确定场地；原 v3 虚构人物版本另作历史保留。
