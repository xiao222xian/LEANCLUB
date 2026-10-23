# 新旧域名的同源隔离

更新：2026-10-24（Asia/Shanghai）。本文说明代码边界与配置，不表示生产已发布。

生产环境显式配置：

```dotenv
CLUB_ORIGIN=https://www.leanschool.net
CLUB_COMPAT_ORIGINS=https://boji.tensorbot.xyz
```

`CLUB_COMPAT_ORIGINS` 可为空，或为最多8个逗号分隔的不同 HTTPS DNS Origin；仅443端口，无路径、查询、片段、用户信息或通配符。不能重复主域或自身。开启兼容域必须显式提供主域。未知 Host 返回421，缺失/重复/语法错误的 Host 返回400；不会读取 `X-Forwarded-Host` 或 `X-Forwarded-Proto` 来扩展信任。

每个域名拥有独立 `CampaignAPI` 和其 `EventsAPI`，但共用一个 `CampaignStore`、SQLite与单个通知worker。每个请求只选取自己的局部API对象，不修改多线程共享的全局Origin。新域只接受新域Origin，旧域只接受旧域Origin；缺Origin时仍只接受同域Referer，外域/null不能降级回退。原Fetch Metadata、管理员登录和CSRF校验保留。票券与核销URL使用选中实例的Origin；完整票券URL仍按所属Origin校验，不扩大跨域票链接白名单。

新域与旧域浏览器Cookie仍分别属于各自域名。旧Cookie用户在旧域可以改投；新Cookie与旧票同IP只读取既有票、无改投权，不增加票数。跨域又换IP无法自动关联匿名身份，这不是跨域身份迁移。会员签名桥接继续使用主域生成订单URL，公开域名不能改变签名桥接主域。

Nginx应仅将明确配置的域名转发给API，并保留该域的Host以及原Origin、Referer、Sec-Fetch-Site。不要改写Origin，不要为正常请求添加跨域许可。API仍仅监听loopback；两条 `/internal/leanclub/*` POST先经过其既有精确路径、loopback及HMAC/时效/nonce/租户与身份检查，不要求公众Host，因此原Java服务内部调用不受影响。未新增内部API外网代理。

生产loopback健康检查也应明确提供主域Host，例如：

```sh
curl --fail --silent -H 'Host: www.leanschool.net' http://127.0.0.1:5381/api/club/health
```

本地开发仅在主Origin为loopback HTTP、请求peer与Host均为loopback且无兼容域时，保留前端4180→API5381的现有代理行为；没有为生产HTTPS启用localhost例外。

本地预览的 `/check-in` 跳转保留 `LEANCLUB_ADMIN_URL` 中的应用挂载目录。例如 `https://www.leanschool.net/console/login?redirect=/leanclub/content` 跳到 `/console/leanclub/registrations`，不复制请求查询参数，保留no-referrer。原不带挂载目录的本地5173入口照常使用。票券fragment仍由浏览器带入原后台并由后台登录守卫处理。

验证入口：`tests/test_campaign_hosts.py` 包含真实HTTP Host/Origin矩阵、缺失/重复/伪造转发Host、管理员CSRF、并发同IP投票、旧Cookie改投、票券URL域名及签名内部接口；`tests/test_integration_gateway.py` 包含 `/console` 下GET/HEAD核销跳转。测试使用合成数据与临时数据库，不触及运行库或真实通知。
