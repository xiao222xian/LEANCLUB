# com 官网与统一后台发布

时间：2026-10-24（Asia/Shanghai）。用户指定 com 部署最新公开页面，后台复用 xyz 服务器，清空两站统计与公开评论；最后明确不再制作额外整站/数据库备份，由用户测试真实交互。

## 入口

- 官网：https://leanmuscleclub.com/
- 投票与留言：https://leanmuscleclub.com/city-vote
- 活动：https://leanmuscleclub.com/events
- 若依后台：https://leanmuscleclub.com/console/
- 自带活动后台：https://leanmuscleclub.com/legacy-admin
- 原 https://boji.tensorbot.xyz/ 继续保留。

## 部署内容

47.88.2.70 提供固定版本静态文件；`/api/`、`/app-api/`、`/admin-api/`、`/console/`、`/infra/ws` 经 HTTPS 代理到152.136.108.87。固定 IP 连接校验证书名 boji.tensorbot.xyz，浏览器始终访问 com 同源接口。原服务器专用入口只接受47.88.2.70，并仅信任该边缘传入的真实客户端 IP。Origin、Referer、Cookie、Authorization和CSRF保持原样。两站共用原若依/MySQL/活动SQLite，没有新增若依实例或公开内部端口。

静态来源 `a8016ee4eb18064f5955556d830956e5364d437f`，104文件，未包含另一会话未提交的薄肌设计。com产物canonical/OG改为com；专用备案脚本清空、隐藏备案区域，旧xyz备案配置保留。弹幕扩为24条自然短句，去“官方话题”标记，桌面48→68px/s、手机48→56px/s，间隔3→2.2秒。装饰短句不伪造用户、不入评论库、不计数；真实评论联动和暂停/减少动态/屏外停播保留。

新站目录 `/srv/www/leanmuscleclub/releases/20261024T080222Z-a8016ee4eb18`；`current` 指向该版本。状态文件 `/srv/www/leanmuscleclub/edge-current.json`。原证书和certbot续签服务保留。原 `leanmuscleclub-api` 已停止并取消开机启动；旧SQLite只有clubs/events，保留原路径但不再服务新站。

## 清理结果

16:01:16共用库投票13→0、投票IP占用13→0、访问事件246→0、公开评论0→0；旧投票通知与相关限流同步清理。25张保留表指纹一致，管理员、私密意见、素材、活动、报名、订单保留；未清MySQL。之后正常访问产生新统计。

按用户最后要求，未新增整站或数据库备份；之前日常发布备份保留，本次保留旧发布目录和小型配置回退副本。清理回执 `/var/backups/lean-edge-reset/20261024T080116Z/result.json` 的 `databaseBackupCreated=false`，目录名称不代表创建过数据库副本。

## 发布方式和验证范围

`deploy/edge/release.py manifest` 从固定Git对象生成SHA256清单；服务器直接从GitHub下载源码，逐文件核对后生成本域静态产物并切换目录。手机热点仅传小型清单和控制脚本。**Git推送不会自动部署com静态页**，需显式执行edge发布；后台继续采用既有build→plan/publish，影响两域共用服务。

第一次切换因reload尚未完成，旧worker命中已停止的旧API而检查失败，工具恢复原配置和目录；修复为等待新worker接管后停旧API，第二次成功。修复提交97c192752b3370e30778c552eff6a4d5d2901bc8。历史首次接入工具upgrade-fullstack-site.py仍限制旧双域，不应用它覆盖当前三域环境；日常发布保留现有环境和Nginx配置。

3项针对打包完整性、备案/域名处理及精确重置的Python测试、公开评论客户端回归通过。上线后：首页、投票、活动、学校、后台页面均200；学校查询返回5条、评论总数0、CMS无旧绝对域名；管理统计匿名401、内部桥接404、旧xyz200。没有完成或宣称真实登录/支付/短信/报名验收，交互由用户测试；既有短信和真实收款能力限制保留。
