# 输出形式与发布门槛

在 `analysis.json` 通过校验后再渲染。三个格式共享同一事实源，标题和篇幅可以变化，主张、风险和证据强度不得变化。

## 选择格式

- `report`：默认。用于决策、复盘和继续追问，保留完整主张账本、冲突与证据索引。
- `xhs-cards`：用于小红书、Instagram 图文轮播、微信公众号卡片等扫读场景。可输出 Markdown 文案、版本化卡片 IR、自包含 HTML 和可选 PNG；不直接声称已发布。
- `short-video`：用于小红书视频、抖音、TikTok、Reels 与 Shorts。输出 60–90 秒分镜、口播、字幕和 `xhs-video/v1` IR，可选渲染无声 MP4。

## 共同结构

1. **价值前置**：标题准确概括问题；第一屏给一句话答案，不用“震惊”“必看”等空洞承诺。
2. **动作前置**：主体保持 3–5 步，每步写动作、适用条件、验证信号和停止条件。
3. **经验分层**：把成功亲历、失败反例、直接建议和风险提醒分开，不混成“网友都说”。
4. **冲突可见**：同时展示主要分歧及造成差异的条件；不按点赞数裁决。高风险冲突先声明“不是操作建议”；含 `unsafe_advice` 的立场标为“未核验高风险观点”，风险提醒方明确标为“风险提醒”。
5. **证据可回钻**：保留评论 ID 和短摘；相关回复只算一个证据组。每条含 `unsafe_advice` 的附录证据必须在独立 `safety_warning` 字段中写“未核验高风险观点，不是操作建议”，由 Markdown、HTML 和 PNG 显式呈现；`category_label` 只表示类别。点赞字段缺失时写“赞数未知”，不得留下无数值的“赞”。
6. **边界透明**：显示采集时间、页面显示量、实际采集量、截断、失败原因、外部复核状态和 AI 辅助披露。使用“页面显示 N 条 · 实际采集 M 条”，避免用 `M/N` 让读者误解分子分母。

## `report`

固定顺序：一句话答案 → 数据范围 → 可执行步骤 → 评论答案与经验 → 主张账本 → 冲突 → 风险与未知 → 排除的候选 → 证据索引。

适合直接回答用户。不要把完整 JSON 粘贴给普通读者；只在调试时附上机器数据。

## `xhs-cards`

主卡数量为 `7 + 方案步骤数`，通常 8–12 张：

1. 封面：具体问题 + 有边界的一句话答案。
2. 数据范围：分析了多少候选和评论，是否截断。
3. 起每个方案步骤独占一张动作卡，同时写证据 ID、适用、验证和停止条件。
4. 动作卡之后依次为亲历个案、失败反例、分歧与风险、待确认、披露与安全互动。
5. 完整评论短摘进入独立证据附录，不算作主卡，也不塞进披露卡。

每张卡只表达一个任务，使用短句与可扫读层级。封面不写无证据的数字、绝对词或保证结果的承诺。

Markdown 和图片都必须从 `xhs-card-deck/v1` IR 生成，不按中文标题猜测卡片边界：

```text
python scripts/render_result.py <canonical.jsonl> <analysis.json> <cards.md> --format xhs-cards --structured-output <cards.json>
python scripts/render_card_images.py <canonical.jsonl> <analysis.json> <output-dir> --style morandi
python scripts/render_card_images.py <canonical.jsonl> <analysis.json> <output-dir> --style morandi --png
```

HTML 始终可生成且不加载远程素材。PNG 后端使用 Playwright 和本机已有 Chromium/Edge/Chrome，逐张输出 1080×1440；真实布局在最小可读缩放仍溢出时必须失败并指出 `card_id`。图片先写入同级临时目录，整套通过后才替换旧输出；中途失败必须保留上一套完整图片。PNG 后端缺失时保留 HTML 与 IR，并明确降级，不自动下载浏览器。

发布前视觉抽检至少覆盖封面、文字最多的动作卡、风险卡和披露卡：标题与页码完整、边距安全、字号可读、证据同屏、合成/高风险警示前置、末卡恰好一个安全问题。不同系统字体可能造成轻微排版差异，不使用跨系统像素哈希代替人工视觉检查。

## `short-video`

Markdown 和 Remotion 项目都必须从 `xhs-video/v1` IR 生成：

```text
python scripts/render_result.py <canonical.jsonl> <analysis.json> <video.md> --format short-video --structured-output <video.json>
npm ci --ignore-scripts --no-audit --no-fund
python scripts/render_video.py <canonical.jsonl> <analysis.json> <output-dir>
python scripts/render_video.py <canonical.jsonl> <analysis.json> <output-dir> --mp4 --browser <chrome-or-edge-path>
npm run video:studio -- --props <output-dir>/<note-id>.props.json
```

固定配置为 1080×1920、30 fps、60–90 秒、H.264、`audio.kind=none`。场景顺序为钩子、范围、每步一个动作、证据、冲突与风险、未知项、披露和安全 CTA；1–5 个步骤分别映射到确定时长，不把多个动作挤进同一场景。字幕只压缩表达，不得删除使结论成立的适用条件。

前三秒钩子优先同时给出 `social_title`、从完整 `summary` 中按语义边界确定性选取的连续短句，以及“继续看 N 步”的观看路径；完整标题与 `summary` 始终保留在 IR 和画面。若标题或安全完整短句无法满足三秒字幕密度，只口播“问题—证据—行动—继续看 N 步”的确定性路径，不截断否定或危险边界，也不编造摘要。CTA 只复制 analysis 语义选择的 `primary_stop_condition`；该字段必须逐字来自某个步骤的 `stop_conditions`，两侧均不得有空白。字段缺失时保留全部停止条件；完整停止文案超过 60 个显示单位时，构建阶段以 `CTA_STOP_CONDITIONS_TOO_LONG` 显式失败，不按关键词排序，也不把溢出推迟到浏览器检测。

引用 `unsafe_advice` 的每个场景必须就地持续显示“未核验高风险观点，不是操作建议”，口播前置同义警告，首条字幕也必须包含警示。短视频描述区再次放数据范围和高风险提示。

当前实现不生成 TTS、配音或音轨；口播字段仅供脚本、字幕和后续人工录音使用。Studio 命令载入构建器生成的 `.props.json`，启动不自动打开浏览器的本地预览服务，不代表已导出或发布。

MP4 渲染仅复用显式 `--browser`、`REMOTION_BROWSER_EXECUTABLE`、`PLAYWRIGHT_CHROMIUM_EXECUTABLE` 或本机已知位置的 Chromium/Edge/Chrome，不自动下载。成品先写入同级临时文件，通过容器与元数据校验后才替换目标；失败必须保留旧 MP4。Remotion 的使用受其[特殊许可](https://www.remotion.dev/docs/license)约束。

## 平台发布前检

`xhs-video/v1` 只证明视频项目自身有效，不代表任一社交平台已经接受素材。发布或投放前，选择一个有适用范围和核对日期的平台 profile，生成确定性的 `xhs-publish-check/v1`：

```text
python scripts/check_publish_profile.py <video.json> --profile youtube_shorts --ai-content-kind assistive_text_only --output <publish-check.json>
python scripts/check_publish_profile.py <video.json> --profile youtube_shorts --ai-content-kind synthetic_visual,synthetic_audio --output <publish-check.json>
```

内置 profile：`xhs_cn`、`douyin_cn`、`tiktok_organic`、`tiktok_ads`、`youtube_shorts`、`instagram_reels`、`instagram_boost`、`cross_platform_master_60`。规则及官方来源保存在 `references/platform-profiles.json`；profile 的适用范围必须与实际发布入口一致。`tiktok_ads` 只代表 Reservation In-Feed Non-Spark/Push 的竖版 9:16 素材，不代表 Spark Pull、Auction Ads 或自然发布。`cross_platform_master_60` 只验证项目母版策略，不证明任何平台已接受素材。

前检按分辨率、比例、帧率、时长、音频策略、AI 披露和平台预览分别给出 `pass`、`needs_review` 或 `blocked`。公开官方来源没有稳定数值时保存 `null`/`unknown` 并产生人工核对项，不使用第三方经验值补空。官方硬限制、官方建议和项目交付门槛分别记录；例如 Instagram Boost 的 `<90 秒`是官方资格边界，而“付费素材需有明确的原声或免版税声音策略”是本项目交付门槛，不声称平台技术上禁止静音。

AI 内容类型必须显式选择为集合：`none`、`assistive_text_only`、`synthetic_visual`、`synthetic_audio`、`realistic_altered`。参数可以重复，也可以用逗号传入；合成画面与合成音频可同时选择并取义务并集。`none` 不得与其他类型组合，`assistive_text_only` 不得与三个合成媒体类型组合。仅使用 AI 辅助脚本、标题或字幕时，不得误选为 YouTube 的逼真修改内容。

中国平台首帧标识采用失败关闭的有限文案。单类分别为“画面由AI生成”（另允许“非真人实拍，画面由AI生成”）、“旁白由AI合成”、“画面经AI修改”；组合类分别为“画面由AI生成，旁白由AI合成”、“画面由AI生成并经AI修改”、“画面经AI修改，旁白由AI合成”、“画面由AI生成并经AI修改，旁白由AI合成”。首帧只能有一条从 0 ms 开始的完整标签字幕，解析出的声明 kind 集合必须与当前 required kinds 完全相等；少报和多报均阻断。两条并列标签、自由文本、疑问句、否定句、反标识或跨媒介标签也不能通过自动门禁。

`needs_review` 表示结构化报告已生成但发布条件尚未闭环，CLI 默认返回 3；只有明确用于收集报告时才可加 `--allow-needs-review` 令其返回 0。`blocked` 返回 1，输入、profile、非标准 JSON 数值或未知字段错误返回 2。所有错误都带 `code` 和 JSON `path`，未知字段默认拒绝。Windows 标准输出固定为 UTF-8。

当前 `xhs-video/v1` 仍只接受 `audio.kind=none`。因此 TikTok Reservation 广告的官方有声硬要求，以及 Instagram Boost 的项目有声门槛，都会在本版本中有意阻断；这不是把静音误判为可投放。未来只有在实际音轨、容器探测和版权证据均可验证后才能放行。

## 90 分发布自检

| 维度 | 分值 | 检查点 |
|---|---:|---|
| 问题契合与直接性 | 10 | 真问题、约束清楚、开头直接回答 |
| 证据可追溯 | 20 | 每个结论有 ID、短摘、独立组和时间范围 |
| 可信度 | 20 | 经验要素、外部复核、操纵风险、热度与真假分离 |
| 可执行性 | 15 | 有序步骤、条件、资源、验证和停止条件 |
| 冲突与风险 | 15 | 双边观点、未知项、高风险和适用边界 |
| 可读性 | 10 | 答案前置、短段、层级、证据索引 |
| 发布包装 | 10 | 标题准确、平台原生语气、无溢出、披露、安全互动、效果验证计划 |

硬门槛失败时不计算分数：未取得评论正文却声称已分析、步骤无有效证据、隐私泄露、数据截断未披露。90 分以上可发布，75–89 分小修，60–74 分必须补证据或重构，低于 60 分不发布。

## 效果评估

把传播效果与事实可信度分开。可比较标题、封面、卡片顺序和时长，但不要改变结论。优先观察读完/观看深度、收藏、分享、有效追问和后续验证反馈；点赞只作为关注信号。

参考：[YouTube 标题与缩略图建议](https://support.google.com/youtube/answer/12340300)、[YouTube A/B 测试](https://support.google.com/youtube/answer/16391400)、[TikTok 创作者建议](https://newsroom.tiktok.com/5-tips-for-tiktok-creators)、[知乎创作者手册](https://www.zhihu.com/knowledge-plan/manual)。
