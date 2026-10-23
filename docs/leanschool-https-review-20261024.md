# 新域 HTTPS 部署脚本独立审查

2026-10-24。审查范围仅为 `deploy/ubuntu/enable-leanschool-https.py`、`leanschool.nginx.conf.in`、`leanschool-proxy.conf.in`，未执行生产安装，未修改旧域配置或数据库。

## 已修复

- 证书 ZIP 只在内存读取，不解压到用户控制的路径；拒绝绝对路径、穿越、Windows 分隔符、控制字符、重复条目、链接、超额大小/数量、加密私钥、重复密钥及歧义证书链。证书和私钥分别提取为规范 PEM。
- 安装前用 OpenSSL 校验至少24小时剩余有效期、系统可信链、`sslserver` 用途、`www.leanschool.net` 主机名和公私钥匹配；不输出私钥。没有替未覆盖的根域配置 HTTPS。
- 拒绝未归属本工具的配置、重复 hostname、证书文件/目录符号链接；现有配置、证书与权限写入 root 私有备份清单。
- 安装失败时恢复原字节与文件模式、恢复站点启用状态、删除本次新建的证书空目录；回滚失败明确报 `ROLLBACK_FAILED`，继续恢复其他文件且不谎报成功。
- TLS 验收明确禁用代理，保留完整证书验证，不使用 `curl -k`。检查新旧站、后台深层路由、缺失静态404、私有路径404与管理/扫码跳转；旧域 `/admin` 仍要求可用。
- `/console/assets/`、`/console/brand/` 和常见静态扩展名缺失返回404，真正的后台 history 路由才回退首页；隐藏文件拒绝访问。`/admin` 跳到指定的原芋道登录和内容工作区。
- HTTP 与 HTTPS 的访问日志都使用路径格式，省略查询串。三个 API 目标明确分开：Python5381、公众白名单网关5382、Java员工48080；共享片段不伪造 Origin/CSRF，读取超时由各 location 指定以避免重复 Nginx 指令。

## 验证范围

新增 `tests/test_leanschool_https.py`，17项通过。其中3项使用临时生成的真实 OpenSSL 证书和临时测试 CA：合法主机/匹配私钥通过，错误主机和不可信链拒绝。临时 CA 仅在该测试进程环境中信任，不写系统信任库。

其余使用合成ZIP、临时目录与受控命令模拟，覆盖安装成功、Nginx校验失败、reload失败、静态错误响应触发回滚、回滚部分失败、备份、旧域字节不变和配置边界。Windows中的受管 symlink 操作用 mock 模拟；未把该测试写成 Linux 生产验证。

本机没有 Nginx 二进制，模板只做源码边界检查。服务器实际安装仍必须通过脚本的 `nginx -t` 后才 reload；需要严格完成新旧域 HTTPS 实测。默认运行只核验证书，带 `--apply` 才修改配置，且 full-stack 健康与原后台产物必须已经就绪。

新域暂保留 `/legacy-admin` 与原 Python `/api/admin` 兼容能力；这并不把两套登录合并。待原芋道运营能力正式验收后，可另外制定旧入口收口计划。原域不会因本脚本被全站301。
