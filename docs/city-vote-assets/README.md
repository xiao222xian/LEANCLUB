# 三城投票素材记录

记录日期：2026-10-22（Asia/Shanghai）。用户先确认采用深圳、珠海、广州三张站姿图，随后要求以主站已有邵艾伦人物图为主要参考替换人物、保留城市背景。当前版本为 `alan-v1`；原 v3 保留为历史素材。新版已接入本地页面，人物相似度与最终审美仍待用户反馈。

运行时图片位于 `web/creator/city-vote/assets/`，本目录不重复保存大图。当前版本使用 [alan-v1-manifest.json](alan-v1-manifest.json) 与 [alan-v1-SHA256SUMS.txt](alan-v1-SHA256SUMS.txt)；原 v3、字体和矢量标记使用历史 [manifest.json](manifest.json) 与 [SHA256SUMS.txt](SHA256SUMS.txt)。

## 当前版本：Alan 人物参考合成

内置 `image_gen` 以三张 v3 海报为编辑目标，以用户明确指认为邵艾伦的主站背景人物作为主参考，另使用两张公开账号照片辅助面部与身体比例。主站原始 Hero 保持原样。新版是 AI 辅助合成概念影像，不能表述为本人在深圳、珠海、广州的现场实拍或已开展活动的记录。

| 城市 | 保留的城市视觉 | 当前网站文件 |
| --- | --- | --- |
| 深圳 | 日光海湾、华润大厦、石台与长裤站姿构图 | [941px](../../web/creator/city-vote/assets/alan-v1/shenzhen.webp) · [640px](../../web/creator/city-vote/assets/alan-v1/shenzhen-640.webp) |
| 珠海 | 蓝色海面、日月贝、短裤静止站姿构图 | [941px](../../web/creator/city-vote/assets/alan-v1/zhuhai.webp) · [640px](../../web/creator/city-vote/assets/alan-v1/zhuhai-640.webp) |
| 广州 | 蓝调暮色、广州塔、临江栏杆与倚靠站姿构图 | [941px](../../web/creator/city-vote/assets/alan-v1/guangzhou.webp) · [640px](../../web/creator/city-vote/assets/alan-v1/guangzhou-640.webp) |

三张编辑输出均为 941×1672 PNG；通过现有 CMS 海报管线校验、清除元数据并编码为 941×1672 和 640×1137 WebP，Pillow 12.3.0、`quality=90`、`method=6`、Lanczos 缩放，不放大。静态版本与本次 CMS 对应文件字节一致，新克隆运行不依赖本机数据库或私有媒体目录。

实际保留了原城市的主要构图、地标、时间氛围和站姿，但不声称背景像素完全一致；人物发色仍有挑染，纹身和面部不是参考照片的精准复制。编辑提示词中的“unchanged”“faithfully”等词是生成目标，不是已验证达到的事实。

- [完整实际编辑提示词](source-prompts/alan-v1-prompts.md)：三城逐条保存，完整文字不变，仅将 CRLF 换行为 LF；清单同时保留源文件字节数和摘要。
- [参考来源与核验边界](alan-v1-sources.md)：主站素材路径、两张实际使用的公开照片原帖和媒体地址。
- [结构化来源](alan-v1-sources.json)：本地主图与实际使用辅助照片的尺寸、摘要和身份关联依据。

外部参考照片仅用于本次编辑参考，没有复制到 Git；原始 PNG 留在设计工作区，尺寸与摘要已归档。来源记录不构成照片授权、本人合作或背书证明。

## 历史版本：v3 海报来源与采用范围

三张海报由内置 `image_gen` 在 2026-10-22 生成，主体是虚构的成年东亚男性薄肌站姿人像。它们是品牌概念影像，不是实际活动记录、真实会员照片或活动场地证明；建筑造型和机位也不是实景测绘结果。未下载商业摄影作为成品海报。

| 城市 | 历史视觉 | 保留的网站文件 |
| --- | --- | --- |
| 深圳 | 冷色日光、黑长裤、一手搭台一手入袋，城市天际线 | [941px](../../web/creator/city-vote/assets/shenzhen.webp) · [640px](../../web/creator/city-vote/assets/shenzhen-640.webp) |
| 珠海 | 银亮海面、双贝建筑、黑短裤、侧向静止站姿 | [941px](../../web/creator/city-vote/assets/zhuhai.webp) · [640px](../../web/creator/city-vote/assets/zhuhai-640.webp) |
| 广州 | 蓝调夜色、近机闪光、搭栏站姿，广州塔靠右 | [941px](../../web/creator/city-vote/assets/guangzhou.webp) · [640px](../../web/creator/city-vote/assets/guangzhou-640.webp) |

原始 PNG 实际均为 941×1672，并非 4K。原件保存在本工作区相邻设计目录 `../lmgua-city-vote/backgrounds-v3/`，没有再复制进仓库。其内容哈希同时记录在清单中，仓库内运行不依赖这些外部原件。

## 历史 v3 完整实际提示词

以下三个文件保留原始记录的完整文字，包含生成、用户纠正和编辑过程。深圳、广州记录仅清理文件末尾的多余空行，未改提示词文字；珠海记录保持原字节。归档文件使用 LF 换行，规范化前后的字节数与 SHA-256 均记录在清单中：

- [深圳生成记录](source-prompts/shenzhen-prompt.md)：当前采用“用户更正后的最终站姿重生成”；初始跑步稿及其肤质编辑段落只属于历史。
- [珠海生成记录](source-prompts/zhuhai-prompt.md)：当前采用完整站姿生成提示词，无后续图像编辑。
- [广州生成记录](source-prompts/guangzhou-prompt.md)：当前采用“用户更正后：站姿薄肌时装人像”，再进行“小标识清理”；俯卧撑段落只记录早期场景／光线参考。

原记录中的“等待用户审图”等状态和原工具输出绝对路径反映生成时点，并不覆盖本页的最新采用状态，也不是网页运行依赖。原生成指令中请求的比例、构图和分辨率属于意图；真实输出以本页尺寸、文件和清单为准。

## 历史 v3 网页转换

从已选 PNG 转换为 WebP，未重新生图、裁切、调色、磨皮、锐化或放大；页面的 `object-fit` 裁切由 CSS 和后台焦点值控制。广州塔靠右，更换图像或焦点后仍需要复核桌面与手机构图。

- 完整版：941×1672；移动候选：640×1137，按原比例缩小。
- 历史转换工具记录：sharp 0.35.5、libvips 8.18.7、WebP 1.6.0。
- 编码：`quality: 92, effort: 6, smartSubsample: true`；缩放使用 Lanczos3，不放大。
- 本次整合直接复制已核对的六个 WebP；转换来源为相邻 `lmgua-city-vote/fullscreen-vote/assets/`，逐文件 SHA-256 一致。
- 管理后台后来上传的图片属于运行数据，保存在 `CLUB_MEDIA_DIR`，不属于本静态清单。服务端解码、清理元数据并重新编码后，通过 `/api/media/` 提供。

## 字体与字标

投票页英文字体为 Space Grotesk，文件 [sans.ttf](../../web/creator/city-vote/assets/fonts/sans.ttf)，对应 [SIL Open Font License 1.1](../../web/creator/city-vote/assets/fonts/SpaceGrotesk-OFL.txt) 完整许可文字随网站文件保留。许可证文本仅删除第 21 行末尾的一个空格并将 CRLF 换行规范为 LF，文字含义未改；清单保留原始字节摘要。版权属于 Space Grotesk Project Authors，作者项目地址记录于许可证：[floriankarsten/space-grotesk](https://github.com/floriankarsten/space-grotesk)。

依原素材记录，该字体于 2026-10-22 从 [Google Fonts 官方仓库](https://github.com/google/fonts/tree/main/ofl/spacegrotesk) 下载，先用于设计原型；字体二进制 `sans.ttf` 逐字节复制进当前仓库，未作修改，许可文本的空白规范化如上。下载时上游 `main` 未锁定 commit，因此当前文件以 SHA-256 固定，不能把可变分支 URL 当作精确版本。中文使用系统字体，不另分发中文字体文件。

2026-10-22 视觉重修新增原创代码矢量 LC 几何标记 `lean-emblem.svg`，用于投票页、主页活动入口与轻量管理台；仍为本次设计提案，不宣称注册商标或正式 VI 已获认可。另有代码生成的静态 SVG 胶片颗粒 `film-grain.svg`，只用于轻微纹理叠加。未下载第三方标志。原 v3 文件保持不变，后续人物替换另存为 `alan-v1`；页面遮罩、饱和度和焦点由 CSS 控制。

## 视觉参考边界

[TOKEN2049](https://token2049.com/) 作为全屏影像分栏与少量文字层级的设计参考。没有复制该站照片、Logo、字形资源或页面代码；三城海报为上述独立生成素材。参考与素材采用不等于公开部署，本次仍仅本地开发和验证。
