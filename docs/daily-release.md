# 薄肌学院日常开发与发布

更新：2026-10-24（Asia/Shanghai）。本文说明新日常发布流程；此前的[首次全栈部署记录](fullstack-release-20261024.md)保留为历史证据。**本轮实际云构建与生产发布结果见文末验收表；未填写的项目不代表已通过。**

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

大文件从 GitHub 直接下载到服务器，本机不需要中转 JAR。publish 必须复用成功 plan 的缓存，核对 run、attempt、artifact ID/摘要和当前可信工具目录，然后重新验证全部文件摘要及当前生产基线。服务器工具升级或构建 attempt 变化后，需要重新执行 plan。

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

请求输入截止 20 秒；plan 下载阶段截止 60 分钟且发生在生产修改之前；publish 只使用已验证的本地缓存，重新核对 GitHub 来源和生产预检。生产协调器负责各步骤的限时和失败恢复，不由 gate 在更新中途直接强杀。客户端最多等待 80 分钟，Publish job 上限 90 分钟。强制先完成 plan 再 publish，可避免把慢速下载与生产更新挤在同一次等待期限内。网络中断或外部取消仍需核查真实服务器状态。

服务器记录位置：

| 路径 | 内容 |
| --- | --- |
| `/etc/leanschool/release-gate.json` | 固定仓库/workflow身份和可信工具目录配置 |
| `/etc/leanschool/daily-release-host.json` | 本机发布身份标记 |
| `/etc/leanschool/daily-current.json` | 最近成功发布的源码、实际运行产物和验证记录 |
| `/var/lib/leanschool/releases/cloud/` | root 私有的代码缓存、`.verified.json` 产物身份回执和 `.planned.json` 成功预检回执；回执权限为 root 0600 |
| `/var/backups/leanschool-daily/` | 每次更新的私有配置/数据库备份及结果记录 |

记录和备份可能含私有运行配置与业务数据，不能上传至 Git 或作为 Actions artifact。下载缓存与备份目前不自动清理；维护者应按磁盘占用和恢复需要安排保留策略。

## 常见结果与处理

Actions 只展示固定安全错误码和固定说明，不展示原始子进程错误、令牌或私密配置。

| 错误码 | 处理方式 |
| --- | --- |
| `REQUEST_REJECTED` / `REQUEST_TIMEOUT` | 检查 run ID、模式和请求连接；没有进入生产更新 |
| `RELEASE_BUSY` | 已有发布正在执行，等待该次结果，不并发操作 |
| `PLAN_REQUIRED` | 使用该 build 的当前 attempt 和服务器当前工具版本重新运行 plan；成功后再 publish |
| `GATE_REJECTED` | 检查所选构建的仓库、workflow、分支、状态及可信服务器配置 |
| `BUILD_FETCH_FAILED` / `BUILD_FETCH_TIMEOUT` | 检查构建是否成功、artifact 是否过期及服务器到 GitHub 的网络；生产尚未修改 |
| `PREFLIGHT_FAILED` | 检查数据库契约、依赖及当前生产基线；不要绕过校验发布 |
| `PUBLISH_FAILED_ROLLED_BACK` | 代码和配置已经恢复，业务数据未回档；检查旧版本并修正候选代码 |
| `ROLLBACK_FAILED` | 需要维护者检查服务和私有恢复记录，先恢复可用状态 |
| `COORDINATOR_FAILED` / `INTERNAL_ERROR` | 检查服务器服务状态和已有记录；某些预检失败发生在创建备份之前 |
| `SSH_CONNECTION_OR_PROTOCOL_FAILED` | 没收到可识别的服务端回执，检查 SSH 和服务端状态，不等同于“没有更新” |
| `RELEASE_STATUS_UNKNOWN` | 客户端等待超过 80 分钟；先查服务器，确认当前版本和是否仍有发布进程，再决定下一步 |

## 本轮实际验收

以下由执行上线的维护者填写；单元测试、代码审查与服务器端到端验收分别记录。

| 项目 | 实测结果 |
| --- | --- |
| GitHub `production` 环境及受限 SSH 安装 | 待核验 |
| 自动 Build run ID / attempt / 三组件 SHA | 待核验 |
| 手动 Publish `plan` run ID 与结果 | 待核验 |
| 手动 Publish `publish` run ID 与结果 | 待核验 |
| 相同版本重复发布与未变化组件跳过 | 待核验 |
| 旧域官网、后台、API 与新域服务器端检查 | 待核验 |
| SSL 摘要、数据库保留、备份与恢复路径 | 待核验 |
| 外网新域访问及实际业务交互 | 独立验收，不由云构建成功推定 |

