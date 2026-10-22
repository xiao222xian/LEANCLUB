# bojizhuye

Lean Club 薄肌社群主页、三城线下活动投票与内容管理。当前开发仓库：[papaoo/bojizhuye](https://github.com/papaoo/bojizhuye)，基于 [xiao222xian/LEANCLUB](https://github.com/xiao222xian/LEANCLUB) 继续开发，保留上游历史与素材来源。

本项目试运行：[主页](http://boji.tensorbot.xyz/) · [三城投票](http://boji.tensorbot.xyz/city-vote)。当前生产版本为 `4499b018296c26cee6e0b2d728241ba2149835d0`，2026-10-22 22:44从首次部署的 `cb66b6a` 升级，仍使用 HTTP，SSL 由用户申请。升级前完成私密SQLite备份，原有账号、统计、海报与机器人配置保留；安装器健康及域名路由检查通过，独立公网检查确认投票与管理的六份HTML/CSS/JS与固定源码包字节一致。公网真实管理登录及飞书设置页面此前已通过，完整业务与真机验收仍待完成。部署状态、验证范围及恢复记录见[部署记录](docs/deployment-152-136-108-87.md)。下方 `leanmuscleclub.com` 为上游参考网站，并非本轮发布地址。

“有个想法”意见入口、后台意见箱及飞书意见提醒已随本轮版本部署，行为、接口与隐私范围见[意见箱说明](docs/feedback.md)。已提交一条明确标注的上线验收意见，实际通知投递正在核对；本地验证及生产核对范围见[意见箱验收记录](docs/feedback-qa-20261022.md)。

2026-10-23 新增付费活动报名开发版：投票页“线下见”入口、活动与票价管理、待支付订单、支付后电子票及现场核销。127项Python测试和前端状态回归通过，本地隔离浏览器完成合成出票与核销；真实收款渠道尚待选择，未配置时禁止下单，未付款不能取得有效入场码。设计和边界见[活动方案](docs/events-plan.md)、[接口契约](docs/events-api-contract.md)、[联调记录](docs/events-qa-20261023.md)。线上仍为上述意见箱版本。

<a href="https://leanmuscleclub.com/">
  <img src="docs/assets/home-desktop.png" width="100%" alt="Lean Club 真实首页：学校搜索、加入入口与高校标识滚动栏" />
</a>

<p align="center">
  <a href="https://leanmuscleclub.com/"><strong>上游参考网站 ↗</strong></a>
  &nbsp;&nbsp; · &nbsp;&nbsp;
  <a href="https://leanmuscleclub.com/schools"><strong>找到你的学校 ↗</strong></a>
</p>

## 学校不同，一起向上。

**Lean Club** 是连接不同高校薄肌同好的俱乐部网站。

搜索自己的学校，找到俱乐部，了解负责人联系方式。也让每个正在组织训练的人，有一个能被同校同好找到的入口。

## 从这里，找到同好。

| 想做什么 | 从哪里开始 |
| :--- | :--- |
| **找到本校俱乐部** | 在首页输入学校中文名、英文名或简称，进入对应俱乐部。 |
| **看看全球有哪些学校** | 浏览学校榜单，按国家／地区筛选，查看各校填写的群人数。 |
| **了解如何加入** | 打开俱乐部详情，查看已填写的负责人联系方式与抖音二维码。 |
| **决定第一次线下见面的城市** | 从首页进入城市投票，在深圳、珠海、广州中选择一城，可返回改选。 |
| **分享活动或训练想法** | 在城市投票页点击“有个想法”，匿名选择类型和城市后提交；团队可在后台意见箱查看。 |
| **管理活动内容** | 从首页的“管理登录”进入后台，查看投票与访问统计，替换海报并调整裁切位置。 |
| **在飞书群接收活动提醒** | 后台“飞书通知”保存机器人 Webhook，可选签名密钥；开启后自动提醒新增投票及意见，也可手动推送票数汇总。改投不通知。 |

> **榜单按各校俱乐部填写的群人数排序，群人数由各校负责人提供，不代表独立会员人数或学校在校人数。**

## 简单开始，持续完善。

前端使用 HTML、CSS 和 JavaScript，后端使用 Python、SQLite 与 Pillow 图片解码。原有学校搜索、地区筛选、俱乐部详情和加入入口继续保留；城市投票和基础内容管理以独立模块接入。

**城市投票、管理与意见箱当前以固定提交 `4499b01` 部署到 `boji.tensorbot.xyz`，没有发布到上述上游网站。** 首次 `cb66b6a` 部署及维护历史保留在部署记录中；HTTP 页面、健康、三张素材与 IP 跳转此前已核实，升级后的六份投票/管理文件已逐字节核对。线上admin密码不写入文档或源码；机器人配置已迁移开启，首条意见通知正在核对，投票交互与海报维护尚未完成生产验收。投票经弹窗确认，同一 IP 只记一票，原浏览器可改选；后台自动刷新统计。共用网络的设备可能共用一个公网 IP，票数不代表实名独立人数；无需填写联系方式即可参与。

<details>
<summary><strong>在本地运行 →</strong></summary>

需要 Python 3.10+，依赖固定为 Pillow 12.3.0 和 qrcode 8.2。在仓库根目录运行：

```bash
git clone https://github.com/papaoo/bojizhuye.git
cd bojizhuye
python -m pip install -r requirements.txt
python scripts/start-local.py
```

打开 [首页](http://127.0.0.1:4180/)、[城市投票](http://127.0.0.1:4180/city-vote) 或 [管理后台](http://127.0.0.1:4180/admin)。启动器同时启动本机网页与 API；Ctrl+C 停止本次创建的两个服务。

Windows 若需关闭启动终端后继续运行，可使用 `./scripts/start-local-background.ps1`；停止使用 `./scripts/stop-local-background.ps1`。详见[本地运行说明](docs/campaign-local.md)。

本地首次运行创建 `data/local.sqlite3`，海报替换文件保存在 `data/media/`，不会读取或修改线上用户资料。首次打开管理页，由你自行设置用户名和密码；没有默认管理员凭据。生产环境关闭网页初始化，使用 `python backend/manage.py create-admin --username admin` 交互设置密码。

**运行测试**

```bash
python -m unittest discover -s tests -v
```

环境变量、自定义端口、初始化和备份见[活动模块运行说明](docs/campaign-local.md)；机器人设置与发送边界见[飞书通知说明](docs/feishu-notifications.md)；目录结构与部署边界见[开发文档](docs/development.md)。

</details>

<br />

**一起完善这个入口。** 学校名称、地区或搜索匹配有误，欢迎[提交 Issue](https://github.com/papaoo/bojizhuye/issues)；功能改进与代码修复也欢迎通过 Pull Request 交流。请勿在公开 Issue 中填写私人联系方式、修改链接或密钥。

学校标识仅用于识别对应俱乐部，不代表学校官方合作或背书。第三方图片与标识的权利归各自权利人，详情见[素材来源](web/creator/images/campus-logo-sources.txt)及[学校信息来源](docs/school-metadata-sources.md)。

投票页三城站姿海报为生成概念影像，不是实际活动照片或已确定场地；[素材与提示词记录](docs/city-vote-assets/README.md)包含当前文件的 SHA-256 和字体许可。

---

<p align="center">
  <strong>Lean body. Strong mind.</strong><br />
  <sub>多运动。多思考。做有复利的事。</sub>
</p>
