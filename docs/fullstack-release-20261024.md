# 薄肌学院整套发布记录

2026-10-24。用户已授权将当前整合代码推到自己的 Git，并在 152.136.108.87 部署完整系统、配置新证书，同时保留旧域名。

## 当前状态

服务器上的 Java、官网、公众网关、`/console/` 和新域 HTTPS 配置已安装，内部验收通过。服务器自身通过公网 DNS 请求新旧 HTTPS 均为200；但本机热点请求 `https://www.leanschool.net` 仍在 TLS 连接阶段被重置（urllib10054 / curl35），Web 工具也未打开新域。因此只能确认服务器配置完成，不能称新域已在外部网络全面可用。热点请求新域 HTTP 为301，www DNS 指向152.136.108.87；连接重置原因尚未确定，不能仅据此归因为备案、证书或某一网络环节。

当前可先使用[旧域官网](https://boji.tensorbot.xyz/)与[旧域原芋道后台](https://boji.tensorbot.xyz/console/)，本机热点已验证两者严格 HTTPS 返回200且品牌匹配。首次交互登录及完整业务流程仍待验收。管理员账户已通过本机私密文件交付，本文不记录凭据。用户此前确认新域网站 ICP 备案尚未办理，需完成备案并向云平台核查新域公网接入。

## 源码与入口

三份源码保持各自历史和目录，不把两个上游工程混进官网目录。现有私有仓库 `papaoo/bojizhuye` 已通过 GitHub API 确认为 private，当前账号可推送。

| 部分 | Git 分支 | 本轮版本 | 目标入口 |
| --- | --- | --- | --- |
| 官网与 Python | main | 1a4ba54b47018e3ed78b013a66f8ac4840b59ca9 | `/`、`/city-vote`、`/schools`、`/events` |
| Java 全模块后端 | platform/backend | d5ff0bb3af7683fb4fd1565aed1c58956aca3e6c | `/admin-api`，公众 API 经独立网关 |
| 原芋道 Vue 管理端 | platform/admin-ui | 8554101037814965582590b88867e1883352cf0d | `/console/` |

Java 基于 YunaiV/ruoyi-vue-pro 的 `e4977f6ccaff29ded7024462d4778e4ccc025a61`；Vue 上游为 yudaocode/yudao-ui-admin-vue3。保留上游许可与版权。Java 首次推送因浅克隆缺少历史对象被拒，补齐原分支历史后成功；没有合并上游的新版本或改写已有线上分支。

正式域名为 `https://www.leanschool.net`。一个完整域名足够，Nginx 按路径分流。Java、Python、公众网关、MySQL、Redis 均使用本机回环，数据库端口不需要开放到公网。证书已验证同时覆盖 www 与根域；根域 `leanschool.net` 尚未启用，还需单独确认 A 记录和服务器跳转配置。

## 已完成的本机准备

- 官网学校选择接入统一目录，公开评论/弹幕和精简留言界面归档；首页原滚动区域按用户指示保留。
- 两个域名各有独立的 Host/Origin 验证实例，共用原 SQLite 和唯一通知 worker。旧浏览器在旧域可继续改票，跨域 Cookie 不会自动迁移，也不通过 IP 认领旧身份。
- `/app-api` 使用 5382 的严格方法/路径/参数白名单、固定站点租户和会员 Bearer，不能直接整段代理上游公众接口。两条签名 `/internal/leanclub/*` 不对公网开放。
- Vue 正式构建只读取 `.env.leanschool`，不继承本机 `.env.local`；登录、品牌图、刷新和扫码回跳支持 `/console/`。
- 新证书安装工具仅读取服务器 ZIP，验证域名、完整链、有效期和密钥；新增新域站点，不改旧证书。加载失败恢复配置，业务库不随配置回退覆盖。
- 网站升级工具对原库停写备份，执行明确的订单/支付结构迁移后再启动；保留旧域 `/admin` 和原证书，旧站学校接口同时接入网关。
- Java 保留当前全部22个业务模块，使用 Java25。本机历史验证 JAR 为611179571字节，SHA256 `6800780ea3a1585a676114598bd9c034754d3c5ef267d5f9c3d1bc267cb4fd53`；实际服务器使用下述 Linux 云端构建产物，两者不可混用摘要。
- 私有干净初始化包来源于核对过的原始 SQL、JAR 引擎脚本和学校/品牌源文件，不是本地运行库导出。包含645表、学校字典和菜单字典；不携带旧账号、token、商户或示例业务。CMS 作为草稿，待真实发布验收。
- 网站备案信息按主机显示；旧域保留原号，新域不沿用旧号或宣称备案完成。

## 本机验证历史

Python 最终全回归302项：301通过、1项既有 Windows 符号链接权限跳过，包含17项证书工具测试；最后的停服端口探针调整后，21项升级专项再次通过。官网客户端12项通过；Java75项权限/租户/内容/签名/支付边界通过，43模块完整构建成功。Vue类型检查、改动文件ESLint、正式构建与产物泄漏检查通过。证书测试包含临时OpenSSL证书的真实验证。

初始化种子在独立 MySQL 8.3.0 空库实测导入成功：645表（含45张Flowable表）、2952学校、16项薄肌权限，其他业务表初始为空；合成管理员初始化与重复拒绝均通过，测试账号随后禁用。实际导入发现并修复了Quartz外键建表顺序、空字符串SQL生成两处阻塞。最终种子 manifest SHA256 为 `7694138803194202000e66a8833826045afb4a6844f4e4300fe2e27c4a168c02`。安装工具17项隔离检查通过。测试使用新QA库，未读写原业务表。

完整JAR随后使用正式 `prod` 配置在本机隔离启动，仅覆盖本机路径、专用端口/合成凭据/QA库；MySQL账号只有SELECT/INSERT/UPDATE/DELETE权限，启动后645表集合未变。88.8秒启动、健康UP、2952校和CMS读取通过；发现并修复了依赖MySQL命名时区表的配置，改用固定+08:00，不改数据库全局设置。OpenAPI、Swagger、Druid和Actuator环境接口均无资源，匿名管理接口拒绝访问。上游 `/doc.html` 静态壳仍可在回环端口读取，不含OpenAPI数据；Nginx不把此路径代理Java，新域证书工具还要求它在公网入口返回404。

隔离启动使用Windows/Redis5.0.14.1，验证应用装配和连接行为，不替代目标Linux、Redis6.2+ ACL或systemd权限验收。测试新建进程和临时DB账号已清理，原开发服务PID未变；未发通知、短信或支付。

上述本机结果保留为历史证据，不替代后续服务器验收。早前 SSH 在认证前重置，用户调整网络后恢复；实际部署结果如下。

## 服务器执行与验收

- 目标机为 Ubuntu24.04.4、4核、3723MiB RAM；运行时为 Temurin25.0.5+8、MySQL8.0.46、Redis7.0.15。验收时可用内存1173MiB、可用磁盘54GiB，是该时点读数，不是峰值或容量保证。
- 服务器直接下载并验证云包，独立初始化645张表、2952所学校、1个管理员、0个支付渠道。DML-only应用账号、Redis ACL/AOF、BCrypt及学校API检查通过；未复制本机业务运行库。
- Java activate 后健康UP，Nginx、Python、公众网关、Java、Redis、MySQL均 active + enabled；Java `NRestarts=0`。平台 core 与 `--host` 验收均PASS，不等同所有22个模块业务验收。
- 官网固定版本1a4ba54已升级，原 SQLite 停写备份与迁移完成。备份：`/var/backups/bojizhuye-fullstack/20261024-095832-06e6f4fb541d40dd8fb2bbc271bcf6324`。双Host健康和旧HTTPS通过，签名桥接两项开关已开启。
- 租户1的 `websites` 初始化值原为JSON，实际类型处理器和查询使用逗号分隔串；已精确改为 `www.leanschool.net,boji.tensorbot.xyz`，两份bootstrap与对应测试同步修正，各20项通过。最终只读GET `/admin-api/system/tenant/get-by-website?website=www.leanschool.net` 与 `website=boji.tensorbot.xyz` 均返回 `code=0/data.id=1`；两项Java桥接开关均为true，六项服务仍active。
- 新证书 `/data/28042900_www.leanschool.net_nginx.zip` 已验证完整链、密钥匹配、www/根域SAN，到期2027-01-20；接收ZIP权限0600。旧域配置及证书保留。
- 首次SSL启用在reload后第一轮curl60失败并自动回滚。补充最长20秒、每次严格TLS验证的就绪重试后，候选脚本实际安装成功；本机20项专项测试覆盖首次60后成功、持续失败回滚和请求超时。安装备份：`/var/backups/leanschool-https/20261024T020954Z-2969896`。
- 服务器回环 `curl --resolve` 严格TLS验证全部指定路由、内部接口404和跳转目标通过；服务器自身走公网DNS的新旧HTTPS均200。外部热点新域仍重置的边界见顶部，未使用 `-k` 掩盖证书错误。

## 未完成项

新域公网接入与ICP备案、首次交互登录、学校选择/评论审核/内容发布回退/扫码和真机完整流程仍需继续验收。短信、支付商户与渠道未配置；不以固定验证码、假支付或手工标记到账替代。OA流程、员工权限及财务资料需按真实运营配置。

通用文件存储保持未配置：当前上游匿名下载缺少租户隔离，且同名文件可覆盖；头像和通用附件不能标为已可用，需先确定隔离与命名方案。全模块代码运行不代表外部渠道、文件服务和每项业务均已交付可用。

## 固定发布包

当前实际部署来自 [GitHub Actions run40049018447](https://github.com/papaoo/bojizhuye/actions/runs/40049018447)，backend、admin-ui、package三个job全部成功。固定源码见上表，构建工具提交为 `8384d2479a03c9f1be80eb5287a520ff85c238a9`，下载工具提交为 `b92facd157714e31e18363171c7bb60dde2f92b8`，工具已推送两个既有官网仓库。第一轮因干净构建缺少自动生成类型声明失败，已改为正式构建生成声明后完整类型检查，没有跳过类型检查。

Linux云包使用 Ubuntu24.04、Temurin25.0.5+8、Maven3.9.7、Node24.20.0、pnpm10.30.0。Artifact `leanschool-release-40049018447-1`（id12542996095）外层ZIP为624793712字节，SHA256 `aec6b85a7d772c5e25637d05881ce67332d840897e432abd375beb8c1dbbf593`，服务端已复核；其中 JAR 为611177447字节，SHA256 `97f2f9c2208133661c3c8290b4d3e6cfd65e83d9ec2811a35e2f443aeb6578de`。完整版本/逐文件摘要见本工作区[云包清单](../../../06-delivery/releases/leanschool-20261024/cloud-release-manifest.json)。

云包只有 `website.tar.gz`、`console.tar.gz`、`yudao-server.jar`、`java-release.json`、`release-manifest.json`、`SHA256SUMS` 六个文件，不含私有初始化SQL、证书或凭据；私有初始化资料单独验证并传入服务器root目录。GitHub artifact只保留3天，不能作为长期备份。清单内 `state=cloud-built-not-deployed` 是构建时状态，保留不变；实际部署状态以本记录和服务器验收为准。

此前 `deploy/build-release.py` 生成的本机Windows包包含 `private-init.tar.gz`，保留在交付目录 `bundle/` 作为历史验证资料；它不是本轮实际运行的Linux云包，不能混用JAR和摘要。所有后台归档均保留 `console/` 顶层，私有初始化包不含测试管理员或运行库，不能公开分发。

恢复与后续部署按[平台执行说明](../deploy/yudao/PLATFORM-RUNBOOK.md)、[官网升级说明](fullstack-site-upgrade.md)执行。先核对摘要和备份，配置回滚不自动覆盖业务数据库。
