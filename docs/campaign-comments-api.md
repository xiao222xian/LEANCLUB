# 投票页公开评论接口

更新：2026-10-23（Asia/Shanghai）。本文记录本地源码与隔离测试；不代表生产已发布。

## 数据与展示边界

- 新增 `campaign_comments` 表，和只给运营看的 `campaign_feedback` 分开。旧意见不会自动成为评论或弹幕。
- 每次提交必须显式携带 `publishConsent: true`，表示同意在公开评论区与弹幕展示。
- 评论首次提交后为 `visible`。原芋道运营人员可改为 `hidden`，公开列表立即排除；前端轮询后同步撤下。恢复为 `visible` 后重新参与公开列表。
- 正文作为普通文字存储。客户端必须用 `textContent` 等文本方式渲染，不能作为 HTML。
- 不生成或存入虚构用户评论。装饰性互动文案由前端明确标为官方话题，不计入真实评论数。
- 不向飞书发送新增评论，不改原私密意见通知配置。

## 公开读取

`GET /api/campaign/comments?limit=20&cursor=<nextCursor>`

`limit` 默认 20，范围 1–50；`cursor` 可省略，传入上次 `nextCursor` 原值。仅接受这两个查询参数，重复参数或无效值返回 400。

```json
{
  "items": [{
    "id": "32位小写十六进制标识",
    "content": "合成示例评论",
    "nickname": "薄肌同好",
    "city": null,
    "createdAt": "2026-10-23T12:00:00Z"
  }],
  "total": 1,
  "hasMore": false,
  "nextCursor": null
}
```

按创建时间、ID 倒序，游标在边界评论被隐藏后仍可续读。`total` 是当前全部可见评论数。GET 不创建访客身份、不设置 Cookie；响应 `Cache-Control: no-store`。查询限频每 IP 每分钟 180 次。

## 提交

`POST /api/campaign/comments`，同源 JSON：

```json
{
  "requestId": "018d179b-c2af-4c58-a9df-81338172de57",
  "content": "合成示例：想参加周末公园训练。",
  "nickname": "合成同学",
  "city": "shenzhen",
  "publishConsent": true
}
```

- `content` 为 2–240 字；允许普通换行、Emoji，拒绝控制字符与不可见定向字符。
- `nickname` 可省略或留空，默认“薄肌同好”；最多 20 字，不允许换行。
- `city` 可省略/null，或 `shenzhen`、`zhuhai`、`guangzhou`。
- `requestId` 为 UUID。同一访客、同一 UUID、相同内容重试复用原评论；改内容重试返回 409。
- 复用投票的 HttpOnly 访客 Cookie。只有提交事务成功后才为新访客下发身份；校验/限频失败不遗留新访客。

首次返回 201，幂等重试返回 200：

```json
{
  "ok": true,
  "item": {
    "id": "32位小写十六进制标识",
    "content": "合成示例评论",
    "nickname": "薄肌同好",
    "city": null,
    "createdAt": "2026-10-23T12:00:00Z",
    "status": "visible"
  },
  "duplicate": false
}
```

原评论已被隐藏时，重试返回 `item.status: hidden`，不重新公开。客户端据此提示并移除该条，不把返回内容重新塞回弹幕。

成功提交配额：每访客 10 分钟 3 条/每日 10 条；每 IP 10 分钟 10 条/每日 30 条。并发提交在事务内核验；Cookie 轮换仍受 IP 配额约束。另有限频每 IP 每分钟 60 次 POST 请求。IP 采用现有私密密钥 HMAC，不存明文。

错误格式为 `{"error":"可展示的错误说明"}`，状态码 400 输入、403 来源、409 幂等冲突、413 超长请求、415 非 JSON、429 限频。

## 原芋道运营桥接

既有私有签名接口增加两个固定动作：

| 动作 | 参数 | 返回 |
| --- | --- | --- |
| `comments-list` | `{status: "all"或"visible"或"hidden", page: 1}`，两项可省略 | `{items,total,page,pageSize:20,totalPages}` |
| `comments-status` | `{id,status:"visible"或"hidden"}` | `{item}` |

运营 item 为公开 item 加 `status`；不返回访客哈希、IP、requestId，不包含 `unread`。继续使用原 HMAC、loopback、租户与员工身份、时效/nonce 防重放和只记操作元数据的审计。Java 权限由原芋道调用侧检查，未新增浏览器端管理认证入口。

## 隔离验证

用项目 `.venv/Scripts/python.exe` 运行 `tests/test_campaign_comments.py`：13 项通过，覆盖公开同意、字段校验、私密意见隔离、游客身份原子性、幂等/并发/配额、隐藏恢复、游标分页、HTTP 同源、JSON/plain-text/no-store。

`tests/test_operations_bridge.py`：12 项通过，含新增两项公开评论管理与签名边界。旧 `test_campaign_feedback.py` 20 项、`test_campaign_origins.py` 13 项、`test_campaign_ip.py` 12 项通过。均为临时数据库与合成数据；未写运行库、未操作生产、未发通知。

本地重载前由主任务备份运行 SQLite；`CampaignStore` 初始化将幂等创建新表与两个索引，不迁移或覆盖旧意见。回退代码前保留新表，避免丢失评论。
