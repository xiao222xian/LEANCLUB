# 薄肌学院全量 Java 生产发布准备

2026-10-24。这是生产模板、发布工具和实际安装入口。**完整JAR已在Windows隔离环境以prod配置、仅DML权限的新库账号冷启动通过；Linux安装/systemd及生产Redis ACL尚未验证。** 不读取/复制本地运行库，不替换当前公网SQLite。执行方式见 [全量平台安装与回退](PLATFORM-RUNBOOK.md)：`plan`只读，`prepare`准备文件，`init-db`初始化全新独立库，`activate`健康检查后切换，`rollback`回退代码。

## 本次保留范围

当前 server POM 的全部业务模块保留：system、infra、member、leanclub、report、bpm、pay、mp、商城 product/promotion/trade/statistics、crm、erp、ai、mes、wms、hrm、fms、pms、oa、im。IoT 与 ai1 原本未启用，不在本轮擅自启用。它们打在**一个单体 JAR**内；不要求 Kubernetes 或另建微服务集群。

上游基线 `e4977f6ccaff29ded7024462d4778e4ccc025a61`；本次定制源码已锁定私有 `platform/backend` 提交 `d5ff0bb3af7683fb4fd1565aed1c58956aca3e6c`，独立git archive完整重构建成功。新JAR为611179571字节，SHA256 `6800780ea3a1585a676114598bd9c034754d3c5ef267d5f9c3d1bc267cb4fd53`。根POM要求Java25、Spring Boot4.1.0；构建成功不代表生产冷启动已通过。

## 发布物和路径

- `yudao-server.jar`：从已提交的完整 Java 源码构建。构建在开发机/构建机进行，服务器不跑 Maven/Vite 开发服务。
- 原 Vue 管理端单独构建静态文件；同域建议 `/console/`。`VITE_BASE_PATH=/console/`，API 走同源 `/admin-api`。源码 `.env.prod` 仍有 localhost 默认，不能原样构建。
- `application-prod.yaml` → `/etc/leanschool/application-prod.yaml`；`java.env.example` 仅模板，实际 secret file 为 root 所有 0600。
- Java 发布目录 `/opt/leanschool/java/releases/<commit>/`，`current` 软链接指向具体版本；Java25 runtime `/opt/leanschool/jdk/bin/java`。数据 `/var/lib/leanschool`，日志 `/var/log/leanschool`，运行用户 `leanschool`。
- Java 127.0.0.1:48080、健康 48081；MySQL、Redis、官网 Python 5381 同机回环。公网仅 Nginx 80/443。`/console/` 页面与 `/admin-api` 是不同路径，管理端授权仍由原系统完成；公网不转发 Actuator、Druid、Swagger 或 Spring Boot Admin。

完整Java需要MySQL和Redis。安装入口只复用已独立安装的MySQL8.0.16+，创建新schema/专用用户；Redis6.2+新建独立127.0.0.1:6382实例，保留已有全局实例。先复核服务器真实CPU/RAM/磁盘，再决定内存限额；模板Xmx2304m来自本地启动参数，绝不是服务器最低配置或吞吐承诺。

## 安全初始状态与功能开放

生产只启用 `prod` profile，不加载 `local`/`dev`；否则会引入示例通道、mock登录等。模板关闭mock、请求体访问日志、OpenAPI/Swagger文档接口、Druid、Quartz自动任务、Flowable异步执行和隐式DDL；管理员图形验证码开启，短信码不再固定。保留全部业务源码和内部页面。上游Knife4j的`/doc.html`静态壳仍可在loopback读取，但没有OpenAPI数据，公网Nginx不得转发此路径；不为删除空壳而关闭全局静态资源、影响报表功能。

未配置的第三方集成通过渠道配置、独立开关和空种子保持关闭，不增加全局外连限制。模板关闭自定义AI供应商、社交登录自动配置与小程序物流同步；保留底层SDK bean所需的无效占位，避免继承上游演示账号。模板支付回调仅正确占位；渠道凭据/应用尚未配置时业务不可用。**这不是声称全部第三方功能已验收。** 日后启用正式渠道需明确配置和验收，不能沿用示例参数。

`leanclub` 当前强依赖 PAY 且构造注入 `PayOrderApi`；binding=false 只拒绝建单调用，不代表能从依赖里删除 PAY。两套桥接签名默认关闭；Python版本、数据库迁移和双方独立签名就绪后再打开 `LS_OPERATIONS_ENABLED` / `LS_MEMBER_ORDERS_ENABLED`。会员短信登录、真实支付保持关闭，用户无需为部署填写虚假商户。

## 全新 MySQL 初始化

1. 建**新的生产 schema**和仅该 schema 可读写的应用账户。检查为空；不要导入 `runtime` 的整库备份、旧账号/密码、令牌、租户、商户、通知、模型或演示业务数据。已有公网 SQLite 独立保留。
2. 使用已核对的上游 base/各业务模块 SQL 与本项目 additive DDL，显式逐个传入离线工具：

   ```text
   python3 prepare-schema.py --input /private/base.sql --input /private/member.sql --input /private/pay.sql --input /private/other-reviewed-module.sql --output-dir /private/leanschool-schema-<release>
   ```

   工具仅输出CREATE TABLE，以及system_menu/system_dict_type/system_dict_data的VALUES种子；其他INSERT、UPDATE、DELETE、DROP、USE等不会进入输出。CREATE按外键父表优先排序，保持外键检查开启；缺失父表、循环依赖或同表结构冲突会失败，并记录所有输入与输出SHA256。可增加 `--flowable-jar <reviewed-jar>`，提取该JAR中固定Flowable8.0.0的45表、索引、外键与版本元数据，单独为003文件；不从运行库复制任何流程记录。输出仍是私有安装物，不应推公开Git；工具并不替代配套SQL资料的授权确认。上游资料当前覆盖16模块，另有报表SQL和ai_workflow本地兼容迁移，必须按schema-audit核查完整性；不可把“示例四个输入”当完整初始化清单。
3. `001-schema.sql`、`002-dictionaries-menus.sql` 只向全新空库应用，mysql batch 禁止 `--force`。核对表数量与映射，不能用 IF NOT EXISTS 掩盖现有结构差异。配套资料里的流程、报表、会计账套、模型和客户均非生产真实业务；如需标准模板，单独审查后导入。
4. 新管理员使用 `prepare-bootstrap.py --username <chosen-user> --tenant-expiry <YYYY-MM-DD> --output /private/bootstrap.private.sql`，交互读取独立强密码（依赖 Python bcrypt）；可指定 root 私有密码文件。它只生成 bcrypt hash 与新 OAuth 客户端 secret，文件0600，不回显值；仅在四张身份表均为空时允许初始化，所有写入同一事务。全局系统租户 id1/package0 与 super_admin 为源码约定，不复制上游示例管理员。首次登录成功后新增实际运营角色；不要给普通员工 super_admin。
5. 为prepare-schema增加 `--leanclub-permissions <module>/sql/mysql/leanclub-permissions.sql --school-dir <schools-2026-source-dir> --site-seed <website>/web/creator/site-content.seed.json`：分别生成004权限、005学校和006品牌草稿及`site-content.ready.json`。16项权限只定义不自动授权普通员工；2952校核验原始XLS摘要、唯一码和省份/层次计数；品牌从源JSON生成version1未发布草稿，原有首页滚动内容保持不动，新域ICP备案未办，草稿对应icpText留空。通过原CMS保存/发布接口验证后再公开，不直接假造发布历史。v5表与PAY binding DDL已经应在001清单中，不重复应用。与 Python SQLite 的关联/签名需要独立验收。
6. 当前 JAR 内 Flowable为8.0.0。先使用该 JAR 匹配的引擎建表脚本；若选择引擎自动初始化，必须是**尚未开放、全新空库的一次性初始化进程**，提前备份，退出后保持本模板 `database-schema-update=false` 再次冷启动验证。不得对运行中的旧库开放 auto-DDL。流程部署定义和审批人由真实运营配置，不自动沿用演示流程。

## 构建与只读预检

从干净提交构建后执行（模板仓库的 `scripts/build.ps1` 会跳过上游测试；需另外跑本次变更对应测试）：

```text
python stamp-release.py --source <committed-java-checkout> --jar <new-built-jar> --output <new-release>/release.json
python preflight.py --jar <new-built-jar> --manifest <new-release>/release.json --java <jdk25>/bin/java
sudo python3 preflight.py --jar /opt/leanschool/java/current/yudao-server.jar --manifest /opt/leanschool/java/current/release.json --java /opt/leanschool/jdk/bin/java --env-file /etc/leanschool/java.env --production --check-local-services
```

preflight 不写数据库、不连接外部网站、不打印 secret；校验 JAR 所有业务模块、class版本、SHA/提交、Java25、secret占位/独立性、Origin/管理路径与本地端口。端口可连不等于数据库认证/表结构/业务链路成功。stamp 只认干净提交，不能证明旧 JAR 是该提交构建；发布流程必须实际重构建。

部署前 `systemd-analyze verify`、Nginx `-t`，部署后以实际运行账户验证 Java/DB/Redis启动、健康、管理员新登录/错误密码/无权限、学校目录、CMS发布/回退、评论隐藏恢复、旧域投票与新域Host/Origin拒绝矩阵。仅代码备份不足以回退：Java/MySQL、官网/Python/SQLite、CMS素材与网关版本应绑定同一发布清单。回退代码不覆盖业务库；有不兼容迁移时停写，使用成对快照恢复。

## 本轮本机准备结果

实际读取原始base/Quartz、16份固定业务SQL包、GoView/积木报表、ai_workflow兼容DDL、leanclub v5/binding DDL和JAR内Flowable8，完整产物位于私有 `generated/full-seed-20261024/`：645表，其中Flowable45；4655条字典/菜单种子，逐条主键扫描重复0；含16项薄肌权限、2952校和未发布品牌草稿。该目录被Git忽略，未读取运行库或导出客户数据。当前JAR只读预检通过：Java class69/Java25，全部22业务模块，704依赖。YAML解析、产物摘要和17项隔离工具检查通过，见`verification-20261024.json`。

本机MySQL8.3.0的独立新建QA空库已实际顺序导入001～006通过：645表/45Flowable、2952校31省、16权限、version1未发布品牌草稿、新域icpText为空，其他业务表为空。合成管理员bootstrap成功，重复初始化被CHECK约束拒绝（3819），随后禁用合成账号。首轮发现的Quartz外键建表顺序和空字符串十六进制SQL问题已修复，最终manifest为 `7694138803194202000e66a8833826045afb4a6844f4e4300fe2e27c4a168c02`。完整私有QA报告保留在generated内；未读取现有业务数据或操作生产。

随后固定新JAR与生产overlay在Windows独立端口48090/48091、空Redis6389/DB0完成真实prod冷启动。首次发现MySQL1298命名时区表依赖，已修为会话固定UTC+08:00。最终用仅SELECT/INSERT/UPDATE/DELETE的独立QA数据库账号启动成功，Flowable已创建、health UP且不暴露details、学校2952、CMS接口可用且种子仍未发布；启动前后645表集合一致，不依赖DDL权限。

匿名边界实测：`/v3/api-docs`、`/swagger-ui/index.html`、`/druid/`、`/actuator/env`返回HTTP200包裹业务码404；管理用户列表返回业务码401。`/doc.html`为1903字节静态壳，HTTP200且没有OpenAPI数据；原始QA将“所有文档路径必须阻断”断言记为failed，保留该记录，范围复核接受loopback空壳并要求公网网关404，不把它表述为所有文档URL均404。自建进程已停止、临时数据库账号删除、既有服务未更改。

冷启动实测使用MySQL8.3.0与Windows Redis5.0.14.1；它验证应用装配/连接，**不能替代目标Linux Redis6.2+ ACL、systemd、目录权限及实际Nginx验证**。当前未执行Linux生产安装/冷启动或真实渠道测试，不扩大为所有8.x数据库版本或完整业务验收通过。
