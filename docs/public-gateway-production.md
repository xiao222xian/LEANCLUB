# 生产公众API网关

更新：2026-10-24（Asia/Shanghai）。本页为代码、配置契约和隔离测试说明；实际服务器启停与发布由部署记录确认。

## 入口与安装契约

- 入口：`deploy/ubuntu/public-gateway.py`，默认只监听 `127.0.0.1:5382`。
- 上游固定为loopback，默认Java `127.0.0.1:48080`；不接受客户端URL、上游主机或端口。
- 单元模板：`deploy/ubuntu/bojizhuye-public-gateway.service.in`，安装名建议 `bojizhuye-public-gateway.service`。
- 环境模板：`deploy/ubuntu/bojizhuye-public-gateway.env.in`，安装位置 `/etc/bojizhuye/public-gateway.env`。没有服务密钥、账号或Token；系统服务用户与代码路径沿用 `bojizhuye`、`/opt/bojizhuye/current`。
- 此进程没有静态站点、SQLite、通知worker或管理API；单元禁止访问 `/var/lib/bojizhuye`，采用只读系统及禁止提权等约束。Python导入公开接口类型不会打开数据库。

```dotenv
CLUB_ORIGIN=https://www.leanschool.net
CLUB_COMPAT_ORIGINS=https://boji.tensorbot.xyz
LEANCLUB_PUBLIC_GATEWAY_PORT=5382
LEANCLUB_YUDAO_PORT=48080
LEANCLUB_PUBLIC_TENANT_ID=1
```

Nginx仅把 `/app-api/` 原路径交给此网关，保留正确Host、原Origin/Referer/Fetch Metadata；不要直接把同路径广泛转给Java。`/admin-api/`、`/internal/`和其他Java模块均不在这个网关的允许范围。

健康检查明确提供主域Host：

```sh
curl --fail --silent -H 'Host: www.leanschool.net' http://127.0.0.1:5382/healthz
```

此检查只证明网关进程及路由配置正常，不证明Java、数据库、短信或支付可用。随后用允许的公开读接口独立检查Java连通性。

## 与本地预览共用的边界

白名单和代理实现集中在 `backend/public_app_gateway.py`，本地 `scripts/preview.py` 与生产入口都调用它：

- 精确method/path/query白名单只含原 `/app-api/leanclub` 公众门面；动态活动报名ID必须为32位小写十六进制。编码路径绕过、任意管理代理、多余查询参数、重复查询参数及URL中的token不通过。
- 私有公众接口要求唯一 `Bearer` 请求头，真实Token、MEMBER类型及启停状态继续由Java验证。网关不能单凭Bearer格式把请求视为已认证。
- 固定覆盖 `tenant-id`，不转发浏览器Cookie、员工Cookie、客户端租户或伪造转发IP。
- 请求上限32KiB、响应上限2MiB；拒绝分块/多重长度、重复安全头和GET正文。上游JSON业务状态保留，非JSON/重定向/过大响应/连接失败返回502。
- 不把上游 `Set-Cookie`、CORS、Location 或认证挑战传给浏览器；响应统一no-store和nosniff，可保留整数Retry-After。
- 生产额外按精确唯一Host选择单Origin；POST/PUT要求自身Origin或合法自身Referer，不把新旧域互相加入写入允许集合。本地预览保持既有开发调用行为。

目前不会给Java转发客户端IP，因此Java侧基于请求IP的策略看到loopback；真实短信默认关闭。未来若需要真实客户端IP限流，必须配套明确Nginx覆盖头和受信代理规则，不能简单透传浏览器自报的X-Forwarded-For。

## 验证范围

`tests/test_public_gateway.py` 9项真实loopback HTTP组通过：白名单、请求/响应限额、Host/Origin、Bearer/tenant/cookie隔离、上游故障、无数据库健康检查。上游为合成Java stub，403转发不等同真实会员身份验收。

`tests/test_integration_gateway.py` 10项本地预览回归通过，含原campaign Cookie继续只给其自身API以及 `/console` 核销跳转。原Origin回归另行运行。没有向真实Java发注册/短信请求，没有读取运行库或部署服务；Linux systemd/Nginx实际启动需在主部署流程验证。
