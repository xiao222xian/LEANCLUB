# 开发与部署

## 本地运行

需要 Python 3.10+。当前唯一第三方 Python 依赖为 Pillow 12.3.0，用于服务端海报解码、校验及重新编码。在仓库根目录运行：

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

上述地址和同端口的 `localhost` 均可用于显式本地启动；生产仅使用配置的来源。启动器只绑定本机，网页默认 4180、API 默认 5381；用 `--port`、`--api-port`、`--db` 可更换端口或数据库。Ctrl+C 只停止本次启动的子进程，不停止端口上原有服务。

首次打开后台由用户设置用户名或邮箱及密码；没有默认用户名或密码。后台提供投票分布、首页／投票页访问统计，以及三城海报替换、焦点调整和恢复默认。现有高校搜索、排名、详情与加入流程保持原有功能，活动模块使用独立的 `campaign_*` 表。

**固定提交 `cb66b6a4d6417d3f047cb7ec636fa4f7aea2c2fb` 已在自有域名 [boji.tensorbot.xyz](http://boji.tensorbot.xyz/) 进行 HTTP 试运行。** 本机独立检查确认页面、健康、素材与 IP301正常；服务器采用独立新库，admin已初始化并按用户指定更新摘要，通用12位密码规则未变。21:44全库备份后清空9票及IP占用、保留47条访问事件；迁移本机机器人配置并开启，没有发送测试消息或迁移本机测试数据。独立session读取曾确认 `setupRequired=false`、`setupAvailable=false`，Edge公网真实登录与飞书设置页面已通过；维护后正常访问使事件总数增至48，统计没有重置。HTTPS、首条通知投递、投票/海报交互、恢复演练及真机尚未验收，不等于上游网站已更新。路径、安装与停用旧应用的恢复记录见[本次部署记录](deployment-152-136-108-87.md)；可审阅工具见[Ubuntu 部署说明](../deploy/ubuntu/README.md)。通用配置、初始化、统计口径与备份步骤见[活动模块运行说明](campaign-local.md)。

## 测试

本轮本地实现的“有个想法”入口、后台意见筛选及飞书纯文本提醒见[意见箱说明](feedback.md)，包含API、幂等、限流与数据范围；该功能尚未部署，真实意见通知投递仍待验收。

```bash
python -m unittest discover -s tests -v
```

## 目录

```text
web/creator/     当前网站HTML、CSS、JavaScript与图片
  city-vote/     三城全屏投票页及默认海报
  admin/         登录、投票/访问统计、海报内容管理
backend/        HTTP API、SQLite存储及学校元数据
scripts/        本地预览工具
tests/         单元测试
docs/           开发、学校信息与图片来源记录
  city-vote-assets/  三城海报完整生成记录、字体来源、SHA-256清单
```

## 数据与部署

仓库包含49个原始学校/组织条目的基础目录，以及51条新增学校的英文名、地区和搜索别名。新增学校元数据只有在对应学校已有提交记录时才合并展示，不会自动创建用户提交。

**线上用户提交、联系方式、用户上传二维码、修改密钥、数据库和服务器凭据均不包含在仓库中。** 仓库可保存不含凭据的部署说明与有限检查记录。本地首次运行会创建独立空数据库 `data/local.sqlite3`，上传海报位于 `data/media/`；这些运行数据被 Git 忽略，本地提交不会自动更新线上网站。备份与恢复必须同时保留数据库和媒体目录，不能仅备份静态网页。

群人数由俱乐部负责人提供，不代表独立会员人数或学校在校人数。当前表单允许同名学校重复提交覆盖资料，未上传二维码则保留已有图片；此版本没有负责人身份认证或审核流程。部署者需要按自己的使用场景决定是否增加认证与审核。

生产运行使用 HTTPS 反向代理，把整个 `/api/` 转发至本机 API 端口，保留 Cookie、Origin、CSRF 头、独立的每条 Set-Cookie 及实际响应类型。Nginx 设置 `client_max_body_size 9m;`，后端继续限制上传原图不超过 8 MiB。用可信代理覆盖客户端 IP，并配置 `CLUB_TRUSTED_PROXY_IPS`，不能信任访客直接传入的 `X-Real-IP`。

保留 `/schools` → `lean-school-ranking.html`、`/join` → `school-submit.html`；新增 `/city-vote` 及尾斜线路径 → `city-vote/index.html`，`/admin` 及尾斜线路径 → `admin/index.html`。网页根目录只使用 `web/creator/`，数据库和媒体保存在网页目录之外。`CLUB_ORIGIN` 设置为准确的前端来源，含协议与实际端口，不含路径或尾斜线。

生产环境不使用 `scripts/start-local.py`，不启用 `CLUB_ALLOW_LOCAL_SETUP`。设置与 API 相同的 `CLUB_DB`、`CLUB_MEDIA_DIR` 后，通过终端交互创建管理员：

```bash
python backend/manage.py create-admin --username admin
```

需重置密码时运行 `python backend/manage.py reset-password --username admin`。不把密码放在命令参数、源码或日志中。公网部署、HTTPS、反向代理、真实手机和微信扫码需部署时进一步验收，本地验证不等于这些环境已经验收。

## 素材说明

学校标识、人物照片及其他第三方素材的权利属于各自权利人，仓库公开不代表授予这些素材额外使用权。学校标识用于识别俱乐部对应学校，不表示学校官方合作或背书。

校徽来源与署名见[图片来源](../web/creator/images/campus-logo-sources.txt)、[校徽清单](campus-marquee-assets.json)。学校英文名与地区核对见[来源记录](school-metadata-sources.md)。

当前三城站姿海报及本地字体的来源、原始生成提示词、文件尺寸与 SHA-256 见[投票素材记录](city-vote-assets/README.md)。当前 `alan-v1` 按用户指定，以主站已有邵艾伦人物图和两张公开照片为参考合成；人物相似度、纹身与背景细节并非照片的精准复制，仍待视觉反馈。它们不代表本人在三城的现场实拍、实际活动或已确定场地；原 v3 虚构人物版本另作历史保留。
