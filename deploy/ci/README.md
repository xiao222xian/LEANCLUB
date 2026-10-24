# 固定版本的云构建

2026-10-24。[云构建 40049018447](https://github.com/papaoo/bojizhuye/actions/runs/40049018447) 的 Java、前端和打包三个 job 均成功。工作流只允许 `papaoo/bojizhuye` 的 `main` 手动触发；此记录证明 Linux 云构建通过，生产验收另行记录。

产品源固定为官网 `1a4ba54b47018e3ed78b013a66f8ac4840b59ca9`、Java `d5ff0bb3af7683fb4fd1565aed1c58956aca3e6c`、Vue `8554101037814965582590b88867e1883352cf0d`。本次工作流工具提交 `8384d2479a03c9f1be80eb5287a520ff85c238a9` 单独记入 `toolsCommit`，不会让官网产品代码随 `main` 移动。三个源码在同一个私有仓库，使用只读 `GITHUB_TOKEN` 检出，不需要另设 PAT；检出不持久化凭据。

Java 与 UI 使用两个独立的 `ubuntu-24.04` x64 job，汇总使用第三个 job。固定 Temurin **25.0.5+8**、Maven **3.9.7**、Node **24.20.0**、pnpm **10.30.0**。JDK 版本与本轮目标服务器一致，区别于此前 Windows 本地验收用的 25.0.4.1+1；Linux 产物使用自己的摘要。JDK/Maven 压缩包校验官方固定 SHA256/SHA512。全部 actions 使用官方仓库的完整提交 SHA，标签解析及签名核验记录在 `actions.lock.json`。

Maven 只缓存依赖仓库，键由后端 POM 决定；CI settings 仅含公开 Central 镜像，不复制本机代理、E 盘路径或凭据。Node 依赖按锁文件全新安装，避免带入 Windows 原生模块。UI 类型检查和构建分别采用 6144 MiB 堆上限，并验证 `/console/`、同源 `/admin-api`、静态资源与环境变量泄漏边界。Java 全 reactor 构建继续显式跳过上游测试，不能标注为全业务测试通过。

首次云构建 `40047678258` 在自动声明生成前执行类型检查，干净检出因此缺少被 Git 忽略的声明文件。现已让 `leanschool` 正式构建生成声明，并调整为先构建、确认声明文件存在，再运行完整 `vue-tsc` 与路由检查。独立源码副本验证和云构建 `40049018447` 均通过，未跳过类型检查。

最终 `leanschool-release-<run_id>-<attempt>` 只包含：

- `yudao-server.jar`、`java-release.json`
- `console.tar.gz`，内部前缀 `console/`，安装时使用 `--ui-prefix console`
- `website.tar.gz`，由固定提交 `git archive` 生成，保留 Git PAX 提交标记
- `release-manifest.json`、`SHA256SUMS`

初始化包、SQL、运行库、证书、私钥和真实环境配置不作为工作流输入，也不作为 artifact 输出。不要在 CI 调用原 `deploy/build-release.py`，它要求私有初始化包。新工具只接收编译产物；汇总时再次检查 JAR 的 22 个模块、Java 25 class 版本、摘要，以及前端归档内容。中间产物保留 1 天，最终产物保留 3 天，上传使用明确文件清单。

经主任务审核、提交、推送到私有仓库 `main` 后，才可手动触发：

```sh
gh workflow run leanschool-release.yml --repo papaoo/bojizhuye --ref main
```

构建完成后，在服务器用已有的私有仓库读取授权直接下载，避免通过手机热点中转大 JAR；下载授权须能读取该仓库的 Actions artifacts，不把令牌写进命令参数或 Git：

```sh
gh run download RUN_ID --repo papaoo/bojizhuye \
  --name leanschool-release-RUN_ID-ATTEMPT --dir /data/leanschool-release-RUN_ID
cd /data/leanschool-release-RUN_ID
sha256sum --check --strict SHA256SUMS
```

再由部署流程独立提供已审核的 `private-init.tar.gz`。云构建不连接服务器、数据库或外部业务渠道，不执行部署。GitHub 私有仓库的计费额度需单独核对；6144 MiB 是配置的堆上限，不代表进程总内存上限。

参考：[私有 runner 资源](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)、[手动触发要求](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch)、[下载 artifacts](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts)、[setup-java](https://github.com/actions/setup-java)、[upload-artifact](https://github.com/actions/upload-artifact)。
