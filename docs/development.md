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

**本次城市投票和内容管理仅完成本地开发与验证，尚未公开部署。** 完整配置、初始化、统计口径与备份步骤见[活动模块运行说明](campaign-local.md)。

## 测试

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

**线上用户提交、联系方式、用户上传二维码、修改密钥、数据库、服务器凭据和运维手册均不包含在仓库中。** 本地首次运行会创建独立空数据库 `data/local.sqlite3`，上传海报位于 `data/media/`；这些运行数据被 Git 忽略，本地提交不会影响线上网站。备份与恢复必须同时保留数据库和媒体目录，不能仅备份静态网页。

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
