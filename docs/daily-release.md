# 薄肌学院日常开发与发布

更新：2026-10-24（Asia/Shanghai）。本文说明日常发布流程；此前的[首次全栈部署记录](fullstack-release-20261024.md)保留为历史证据。**端到端发布及 no-op 验收完成：自动构建、完整 plan、正式 publish 和相同版本重复发布均实测成功。官网 `c5d3a4b`、Java `e278c87`、Vue `77b400e` 于 15:32:21（07:32:21 UTC）上线；当前工具为 `7bc4119`，98 项 Linux 测试通过。** 真实业务交互、新域公网及 ICP 备案、生产故障回退演练仍独立待办；早期下载失败与恢复过程保留在文末验收表中。

日常操作是：**本地开发 → 推送私有 Git → 自动检查和构建 → 选择成功构建 → 手动发布。** 推送不会直接更新生产；构建成功也不表示已经上线。

## 开发代码推到哪里

三个独立源码工程继续使用同一个私有仓库的不同分支，不合并工程目录：

| 组件 | 本地工程 | 私有仓库分支 |
| --- | --- | --- |
| 官网、Python 服务、发布工具 | `E:\work\natie\03-website\LEANCLUB` | `main` |
| Java 后端 | `E:\work\natie\05-lab\yudao\sources\ruoyi-vue-pro` | `platform/backend` |
| Vue 管理后台 | `E:\work\natie\05-lab\yudao\sources\yudao-ui-admin-vue3` | `platform/admin-ui` |

发布仓库为 [papaoo/bojizhuye](https://github.com/papaoo/bojizhuye)。官网公开镜像用于源码同步，不承担生产发布；Java/Vue 完整源码继续留在私有分支。不要将数据库、环境文件、证书、私钥或管理员账号加入提交。

本机两个芋道工程已增加 `production` remote，指向该私有仓库；原 `origin` 仍为上游开源仓库。完成本地提交后，按本次改动执行相应命令：

```powershell
# 官网
git -C E:/work/natie/03-website/LEANCLUB push origin main
# Java 后端
git -C E:/work/natie/05-lab/yudao/sources/ruoyi-vue-pro push production HEAD:platform/backend
# Vue 管理后台
git -C E:/work/natie/05-lab/yudao/sources/yudao-ui-admin-vue3 push production HEAD:platform/admin-ui
```

自动构建工作流在三个分支各有一份。修改 `.github/workflows/leanschool-release.yml` 时须同步三份；解析和打包脚本由工作流固定的 `main` 工具提交提供。提交信息包含 `[skip ci]` 会跳过 push 构建，仅用于已核验的文档或维护变更，不用于发布业务代码。

上述任一分支收到 push 后，`Build LEAN SCHOOL release` 会自动执行网站检查、Java 专项测试及完整打包、Vue 构建和类型检查，最终生成一个完整版本包。**目前云端仍构建全套；服务器更新时才按组件跳过未变化的源码。**

构建开始时一次性确定三个源码提交，写入不可变的版本清单。触发 push 的组件使用该次 push 的提交；其他组件使用当时解析到的约定分支版本。依次推送多个工程会产生多个构建，早期构建可能尚未包含后推送的组件，因此发布前必须核对构建摘要中的三个 SHA。

需要明确选择组合时，可在 `Build LEAN SCHOOL release` 点击 **Run workflow**，分支选 `main`。三个输入默认分别为 `main`、`platform/backend`、`platform/admin-ui`，也支持属于对应分支历史的完整提交 SHA；不接受任意仓库或分支。手动构建不会部署服务器。

## 发布一次更新

1. 打开 [Build LEAN SCHOOL release](https://github.com/papaoo/bojizhuye/actions/workflows/leanschool-release.yml)，选择状态为成功的构建，核对三个源码 SHA。
2. 复制构建页面地址中 `/actions/runs/` 后的数字，即 **run ID**。不要复制列表中的 `#123` 运行序号，也不要填 artifact ID。
3. 打开 [Publish LEAN SCHOOL production](https://github.com/papaoo/bojizhuye/actions/workflows/leanschool-deploy.yml)，点击 **Run workflow**，分支选 `main`；`build_run_id` 填上述 run ID，`mode` 保持默认 **plan**。
4. 查看 plan 的 Actions 摘要。`changed.website`、`changed.backend`、`changed.adminUi` 显示预计更新的组件；预检失败时先处理原因。plan 会下载并缓存验证包、读取生产状态，但不会切换应用版本、重启服务或修改业务数据。只有预检完整通过，才写入服务器私有的成功 plan 回执；仅下载成功不算预检通过。
5. 确认版本和预检结果后，再次运行 Publish，填同一个 run ID，把 `mode` 改为 **publish**。这一步才会执行生产更新。**成功 plan 是强制前置条件：publish 不下载缺失的安装包，也不接受其他 attempt、其他 artifact 或其他服务器工具版本的 plan 回执。** 完成后查看发布回执，并检查官网、管理后台及本次修改的业务功能。

命令行等价操作如下；先将 `<BUILD_RUN_ID>` 替换为真实构建 ID：

```sh
gh workflow run leanschool-deploy.yml --repo papaoo/bojizhuye --ref main \
  -f build_run_id=<BUILD_RUN_ID> -f mode=plan

gh workflow run leanschool-deploy.yml --repo papaoo/bojizhuye --ref main \
  -f build_run_id=<BUILD_RUN_ID> -f mode=publish
```

服务端只接受该构建当前已完成且成功的 attempt。若 plan 后重新运行了同一个 Build，attempt 和产物会变化，发布前应重新核对并执行 plan。最终 artifact 当前保留 **3 天**；过期后需重新构建，不能依靠服务器旧缓存绕过 GitHub 过期检查。

大文件从 GitHub 直接下载到服务器，本机不需要中转 JAR。plan 可复用 root 私有的完整 ZIP 缓存，但仍须实时核对 GitHub 构建与产物身份，并重新计算整个 ZIP 的长度和摘要；缓存不能绕过过期、来源或当前 attempt 检查。publish 必须复用成功 plan 的缓存，核对 run、attempt、artifact ID/摘要和当前可信工具目录，然后重新验证全部文件摘要及当前生产基线。服务器工具升级或构建 attempt 变化后，需要重新执行 plan。

## 服务器会做什么

发布入口验证私有仓库 ID、构建 workflow ID/路径、分支、事件、成功状态和当前 attempt；再验证 artifact 的来源、大小、ZIP 摘要、文件清单、各文件摘要及归档内容。通过后，由预先安装且 root 所有的固定部署器执行更新。

- 官网源码提交没变则跳过官网切换；Java 源码提交没变则跳过 Java 重启；Vue 在已有发布源码记录时按源码提交判断。首次缺少 Vue 源码记录时以已安装产物摘要判断，可能发生一次同源码的重建产物更新。
- 更新前保留配置与数据库备份，准备独立版本目录，再切换对应 `current` 链接。Java 更新需要短暂重启；这套流程不是零停机部署。
- 保留运行数据库、上传媒体、管理员账号、已有运行环境和桥接开关，不重新执行初始化种子或创建数据库。
- 保留 `boji.tensorbot.xyz` 与 `www.leanschool.net` 的现有访问配置和证书，校验双域证书/私钥文件摘要没有改变。
- 更新后检查服务状态、服务器本地的严格 HTTPS 和只读业务接口。失败时尝试恢复之前的代码与配置，并检查恢复状态。

**代码恢复不会自动回档业务数据。** 备份不是跨 MySQL/SQLite 的同一时刻事务快照。发现恢复失败、客户端断线或超时，应先核查服务器，不要连续点击 publish。

服务端 HTTPS 通过不等于所有外网均可访问。新域名此前存在外部连接重置及 ICP 备案待办，日常发布不会自动解决这些接入问题；参见[域名接入清单](domain-cutover-20261024.md)。

## 哪些变更需要单独升级

这条入口用于与现有生产数据库及运行环境兼容的应用代码更新。数据库结构/迁移、生产依赖、Java/Python 运行时、systemd、Nginx、端口、证书及身份凭据的调整，应走单独的运维升级和恢复验证。

目前程序会拒绝官网依赖清单或服务模板变化、检测到的网站结构/迁移代码变化、Java 持久化或数据库迁移资源变化，以及不一致的生产基线。结构保护是保守的预检，不代表对任意新业务逻辑的完整兼容性证明。普通 mapper 或数据对象调整也可能触发拒绝，需要人工判断后安排单独发布。

不要通过删除检查、执行 `init-db`、覆盖生产环境文件或把候选仓库里的安装脚本作为 root 运行来解决预检失败。发布工具本身也是独立的运维组件：Git 推送新工具文件不会自动替换服务器的固定工具目录。

## 维护者配置与记录

GitHub 使用 `production` environment；以下两项环境 secret 由维护者配置，本文只记录名称，不记录内容：

| 名称 | 用途 |
| --- | --- |
| `LEANSCHOOL_DEPLOY_SSH_KEY` | 仅用于发布的独立 SSH 私钥 |
| `LEANSCHOOL_DEPLOY_KNOWN_HOSTS` | 已核验的目标服务器 SSH 主机密钥 |

Publish 只在私有仓库 `main` 手动运行，使用 `contents: read` 与 `actions: read`。生产环境的分支限制和可用审批规则在 GitHub 环境设置中维护；不要把“存在 environment 名称”理解为已经启用了额外人工审批。

服务器使用 `leanschool-deploy` 专用身份、强制固定命令及受限 sudo 入口，禁止一般 shell、PTY、端口/代理/X11 转发。入口仅接收 `runId`、`mode` 和短效 token 的限定 JSON。token 通过加密 SSH 的标准输入传递，仅用于本次 GitHub 读取，不写入服务器文件、命令参数或发布回执。客户端验证固定 SSH 主机密钥，不在每次发布时动态信任新的主机密钥。

首次安装和可信工具升级使用 `deploy/ubuntu/install-release-gate.py`。维护者先把已审查提交的工具放到 root 所有、其他账号不可写的独立目录，再传入 `--tools-root` 和仅含公钥的 `--public-key-file`。首次可传 `--baseline-release-dir`，从已核验的旧包建立运行版本记录。工具会验证现有账号登记、所有权、无参数 sudo 规则及运行产物，不接收私钥，不修改现有 SSH 登录账号，也不重启生产服务。安装记录在 `/etc/leanschool/release-gate-install.json`；升级时不直接改写已有固定工具目录。

本次新增的 [Maintain LEAN SCHOOL release tools](https://github.com/papaoo/bojizhuye/actions/workflows/leanschool-maintenance.yml) 是无输入、手动触发的固定版本维护入口，维护代码提交为 `1f0547c`。它只安装已审查的 `5ac6d1b39b391d038e1813ca28a142c0eacd99bd` 工具及其固定摘要，不接收任意命令、版本或应用发布请求；与 Publish 共用生产并发锁。此次用于解决本机热点 SSH 仍在认证前重置的连接问题，已完成真实维护。临时 `LEANSCHOOL_MAINTENANCE_SSH_PASSWORD` 环境 secret 在任务成功后已删除，API 再查询返回 404；日常发布继续使用受限 `leanschool-deploy` 身份和上表两项 SSH secret，无需保存或使用 Ubuntu 密码。该维护入口后续不能直接用于其他工具版本。

请求输入截止 20 秒；plan 下载阶段截止 90 分钟且发生在生产修改之前；publish 只使用已验证的本地缓存，重新核对 GitHub 来源和生产预检。生产协调器负责各步骤的限时和失败恢复，不由 gate 在更新中途直接强杀。客户端最多等待 110 分钟，Publish job 上限 120 分钟。强制先完成 plan 再 publish，可避免把慢速下载与生产更新挤在同一次等待期限内。网络中断或外部取消仍需核查真实服务器状态。

服务器记录位置：

| 路径 | 内容 |
| --- | --- |
| `/etc/leanschool/release-gate.json` | 固定仓库/workflow身份和可信工具目录配置 |
| `/etc/leanschool/daily-release-host.json` | 本机发布身份标记 |
| `/etc/leanschool/daily-current.json` | 最近成功发布的源码、实际运行产物和验证记录 |
| `/var/lib/leanschool/releases/cloud/` | root 私有的代码缓存、`.verified.json` 产物身份回执和 `.planned.json` 成功预检回执；回执权限为 root 0600 |
| `/var/lib/leanschool/releases/zip-cache/` | 已完整校验的 ZIP，目录 root 0700、文件 root 0600；复用时重新核对 GitHub 产物身份、字节数和完整 SHA-256，不缓存可恢复的未完成分块 |
| `/var/backups/leanschool-daily/` | 每次更新的私有配置/数据库备份及结果记录 |

记录和备份可能含私有运行配置与业务数据，不能上传至 Git 或作为 Actions artifact。下载缓存与备份目前不自动清理；维护者应按磁盘占用和恢复需要安排保留策略。

## 常见结果与处理

Actions 只展示固定安全错误码和固定说明，不展示原始子进程错误、令牌或私密配置。

当前 `7bc4119` 工具保留短效链接刷新，并新增完整 ZIP 私有缓存。GitHub 下载链接本次实测有效期为 600 秒，分块下载每隔约 300 秒在下一个请求前获取新链接，401/403 时最多刷新并重试一次；各分块仍必须满足相同 ETag、Range 和最终 ZIP 摘要。链接只保留在内存中。传输失败仍会清理临时分块，当前没有内置跨运行分块断点续传；本轮人工恢复保留分块是一次性操作，不能当作已有产品能力。

| 错误码 | 处理方式 |
| --- | --- |
| `REQUEST_REJECTED` / `REQUEST_TIMEOUT` | 检查 run ID、模式和请求连接；没有进入生产更新 |
| `RELEASE_BUSY` | 已有发布正在执行，等待该次结果，不并发操作 |
| `PLAN_REQUIRED` | 使用该 build 的当前 attempt 和服务器当前工具版本重新运行 plan；成功后再 publish |
| `GATE_REJECTED` | 检查所选构建的仓库、workflow、分支、状态及可信服务器配置 |
| `BUILD_FETCH_FAILED` / `BUILD_FETCH_TIMEOUT` | 检查构建是否成功、artifact 是否过期及服务器到 GitHub 的网络；生产尚未修改 |
| `DOWNLOAD_AUTH_REJECTED` | 下载返回 401/403，刷新后仍失败；检查访问权限与下载服务，不能仅凭此码断定链接过期 |
| `DOWNLOAD_RETRY_EXHAUSTED` | 传输重试次数耗尽；检查服务器网络，再运行 plan |
| `RANGE_PROTOCOL_INVALID` / `ARTIFACT_DIGEST_MISMATCH` | 分块协议或最终摘要不匹配；拒绝安装，不可跳过校验 |
| `PREFLIGHT_FAILED` | 检查数据库契约、依赖及当前生产基线；不要绕过校验发布 |
| `PUBLISH_FAILED_ROLLED_BACK` | 代码和配置已经恢复，业务数据未回档；检查旧版本并修正候选代码 |
| `ROLLBACK_FAILED` | 需要维护者检查服务和私有恢复记录，先恢复可用状态 |
| `COORDINATOR_FAILED` / `INTERNAL_ERROR` | 检查服务器服务状态和已有记录；某些预检失败发生在创建备份之前 |
| `SSH_CONNECTION_OR_PROTOCOL_FAILED` | 没收到可识别的服务端回执，检查 SSH 和服务端状态，不等同于“没有更新” |
| `RELEASE_STATUS_UNKNOWN` | 客户端等待超过 110 分钟；先查服务器，确认当前版本和是否仍有发布进程，再决定下一步 |

## 本轮实际验收

以下为 2026-10-24 实际结果；云构建、工具测试、服务器预检与生产发布分别记录。

| 项目 | 实测结果 |
| --- | --- |
| GitHub `production` 环境及受限 SSH 安装 | 已配置环境、`main` 分支限制及两项环境 secret；真实受限 SSH 登录、强制命令和禁止转发验证通过。未据此宣称已启用额外人工审批 |
| 自动 Build run ID / attempt / 三组件 SHA | [40056397838](https://github.com/papaoo/bojizhuye/actions/runs/40056397838)，attempt 1，全部 5 个 job 成功；下表三个源码提交已正式发布 |
| 当前服务器受信工具及 Linux 测试 | `7bc41194449ec38682791acf15c1398d13b48c16` 已推送两个 main 并通过固定 SSH 安装；98 项 Linux 测试全部通过，工具升级期间生产保持不变。此前 `580d417` 的 76 项及 `5ac6d1b` 的 88 项结果保留为历史 |
| 固定工具维护实测 | [run 40069121141 / job 120798538026](https://github.com/papaoo/bojizhuye/actions/runs/40069121141/job/120798538026) 成功；回执 `ok:true`、`tests:88`、`nginx:valid`、`services:active`、`applicationPublished:false`、`productionUnchanged:true`。检查 6 个服务 PID、3 个当前版本链接、7 项配置和 4 个证书/私钥的内部摘要，未输出私密内容 |
| 临时维护凭据清理 | `LEANSCHOOL_MAINTENANCE_SSH_PASSWORD` 已删除，并通过 API 查询返回 404 确认；日常受限 SSH 发布配置保留 |
| 完整 ZIP 缓存与时限升级 | 新增 root 0700/0600 完整 ZIP 缓存和严格复验；plan 下载 90 分钟、客户端 110 分钟、job 120 分钟。安装 `7bc4119` 无需重新创建临时密码 secret；此次完整 ZIP 校验、plan 与 publish 已通过 |
| 旧版本服务器本机 plan | 已通过，`changed.website/backend/adminUi` 均为 `false`；基线包在 root 保护的 `/var/lib/leanschool/releases/bootstrap-40049018447` |
| 手动 Publish `plan` 第一次 | [40057651101](https://github.com/papaoo/bojizhuye/actions/runs/40057651101)：为调整下载时限主动终止在下载阶段，Actions 结果 failed；未进入生产发布 |
| 手动 Publish `plan` 第二次 | [40058666408](https://github.com/papaoo/bojizhuye/actions/runs/40058666408)：下载阶段 `BUILD_FETCH_FAILED`；当次未发布新版本 |
| `5ac6d1b` 工具下的新包 `plan` | [40069469354](https://github.com/papaoo/bojizhuye/actions/runs/40069469354)：下载到约 594 MiB 时触及原 60 分钟上限，最终 `BUILD_FETCH_TIMEOUT`；生产未变 |
| 完整包恢复与正式 `plan` | 一次性利用预先保存的 69 个完整候选分块（552 MiB），补齐 45996892 字节；整个 624810844 字节 ZIP 摘要与 GitHub 一致。随后 [plan 40074280787](https://github.com/papaoo/bojizhuye/actions/runs/40074280787) 成功；这次人工恢复不是内置断点续传 |
| 正式 `publish` | [40074361422](https://github.com/papaoo/bojizhuye/actions/runs/40074361422) 成功，2026-10-24 07:32:21 UTC 完成，应用版本更新为下表三个提交 |
| 相同版本重复发布 | [40074617715 / job 120815221286](https://github.com/papaoo/bojizhuye/actions/runs/40074617715/job/120815221286) 成功，回执 `deployed:false`、三个 `changed` 均为 `false`、`databaseRestored:false`；root 独立复核 6 个服务 PID、3 个 current 链接、daily-current 记录与备份集合均未变，数据库结构/证书/核心配置继续一致 |
| 当前生产版本及旧域访问 | 官网 `c5d3a4b`、Java `e278c87`、Vue `77b400e`；旧域外部 11 项 HTTP/API/资源检查全部通过，包括[首页](https://boji.tensorbot.xyz/)与[管理后台](https://boji.tensorbot.xyz/console/) |
| 本轮备份与生产保持检查 | MySQL 备份 2950167 字节、SQLite 备份 512000 字节；独立核对数据库结构、证书、7 项核心配置和 15 项保留配置通过；本机双域 TLS/API 检查通过，Nginx/MySQL/Redis PID 未变 |
| 恢复与业务交互验收边界 | 未人为制造生产故障演练回退，未进行交互式管理员登录或业务写入；不将备份存在、只读验收或单元测试写成这些项目已完成 |
| 外网新域访问 | 新域公网及 ICP 备案仍为独立待办；本机严格双域 TLS 通过不代表外网新域已经全面可用 |

本次已发布的源码版本：

| 组件 | 完整提交 |
| --- | --- |
| 官网 | `c5d3a4bd5f84bf8eadc9bf3c914bdee2bd826d46` |
| Java | `e278c8747575d047721d3ee622c6015abf16d8a8` |
| Vue | `77b400e7fd755a4d96016338822f5ef052d1af16` |

最终 artifact ID 为 `12545742030`，ZIP 为 `624810844` 字节，SHA-256 为 `05e3e4a4d3d110f6b1ab6a9e2ab90faa110b4143d9e383d298fd7da12cae80e9`，服务器完整校验与 GitHub 记录一致。该包已取得完整 plan 和 publish 成功回执。

本次发布的私有备份目录为 `/var/backups/leanschool-daily/20261024T073024Z-fad70a1b5f834893baf67fdc5776dbde`。备份留在服务器，不上传 Git 或 Actions；恢复代码和配置时不自动回灌业务库。

下载故障补充证据：仅请求该 artifact 的 API 重定向，未下载包、未记录签名 URL；本次响应的有效期相对服务端 Date 为 600 秒。此前失败发生在 11 分 19 秒后，旧下载器从不刷新链接，因此到期是有证据支持的原因推断；失败日志没有保留原始 HTTP 状态，不能声称已直接捕获过期 403。新代码同时增加四类固定错误及 Actions annotation，便于后续定位，未经新服务器预检的修复不视为部署成功。

工具升级历史：先通过维护提交 `1f0547c` 安装 `5ac6d1b39b391d038e1813ca28a142c0eacd99bd`，其归档为 125845 字节、45 个文件，SHA-256 为 `135189734925a5f6e439771d9cba9c946e446db69d948f9c57c405929ce994be`，任务后临时密码 secret 已清理。随后为完整 ZIP 缓存及时限升级安装 `7bc41194449ec38682791acf15c1398d13b48c16`；当前受信目录为 `/opt/leanschool/release-tools/7bc41194449ec38682791acf15c1398d13b48c16`，无需重新创建临时密码 secret。本地同时进行的设计改动未纳入本次发布；真实登录、业务操作、新域公网与 ICP 备案继续独立验收。

安全回执和独立验收结果汇总见[本轮验收证据](evidence/daily-release-20261024.json)。服务器已清理本次候选分块与旧失败下载临时目录约 750 MiB，保留完整已验证 ZIP 缓存、当前生产版本和发布备份；这是本次明确清理，不代表已有自动保留策略。

