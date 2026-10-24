# 薄肌设计引擎：图形、字标与组合标

> 历史方案：用户已否定此轮视觉；当前直接复用原双字母页面，见 README.md。以下不描述当前发布入口。

2026-10-24，第三轮实现。上一轮把所有长名称分组叠加，几何验证通过但用户指出辨识度和视觉效果不足。本轮保留原几何能力，以真实字形轮廓与局部改造扩展长名称。两个引擎在统一的 DesignVariant 接口下使用相同预览、构造、配色与导出链路。

## 分发与输入

`src/engine/index.ts` 导出 initializeEngine(fontBaseUrl?)、generateVariants(input)、generateVariant(input,id)、resolveComposition(input) 和 getVariantOptions()。

- text：1–32个 A–Z/a–z，大小写保留。不支持字符明确拒绝。
- composition：auto / monogram / wordmark / signature；auto 以2字符为界。
- weight14–38、overlap24–68、tension0–100、spacing−20–80、seed整数0–1000000。不同引擎映射见下文。
- markText：组合标可选1–4个字母；空值自动选择。focusIndex：字标中可选的实际字符索引。
- 结果包含最终paths、fillRule、viewBox、bounds、placements、guides、statistics，并补充layout、detail、modifiedIndices和signature来源信息。长名称采用动态横向viewBox，不固定320×200。

## 图形标

legacy目录直接继承固定 SiliRootLogoDesign@677e8f8f1a3d7128d8ab831cae99d7e00ac567ce 的直线/真实圆弧、解析求交与区域边界。双大写且spacing=0逐路径和构造数据精确匹配144组独立上游配置。新增26个小写几何字形，2–4字母进行共形组合，超过4字母分组；全部字符参与。用户可显式使用，但长名称默认不走此路线。

保留六种关系、原参数单位、15秒五阶段和实际圆/边/接点。新字形和新组合数值退化处理只在扩展层，不放宽原版等价基准。源文件边界见 [ORIGIN.md](src/engine/legacy/ORIGIN.md)。历史长名称几何耗时只反映该路线，不能作为新字标性能数字。

## 字标与组合标

wordmark.ts 用 opentype.js 读取同源 Space Grotesk 300/400/500/600/700 五个静态字重母版；weight映射到最近母版，并非连续变量轴。排布使用真实advance/kerning，spacing改变字距；SVG保留M/L/Q/C曲线。为避开opentype2数值序列化的不兼容，直接序列化路径命令并检查有限数值。

六个稳定ID仍为weave/fusion/counter/axis/embrace/tension，在字标中分别为桥接连字、共笔衔接、负形刻口、轴线断点、内缘圆切、斜切端点。Paper.js布尔计算只处理所选1–2个字母，其他字母保持相同位置和轮廓。负形设计保留至少90%原字形墨面积，避免过度侵蚀；这是形状约束，不是审美或识别率保证。

overlap影响局部连接深度，tension影响切口；seed可重现局部关系。focusIndex选择实际目标；双字操作使用该字符与下一字，末字取前一对。候选局部预览裁切真实最终paths，完整名称视图可切回。

组合标将原几何生成的独立缩写图形与完整字标排列；LeanSchool自动取LS，其余名称优先CamelCase首字母，否则首两字母。markText可自定义1–4字母。字标焦点调整不改变独立缩写。输出只有路径，导出不依赖接收者安装字体。

## Worker 与错误恢复

Vite ?worker&inline 将计算程序内联到入口包。任务使用Worker身份、epoch和requestId隔离；取消被替代计算、复用空闲已初始化Worker、130ms防抖、15秒超时。过期消息、异常和定时器不允许破坏新任务。

内联Worker使用blob URL，字体由页面计算同源绝对 /design/fonts/ URL传入。字体初始化失败清除失败Promise，重试重新加载。失败保留上次可用预览，期间禁用导出，区别加载/计算/超时错误；不再覆盖整张作品为失败遮罩。

用户截图SRRR、24/66/57/32在纯几何重现及2304参数扫描均未复现失败。已修复生命周期和部署后旧Worker哈希路径风险，但未捕获用户现场错误堆栈，不能断言这是那次事故唯一根因。已打开的旧实现须刷新一次。

## 实验室、应用与验证

固定SR实验室按需读取原参考图和几何JSON，保留三步实体形状、18补圆/18切圆、10直线及33指定平滑接点；计算切线角误差，不使用上游固定误差文案。它不是任意输入的参考图重建器。

名片/海报与普通导出使用同一最终作品路径，海报SVG内嵌官网照片。61组测试涵盖上述算法和错误恢复；另验证144原等价、原234回归、2304截图参数、桌面/390px和真实SVG/PNG下载。历史新增12,168默认几何组合通过仅对几何路线成立。

主包约686KB/gzip232KB，内联Worker代价已记录，Vite体积告警保留。未做实体手机性能或生产验收。没有审美评分、中文shaping、可变字宽轴、字体上传或动画视频；HarfBuzz/fontTools/skia-pathops/Coldtype仅为已核查的后续路线，详见工作区研究报告。
