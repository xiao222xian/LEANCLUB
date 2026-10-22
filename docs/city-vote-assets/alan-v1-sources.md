# Alan v1 人物编辑参考来源

记录日期：2026-10-22（Asia/Shanghai）。本页只记录三张成品编辑时实际使用的参考；不把检索候选全部列为已用素材。

## 主参考：已有主站人物背景

- 文件：[lean-club-sunset-hero-v3.jpg](../../web/creator/lean-club-sunset-hero-v3.jpg)，1672×941；页面使用的响应式 [hero-1672-v2.webp](../../web/creator/images/hero-1672-v2.webp) 为同一画面。
- 用户明确指认该背景人物为邵艾伦。本次沿用用户给出的身份，没有通过面部识别验证身份。
- 角色：人物整体特征、薄肌比例、侧身气质及右臂纹身参考。主站原 Hero 不在本次替换范围。
- 既有素材的原始拍摄来源和授权未在本次核实；已有 Hero 也不应被称为未经编辑的摄影原片。

## 辅助参考：公开账号照片

两张照片均来自公开页面中标为 `@AlanShao111` 的帖子。关联依据是账号、原帖链接、文案和图片位于同一页面节点，不依据面部识别。X 原帖工具读取返回 403；通过[公开聚合页](https://sopilot.net/zh/rank/topic/male-sex-toy-necessity-consumerism-debate)取得对应原帖及媒体 URL，媒体从 `pbs.twimg.com` 直接读取。没有独立验证该公众账号控制人，不能将聚合页关联写成官方身份认证。

| 参考编号 | 实际用途 | 原帖 | 原媒体 | 尺寸 |
| --- | --- | --- | --- | --- |
| x-candidate-22 | 面部三分侧角度、发型与纹身辅助 | [AlanShao111 帖子](https://x.com/AlanShao111/status/2107709961027633247) | [HUAXCfpbMAAW5xF.jpg](https://pbs.twimg.com/media/HUAXCfpbMAAW5xF.jpg?name=orig) | 1368×2048 |
| x-candidate-13 | 镜自拍薄肌躯干与身体比例；手机部分遮脸 | [AlanShao111 帖子](https://x.com/AlanShao111/status/2105447896451879289) | [HTgNswIbsAAKUKG.jpg](https://pbs.twimg.com/media/HTgNswIbsAAKUKG.jpg?name=orig) | 1080×1440 |

公开参考照片未复制到本 Git 仓库。其 SHA-256 与检索记录见 [alan-v1-sources.json](alan-v1-sources.json)。未查明这些照片的商业授权，本记录不表示本人合作、授权或背书。

## 编辑输入顺序与输出性质

每次调用内置 `image_gen` 均按以下顺序传入四张参考图：

1. 对应城市的原 v3 PNG，作为编辑目标。
2. 主站 `lean-club-sunset-hero-v3.jpg`，作为主要人物参考。
3. `x-candidate-22.jpg`，作为补充面部参考。
4. `x-candidate-13.jpg`，作为补充身体参考；这是镜自拍，提示词要求避免纹身方向被错误镜像。

输出为新的 AI 辅助城市人像合成，保持主要城市构图但并非背景像素锁定，也不表示本人实际在三处拍摄。人物黑发仍有挑染，纹身图案与五官还原存在生成差异；相似度和最终视觉采用待用户反馈。完整实际指令见[编辑提示词](source-prompts/alan-v1-prompts.md)。
