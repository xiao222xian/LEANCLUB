# 在服务器直接下载经过核验的 GitHub 代码产物

`deploy/ubuntu/fetch-github-artifact.py` 是独立 Python 3.9+ 工具，仅通过 HTTPS GET 读取固定私有仓库 `papaoo/bojizhuye` 的 Actions 元数据和代码产物。它不触发构建、不安装、不启动服务，也不更改数据库。

## 调用约定

在服务器上使用已审阅的脚本，预先创建一个受控父目录。通过标准输入传入单个 JSON 对象，恰好包含 `username`、`password` 两个非空字段。`password` 指 GitHub Git 凭据中的访问令牌，不是 GitHub 账号登录密码；令牌只需具备目标仓库的 Actions 读取权限。凭据来源应为已授权的内存凭据提供程序，不要放在命令行、脚本、环境文件、临时文件或终端回显中。

将受控凭据提供程序的标准输出通过管道接到下列命令的标准输入；尖括号为需要替换的公开参数：

```text
python3 fetch-github-artifact.py \
  --run-id <成功运行的数字ID> \
  --artifact-name leanschool-release-<运行ID>-<成功运行的attempt> \
  --expected-workflow-sha <已审阅workflow的40位提交SHA> \
  --output /root/leanschool-releases/<尚不存在的新目录>
```

不要把组件源码提交误填到 `--expected-workflow-sha`；它对应该次 Actions run 的 `head_sha`，并与发布清单的 `toolsCommit` 一致。网站、Java 和后台 UI 的三个提交由发布清单另行报告。若重新运行 workflow，必须使用最新成功 attempt 对应的最终产物名；不接受 `backend`、`console` 等中间产物。

单连接吞吐较低时可追加 `--download-workers 8`，允许范围为 `1` 到 `16`，默认 `1` 保留原单流行为。并发模式先用 `GET Range: bytes=0-0` 检查支持情况，必须返回 `206`、与 API 一致的总字节数及强 ETag，再把完整文件分成互不重叠的区间同时下载。每段附带 `If-Match`，逐一核对 `Content-Range`、`Content-Length`、ETag、identity 编码及实际收到的字节数；拼接后仍须通过完整 ZIP SHA-256 和全部发布包校验。

并发模式对连接失败、截断和 HTTP 408/429/500/502/503/504 最多尝试三次，每次重取该分段；等待分别为 0.5 秒、1 秒。范围/ETag 不符、HTTP 200 忽略 Range 等情况直接失败，不隐式降级为未验证下载。签名地址过期后也不会绕过 API 身份校验；应重新运行工具取得新地址。签名 URL 和令牌均不写入分段文件，分段使用独立临时目录并在结束时清理。拼接期间额外占用约一份 ZIP 的磁盘空间。旧进程已经下载的部分文件不会被新进程复用，下载吞吐仍受服务器总带宽限制。

输出目录必须不存在，父目录必须已经存在。成功时仅生成下面六个普通文件，并在标准输出返回 JSON 摘要：

```text
yudao-server.jar
java-release.json
console.tar.gz
website.tar.gz
release-manifest.json
SHA256SUMS
```

JSON 包含运行、提交、ZIP SHA-256、逐文件摘要/大小以及 `deployed: false`。成功退出码为 `0`。下载及验证失败退出码为 `1`，标准错误只含经过控制的失败说明。校验通过前不创建目标目录；最后发布文件时发生磁盘等系统错误，可能留下不完整目标目录，此时不可用于部署，应检查后选择新的输出目录重新下载。工具不会覆盖已有目录。

## 核验与凭据边界

- run 必须属于固定仓库，来源仓库也须相同，状态必须为 `completed/success`，并匹配指定完整 SHA。
- 按 run 分页读取全部产物，要求唯一同名、对应成功 attempt、未过期；再次查询 artifact，检查关联 run、SHA、体积和必备 `sha256:` digest。
- API 的签名下载地址仅保留在内存，不输出。下载使用独立 HTTP 客户端和全新请求；每次跳转都检查 HTTPS、443 端口、无 URL 凭据，不发送 GitHub Authorization、Cookie 或 Referer。所有网络操作只有 GET。
- 下载完整 ZIP 后与 GitHub API digest 比对。ZIP 必须恰好含根目录六个允许文件；拒绝目录、软链、特殊文件、加密成员、重复项和超限展开。
- 再校验 `SHA256SUMS`、发布清单的 run/attempt/toolsCommit、四个代码文件摘要，以及 Java stamp 对应 JAR 的摘要/大小。
- 检查两个内部 tar 的路径、成员数量和展开大小，拒绝链接、SQL、运行库、私有初始化包、密钥文件和非示例 `.env`。网站归档须保留对应 Git commit 的 PAX comment；后台归档须包含 `console/index.html` 且仅有 `console/` 内普通文件。
- 凭据及签名 URL 不写入文件；临时下载 ZIP 随处理结束删除。Ubuntu 下输出目录权限为 `0700`，文件为 `0600`。文件名/结构/摘要核验不能替代源码审查或生产验收；本工具不扫描 JAR 内业务内容，也不导入私有初始化数据。

如 GitHub 返回缺失摘要、运行失败或包过期，不做降级下载。应选择已完成且满足约定的运行。

## 本地隔离验证

```text
python -m unittest discover -s tests -p test_fetch_github_artifact.py -v
```

测试使用合成 ZIP/tar 和模拟 HTTP，并禁止真实网络请求。2026-10-24 另对已有 `leanschool-20261024/bundle` 的两个代码归档做了只读兼容检查：console 2757 个成员；website 180 个成员，PAX commit 为 `1a4ba54b47018e3ed78b013a66f8ac4840b59ca9`。两者通过；没有读取或打包该目录的私有初始化包。

接口依据：[GitHub Actions artifacts REST API](https://docs.github.com/en/rest/actions/artifacts?apiVersion=2022-11-28)、[workflow runs REST API](https://docs.github.com/en/rest/actions/workflow-runs?apiVersion=2022-11-28)，核对日期 2026-10-24。私有仓库令牌必须已获授权；fine-grained token 需要 Actions: read。脚本自身仅执行读取，不申请或修改令牌权限。
