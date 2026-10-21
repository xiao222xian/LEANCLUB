# LEANCLUB

薄肌全球高校联盟网站：学校榜单、俱乐部资料和手机端信息提交。

线上网站：[leanmuscleclub.com](https://leanmuscleclub.com/)

## 功能

- 响应式首页、学校搜索，以及30所国外学校的校徽滚动展示。
- 学校榜单、国家/地区筛选、中文/英文/简称搜索。
- 俱乐部详情：负责人联系方式、群人数、抖音二维码。
- 手机填写入口：学校、负责人联系方式、群人数、抖音二维码。
- Python标准库API与SQLite存储；图片压缩、加载状态与页面切换效果。

## 本地运行

需要Python 3.9+，无需安装第三方Python依赖。在仓库根目录打开两个终端。

终端一，启动API（仅监听本机）：

```bash
CLUB_DB="$PWD/data/clubs.sqlite3" \
CLUB_ORIGIN="http://127.0.0.1:4180" \
python3 backend/server.py
```

终端二，启动前端预览及同源API代理：

```bash
python3 scripts/preview.py
```

打开：

- 首页：<http://127.0.0.1:4180/>
- 学校榜单：<http://127.0.0.1:4180/schools>
- 填报：<http://127.0.0.1:4180/join>

浏览器请使用上述127.0.0.1地址，以匹配表单提交的Origin设置。
预览代理默认连接本机5381端口；开发服务器仅供本地使用。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 目录

```text
web/creator/     当前网站HTML、CSS、JavaScript与图片
backend/        HTTP API、SQLite存储及学校元数据
scripts/        本地预览工具
tests/         单元测试
docs/          学校信息与图片来源记录
```

## 数据与部署

仓库包含49个原始学校/组织条目的基础目录，以及51条新增学校的英文名、地区和搜索别名。新增学校元数据只有在对应学校已有提交记录时才合并展示，不会自动创建用户提交。

**线上用户提交、联系方式、用户上传二维码、修改密钥、数据库、服务器凭据和运维手册均不包含在仓库中。** 本地首次运行会创建独立空数据库；本地提交不会影响线上网站。

群人数由俱乐部负责人提供，不代表独立会员人数或学校在校人数。当前表单允许同名学校重复提交覆盖资料，未上传二维码则保留已有图片；此版本没有负责人身份认证或审核流程。部署者需要按自己的使用场景决定是否增加认证与审核。

生产运行应使用HTTPS反向代理，把`/api/club/`转发至本机API端口，设置可信客户端IP、请求大小限制和限流；将`/schools`映射到`lean-school-ranking.html`、`/join`映射到`school-submit.html`。网页根目录只使用`web/creator`，数据库保存在网页根目录之外，并安排备份。`CLUB_ORIGIN`须设置为部署域名。

## 素材说明

学校标识、人物照片及其他第三方素材的权利属于各自权利人，仓库公开不代表授予这些素材额外使用权。学校标识用于识别俱乐部对应学校，不表示学校官方合作或背书。

校徽来源与署名见[图片来源](web/creator/images/campus-logo-sources.txt)、[校徽清单](docs/campus-marquee-assets.json)。学校英文名与地区核对见[来源记录](docs/school-metadata-sources.md)。
