# 本地运行库加密备份

本次按用户指定，只保存本地 `data/local.sqlite3` 与 `data/media/`，不是线上数据库，也不包含活动 QA 演示库。SQLite 通过只读 backup API 获取一致快照，原运行库未迁移、未清空；源码中静态素材另由 Git 保存。

| 项目 | 本次记录 |
| --- | --- |
| 快照时间（Asia/Shanghai） | 2026-10-23T08:17:59.6261643+08:00 |
| 加密文件 | [local-runtime-20261023-081759.enc](local-runtime-20261023-081759.enc) |
| 密文字节数 | 1,310,330 |
| 密文 SHA-256 | `320e566d51b3fda520cb9c8c80a1852b4ee1dfc738000660c4f12fbdb2cc49a8` |
| 恢复检查 | 数据行内容一致、数据库完整性及外键通过、6 张海报逐文件摘要一致 |
| 回归检查 | 138项测试，137通过，1项因Windows无符号链接创建权限跳过；恶意归档链接拒绝测试通过 |

完整内容由 AES-GCM 加密，密钥只留本机 `data/private-backup-keys/`，没有提交到任何仓库。克隆源码后，须另行取回对应私钥文件才能恢复；管理员密码不能替代该密钥。请将密钥另外妥善备份。

本机对应密钥路径为 `data/private-backup-keys/local-runtime-20261023-081759.key`（此处只有路径，没有密钥内容）。密钥目录 NTFS 权限仅当前用户和 SYSTEM 可访问。

需要 Python 3.11+。在仓库根目录执行：

```powershell
python -m pip install -r requirements-backup.txt
python scripts/database-backup.py verify --input backups/local-runtime-20261023-081759.enc --key-file data/private-backup-keys/local-runtime-20261023-081759.key
python scripts/database-backup.py restore --input backups/local-runtime-20261023-081759.enc --key-file data/private-backup-keys/local-runtime-20261023-081759.key --target data/restored-local-manual-20261023
```

恢复目标必须是新目录；此操作只离线还原文件，不启动网站，不发送备份中的历史飞书通知。恢复目录布局和未来切换运行库的步骤见[备份与恢复说明](../docs/database-backup.md)。本轮未替换线上库或部署新网站。
