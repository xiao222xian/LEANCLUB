# leanschool.net 接入进度与切换清单

检查日期：2026-10-24（Asia/Shanghai）。目标 `https://www.leanschool.net`，沿用 `152.136.108.87`。当前已完成服务器配置，外部新域HTTPS仍待核查。

## 最新执行范围

用户已授权的整套部署完成：Java、双域官网、公众网关、`/console/`与新域证书已安装，全部相关服务active+enabled。服务器回环 `--resolve` 严格TLS路由、内部404、跳转和平台验收通过；服务器自身走公网DNS的新旧HTTPS均200。新证书SAN覆盖www与根域，到期2027-01-20；旧域及旧证书保留。详细固定版本、备份和限制见[全栈发布记录](fullstack-release-20261024.md)。

双域租户映射最终GET已复验：`/admin-api/system/tenant/get-by-website` 分别传入 `www.leanschool.net` 和 `boji.tensorbot.xyz`，均返回 `code=0/data.id=1`。两项Java桥接开关均为true，六项服务仍active。

本机热点访问旧域官网及`/console/`严格HTTPS均200且品牌匹配；新www HTTPS仍连接重置（urllib10054/curl35），HTTP301正常，DNS为152.136.108.87，Web工具也未打开新域。**服务器配置完成不代表外部新域已全面可用**；重置原因未定位，不将其直接归因为备案或某个单一网络环节。首次交互登录仍待验收。

## 现在需要用户处理

1. 用户此前确认只完成域名实名认证，新域网站ICP备案尚未办理；继续在实际接入云平台办理，并向云平台核查新域公网接入/拦截状态。保留现有 `www / A / 152.136.108.87`，无需重复上传或购买证书。
2. 用另一条外部网络测试 `https://www.leanschool.net`，记录时间与具体错误，供云平台和后续网络排查对照。当前可先用[旧域官网](https://boji.tensorbot.xyz/)和[旧域后台](https://boji.tensorbot.xyz/console/)。账户已通过本机私密文件交付，不在聊天粘贴密码或私钥。
3. 如需不带www的入口，再核对根域A记录并安排服务器HTTPS跳转；证书已覆盖根域，但本次尚未启用该入口。后续仍需完成首次登录、内容/评论审核和手机流程验收，短信/支付渠道及通用文件存储未配置。

## 接入前检查历史（以下结果已由顶部状态更新）

| 项目 | 本轮结果 |
| --- | --- |
| 注册局 | RDAP 为 active，client hold 已解除；NS 为 DNS25/DNS26.HICHINA.COM |
| www 解析 | 8.8.8.8 与 dns25 权威查询均为 152.136.108.87 |
| 根域解析 | leanschool.net 尚无 A 记录 |
| 新域 HTTP | 00:05 请求返回 Nginx 404 |
| 新域 HTTPS | 严格 TLS 验证失败，证书名称不匹配；未绕过验证 |
| 旧域 | https://boji.tensorbot.xyz 返回 200 |
| 新域网站备案 | 用户已确认尚未办理；本次通过的是域名实名认证 |
| 新证书 | 已在腾讯云文件界面核实 `/data/28042900_www.leanschool.net_nginx.zip`；SAN、完整链和私钥尚待服务器验证 |

注册局最新变更时间为 `2026-10-23T15:56:31Z`。用户最新 DNS 截图显示绿色正确提示，与实查一致。无需再处理上一轮的 NS 警告。

## 原用户控制台清单（历史）

1. 阿里云解析列表保留现有 `www / A / 152.136.108.87`，再添加 `@ / A / 152.136.108.87`，默认线路、TTL 10 分钟即可。`@` 支持不带 www 的地址，不代替服务器 HTTPS 跳转。
2. 用户已确认本次仅域名实名认证通过，新域网站 ICP 备案尚未办理。当前服务器在腾讯云中国内地，新域备案按实际接入和主体在腾讯云办理；旧域备案号不能直接视为新域备案。腾讯云当前文档说明已备案网站不支持直接新增/替换成新域名，应按对应新增服务流程办理。[腾讯云说明](https://cloud.tencent.com/document/product/243/37406)
3. 用户已有新 SSL 证书：下载 Nginx 格式证书包并放到服务器 `/data/` 下，再提供完整路径即可，不在聊天中粘贴私钥。接收后核实 SAN 是否覆盖 `www.leanschool.net` 和 `leanschool.net`、链完整性、有效期及密钥匹配；文件在 /data/ 只作接收，不直接用作永久私钥存放。实际部署到 root 私有证书目录并设置权限、记录续期方式。用户无需重新购买证书。

## 原代码与服务器工作清单（历史）

由开发侧处理，用户不需要手改源码：

1. 先读取生产当前版本和 Nginx 配置，备份配置、环境文件与数据库。生产仍为 4499b01 加 HTTPS/备案静态补丁，不能用整个本地 v5 工作树替换。
2. 新增新域 HTTPS 站点和 HTTP 跳转，保留旧站和旧证书。验证新证书链、域名、有效期及私钥匹配后再加载；根域即使只跳 www，也需要根域证书。
3. 校正后端公共地址和同源写入验证。现有后端只接受单个 `CLUB_ORIGIN`，仅改 Nginx 会导致投票等写入失败；也不能通过代理伪造 Origin 来绕过验证。
4. 保留旧匿名投票入口及相应 API，设计并验证双域过渡后才切换；不同根域的访客 Cookie 不互通，不能因改名清票，也不能用 IP 自动认领身份。普通旧页面的永久跳转放在新站验收之后。
5. 分别验证首页、投票、管理登录、改投、意见提交、公开链接、重定向及手机访问；用无副作用请求检查同源拒绝边界。保留可恢复配置，失败时成对恢复环境与站点配置，业务库不随配置回退被覆盖。

本地已有的学校目录、评论、会员、CMS、活动/支付模块需要各自的正式发布准备；用户后续已授权本轮整套部署，但仍须完成服务器资源、数据库与业务验证。

### 历史过渡方案（已由整套部署授权替代）

只读审查 `4499b018296c26cee6e0b2d728241ba2149835d0` 后，建议先统一当前活动的投票入口：新域先承载经过限定处理的品牌展示，投票等交互临时 302 回旧域完成，旧域保留原 Origin 与 Cookie。直接复用旧主页还需处理 `site-analytics.js` 的统计 POST，不能留下悄悄失败的写请求。这是待实施的过渡方案，不是本轮已经发布的行为。

待新域也承载交互时，最小补丁需要精确的 Host → 单 Origin 映射：两个 `CampaignAPI` 共享同一 `CampaignStore`，每个请求选择局部实例，拒绝未知/重复 Host；旧 Host 只接受旧 Origin，新 Host 只接受新 Origin，保留 Referer、Fetch Metadata 与管理员 CSRF 校验。不能按请求修改全局实例，不能简单放开两个 Origin，也不能多开通知 worker。

验证应包含新旧 Host/Origin 交叉矩阵、缺失 Origin 的 Referer 回退、重复 Host、管理员 CSRF 和临时数据库的旧 Cookie 改投。双域同库仍不等于匿名身份跨域迁移，旧用户同时换域和换网络会被当作新访客；当前活动使用一个投票入口可避免额外引入这种跨域重复。

## 当前可用入口

- 线上：[旧域官网](https://boji.tensorbot.xyz/)及[原芋道后台](https://boji.tensorbot.xyz/console/)。新域站点与HTTPS已安装，外部热点访问仍重置，待继续核查。
- 本机：[新版投票与留言](http://127.0.0.1:4180/city-vote#conversation)。本地改版不自动出现在公网。

来源复核于 2026-10-24：[Verisign RDAP](https://rdap.verisign.com/net/v1/domain/leanschool.net)、[阿里云解析记录](https://help.aliyun.com/zh/dns/pubz-add-parsing-record/)、[腾讯云变更备案](https://cloud.tencent.com/document/product/243/37406)、[Let's Encrypt 验证方式](https://letsencrypt.org/docs/challenge-types/)。完整品牌及账号事项见[迁移方案](../../../02-brand/LEAN-SCHOOL-MIGRATION.md)。
