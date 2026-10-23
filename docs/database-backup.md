# 本地数据库的加密备份与恢复

本工具把用户明确指定的**本地 SQLite 数据库**及其上传海报打成可恢复的加密文件。源码、密文可同步到仓库；密钥、数据库明文、解密后的目录始终单独保存。它不会连接线上服务器，不会导入应用、迁移数据库、启动服务或推送飞书。数据库可能包含账号摘要、机器人地址和用户资料，因此即使某个目标仓库公开，也只同步密文。

## 环境

备份工具独立要求 **Python 3.11+**，且该 Python 的 SQLite 支持 `Connection.serialize/deserialize`。网站本身的 Python 要求不变。可选依赖与运行时依赖分离：

```powershell
python -m pip install -r requirements-backup.txt
```

加密依赖固定 `cryptography==50.0.2`，2026-10-23 核对过 [PyPI 对应发布页](https://pypi.org/project/cryptography/50.0.2/)。使用其标准 [AESGCM API](https://cryptography.io/en/latest/hazmat/primitives/aead/)：每个备份生成独立随机 256 位二进制密钥和 96 位 nonce，使用 AES-GCM 的认证标签验证密文，固定格式标识同时作为 AAD。密钥不是管理员密码，也不能从管理员密码推导或找回。

## 创建与验证

在仓库根目录运行；先暂停海报上传和历史版本切换，避免 SQLite 快照与外部媒体处于不同时间点。数据库采用 `mode=ro` 和 SQLite backup API 获取一致性快照，包含 WAL 中已提交的数据；仅在私有内存副本执行 SQLite `VACUUM`，得到可独立恢复的数据库。不会复制运行中的主文件来冒充一致性备份。若 WAL 存在但 SHM 缺失，工具拒绝打开，须先正常关闭写入进程再试。

```powershell
python scripts/database-backup.py backup --db data/local.sqlite3 --media-dir data/media --output backups/local-20261023.enc --key-file data/private-backup-keys/local-20261023.key
python scripts/database-backup.py verify --input backups/local-20261023.enc --key-file data/private-backup-keys/local-20261023.key
```

示例路径和日期仅演示命令，每次应使用新的文件名。输出文件和密钥必须不存在，工具从不覆盖。媒体只允许网站生成的 32 位小写十六进制 UUID WebP 文件（含 `-640.webp`），发现无关文件、符号链接、Windows 重解析点、读取中变动时拒绝打包；快照中的当前和历史海报引用均须在包中存在。默认静态海报随源码保存，无需复制进数据备份。

完整数据库、媒体、文件 SHA-256 和清单均在认证密文内部，仓库没有可读的表内容、记录数或清单。创建后自动执行解密认证、全部文件校验与 SQLite `integrity_check`。单包原始文件合计不超过 256 MiB、5000 文件；压缩与加解密在内存完成，不生成中间明文文件。这适用于当前小型站点；更大数据应改用专门备份系统。

密钥只允许存放在本仓库 `data/` 内或本仓库外，拒绝 Git 已跟踪的目标。`data/`、`*.key` 已由仓库忽略；**不要使用 `git add -f` 上传密钥，不要把密钥写进提交、终端输出或聊天记录**。文件使用 create-new 和 POSIX `0600`；Windows 上 `0600` 不能代替 NTFS ACL，操作者还须将密钥目录权限收紧到当前用户并妥善保存另一份离线副本。丢失密钥后无法恢复；拿到密钥的人可读取完整备份。

## 恢复到隔离目录

```powershell
python scripts/database-backup.py restore --input backups/local-20261023.enc --key-file data/private-backup-keys/local-20261023.key --target data/restored-local-20261023
```

目标必须是**从未存在的新目录**，且在 `data/` 内或仓库外。工具先完成认证、路径白名单、重复文件、大小、逐文件 SHA-256、海报引用及数据库完整性验证，再创建 `database/clubs.sqlite3` 与 `media/`。不使用自动解压，不跟随链接，不覆盖原有文件，不启动恢复库或发送旧通知。恢复失败时若留下新目录，保留原包与密钥，核查后另选新目录重试。

恢复布局是 `目标/database/clubs.sqlite3` 与 `目标/media/`，属于离线检查布局。`scripts/start-local.py --db` 会自动推导数据库旁的 `media/`，因此不能只换 `--db` 就启动此布局；未来手动部署应把数据库与媒体放回实际运行目录，或为服务明确设置 `CLUB_MEDIA_DIR=目标/media`。

检查恢复库应使用 SQLite 只读方式；不要直接用应用启动恢复副本做验证，因为服务可能消费备份中的待发通知。若将来要替换线上库：先停网站与通知工作进程，为当时线上库另做备份；核对代码版本、保留新产生的数据，决定如何处理历史 outbox/会话与支付状态，再由维护人员执行切换。此工具不执行这类破坏性切换，也不自动清除任何数据。

## 验证范围

```powershell
python -m unittest discover -s tests -p test_database_backup.py -v
```

测试只使用临时合成资料，覆盖完整往返、WAL 已提交页、源主文件/WAL 不变、每次密钥与 nonce 更新、错钥/篡改失败、现有文件/目录拒绝覆盖、Git 源码目录拒绝私钥/明文恢复、认证 ZIP 的穿越路径/链接拒绝、清单校验、限制、当前/历史海报缺失和无关媒体拒绝。未安装可选依赖或 Python 不支持序列化时，备份测试跳过并给出安装提示，既有网站测试不新增该依赖。真实本地库的备份/恢复及双仓库推送结果由本次执行记录另行记载。
