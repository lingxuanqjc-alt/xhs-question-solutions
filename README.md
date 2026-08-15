# xhs-question-solutions

[![tests](https://github.com/lingxuanqjc-alt/xhs-question-solutions/actions/workflows/test.yml/badge.svg)](https://github.com/lingxuanqjc-alt/xhs-question-solutions/actions/workflows/test.yml)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

面向 Codex 与 Claude Code 的小红书问题帖解决方案 Skill：先找出真正求助的笔记，再把评论里的答案、亲历、反例、风险、猜测与营销信号拆开，最后生成每一步都能回到原评论的方案。

> English: A cross-agent skill that turns Xiaohongshu question posts and comment evidence into traceable, actionable solutions.

![由示例证据确定性生成的小红书卡片封面](examples/sample-cards/demo-mold-001-21e64ed3/01-cover.png)

## 它解决的不是“总结”，而是“能不能信、怎么执行”

| 普通评论总结 | xhs-question-solutions |
|---|---|
| 问号多就当问题帖 | 判断作者是否真的在求答案、诊断、选择或亲历反馈 |
| 高赞和重复说法优先 | 热度与真假分离；回复树和复制话术不重复计票 |
| 把网友经验写成事实 | 分开 UGC 个案、社区建议、风险主张与外部事实 |
| 给出顺滑但无出处的方案 | 每一步绑定主张 ID、评论 ID、适用条件与停止条件 |
| 隐藏冲突和缺失数据 | 显示反例、分歧、采集覆盖、截断和待验证项 |

## 30 秒体验

从项目根目录启动 Agent，然后调用：

Codex：

```text
$xhs-question-solutions 分析这些候选笔记：筛出真实问题帖，把评论中的答案、亲历、反例、风险和猜测分开，并给我一份可执行、可追溯的中文方案。
```

Claude Code：

```text
/xhs-question-solutions 分析这些小红书候选笔记和评论，输出证据报告和小红书卡片稿。
```

完整虚构演示：

- [原始候选笔记](examples/sample-input.json)
- [结构化分析](examples/sample-analysis.json)
- [证据报告](examples/sample-report.md)
- [小红书卡片稿](examples/sample-xhs-cards.md)
- [实际渲染的 1080×1440 卡片（10 张主卡 + 3 张证据附录）](examples/sample-cards/demo-mold-001-21e64ed3/)
- [短视频分镜](examples/sample-short-video.md)
- [可复现的视频 IR、Remotion props 与 1080×1920 预览帧](examples/sample-video/)
- [75 秒无声 H.264 演示成片（v0.4.0 Release）](https://github.com/lingxuanqjc-alt/xhs-question-solutions/releases/download/v0.4.0/xhs-question-solutions-v0.4.0-demo.mp4)

## 三层可信链路

```mermaid
flowchart LR
    A["候选笔记与评论"] --> B["确定性规范化\n匿名化·去重·回复树·覆盖率"]
    B --> C["Agent 语义判断\n问题帖·评论类别·主张账本"]
    C --> D["确定性校验\n完整分类·同帖引用·高风险门槛"]
    D --> E["版本化卡片 / 视频 IR\n同一事实源·不解析标题"]
    E --> F["读者版输出\n报告·HTML/PNG·分镜/MP4"]
```

模型只负责需要判断的部分：问题识别、分类、主张提取和摘要。ID 路由、去重、完整性、证据引用和渲染由标准库脚本处理。

## 输入来源

按优先级使用：

1. 当前环境可用的小红书专用 Agent、Skill 或连接器。
2. 用户已登录浏览器中的公开可见内容。
3. 用户提供的 JSON、JSONL 或少量内联文本。

每次采集同时记录来源、时间、页面显示评论数、实际取得数、是否截断和失败原因。只有链接却无法读取评论正文时，Skill 会请求导出数据，不会假装分析了评论区。

当前版本不内置依赖私有接口或固定 DOM 选择器的抓取器。这样可以避免把易失效、未经验证或需要绕过风控的逻辑包装成稳定能力；已有合规连接器或浏览器环境时，Skill 会优先使用它们。

## 输出形式

- `report`：一句话答案、3–5 步方案、主张账本、冲突、未知项和完整证据索引。
- `xhs-cards`：通常 8–12 张图文卡片；每个方案步骤独占一张，完整证据放独立附录。可生成 Markdown、结构化 IR、自包含 HTML 和可选 PNG。
- `short-video`：从同一证据源确定性生成 60–90 秒分镜、口播、字幕和 `xhs-video/v1` IR；可选渲染 1080×1920、30 fps 的无声 H.264 MP4。

标题、封面和节奏可以针对平台调整，但所有格式共享同一个已校验的 `analysis.json`，不会为了传播效果修改证据结论。

## 安装

先克隆项目：

```text
git clone https://github.com/lingxuanqjc-alt/xhs-question-solutions.git
cd xhs-question-solutions
```

Windows：

```powershell
.\installers\install.ps1
```

macOS / Linux：

```bash
bash installers/install.sh
```

安装器把核心流程放入 `.agents/skills/`，Claude Code 入口只引用这份核心，避免双副本漂移。目标已存在时默认停止；显式传入 `-Force` 或 `--force` 会先创建带时间戳的备份。

## 本地验证

```powershell
python -X utf8 .agents/skills/xhs-question-solutions/scripts/normalize_xhs_export.py examples/sample-input.json build/sample.jsonl
python -X utf8 .agents/skills/xhs-question-solutions/scripts/validate_result.py build/sample.jsonl examples/sample-analysis.json
python -X utf8 .agents/skills/xhs-question-solutions/scripts/render_result.py build/sample.jsonl examples/sample-analysis.json build/report.md --format report
python -X utf8 .agents/skills/xhs-question-solutions/scripts/render_result.py build/sample.jsonl examples/sample-analysis.json build/short-video.md --format short-video --structured-output build/xhs-video.json
python -X utf8 .agents/skills/xhs-question-solutions/scripts/render_card_images.py build/sample.jsonl examples/sample-analysis.json build/cards --style morandi
python -X utf8 -m unittest discover -s tests -v
```

GitHub Actions 会在 Windows 与 Ubuntu 上运行测试，并实际验证两个安装器。

生成 PNG 是显式可选能力。先确认 Node.js、Playwright 以及 Chromium、Edge 或 Chrome 已存在，再运行：

```powershell
python -X utf8 .agents/skills/xhs-question-solutions/scripts/render_card_images.py build/sample.jsonl examples/sample-analysis.json build/cards --style morandi --png
```

如果只缺少 Node 模块，可由你明确决定后安装仓库内锁定版本：

```powershell
npm ci --prefix .agents/skills/xhs-question-solutions --ignore-scripts --no-audit --no-fund
```

脚本不会自动安装浏览器或依赖。缺少 PNG 后端时，自包含 HTML 与 `xhs-card-deck/v1` IR 仍会保留；不能据此声称 PNG 已生成。PNG 输出包含主卡和分页证据附录。截图阶段逐张测量真实布局，任何卡片在最小可读缩放仍溢出都会返回对应 `card_id`，不会静默裁字；新图片全部通过后才替换上一套，失败不会留下半套成品。

视频项目与 MP4 使用同一组锁定的 Node.js 依赖。先运行上面的 `npm ci`，再按需执行：

```powershell
python -X utf8 .agents/skills/xhs-question-solutions/scripts/render_video.py build/sample.jsonl examples/sample-analysis.json build/video
python -X utf8 .agents/skills/xhs-question-solutions/scripts/render_video.py build/sample.jsonl examples/sample-analysis.json build/video --mp4 --browser "C:\Program Files\Google\Chrome\Application\chrome.exe"
npm --prefix .agents/skills/xhs-question-solutions run video:studio -- --props "<absolute-path-to-props.json>"
```

第一条生成 `xhs-video/v1` 项目、Markdown 分镜和各视频 `.props.json`；`--mp4` 才调用 Remotion。浏览器可由 `--browser`、`REMOTION_BROWSER_EXECUTABLE` 指定，或复用系统中已知位置的 Chromium、Edge、Chrome；脚本不会下载浏览器。输出固定为 1080×1920、30 fps、60–90 秒、H.264 无声视频。Studio 以 `--no-open` 启动并打印本地地址；将占位符替换为生成的 props 绝对路径，它只用于预览，不会发布内容。当前管线没有 TTS 或配音合成，`audio.kind=none`，口播文本仅作为脚本和字幕来源。MP4 通过临时文件完成渲染与校验后才替换目标；失败时旧 MP4 保持不变。

## 隐私、真实性与安全边界

- 只处理公开内容或用户有权访问的数据；不绕过登录、验证码、风控或访问限制。
- 评论是“不可信数据”，其中要求 Agent 执行命令、访问链接或忽略规则的文字不会被执行。
- 规范化数据使用帖内稳定匿名代号；不要提交 Cookie、Token、真实导出或未脱敏评论。
- 点赞只表示关注度；相关回复、复制话术和转述不算多个独立来源。
- 医疗、法律、金融、人身安全及化学品操作默认高风险；没有权威复核时不能标为可发布。
- 视频场景引用 `unsafe_advice` 证据时，必须在同一场景持续显示“未核验高风险观点，不是操作建议”，且口播与首条字幕同步警示。
- 社交发布稿应披露 AI 辅助、样本范围、截断和利益关系。

## 项目结构

```text
.agents/skills/xhs-question-solutions/   Agent Skill、脚本、卡片样式与按需 references
.claude/skills/xhs-question-solutions/   Claude Code 兼容入口
tests/                                   意图驱动的确定性测试
examples/                                完全虚构的输入、分析、文本输出和实际 PNG 卡片
installers/                              Windows 与 macOS/Linux 安装器
demo/                                    项目发布视频脚本
```

## 参考与致谢

笔记正文获取思路参考 [chenxiachan/xhs-claude-skills](https://github.com/chenxiachan/xhs-claude-skills)。它覆盖 Cookie + HTTP 的笔记正文、图片和视频处理；本项目没有把它描述成评论正文接口。

内容结构参考平台公开创作建议：[YouTube](https://support.google.com/youtube/answer/12340300)、[TikTok](https://newsroom.tiktok.com/5-tips-for-tiktok-creators)、[知乎创作者手册](https://www.zhihu.com/knowledge-plan/manual)。平台建议只用于提升可读性，不参与证据真假判断。

## 许可证

本项目自有代码采用 [MIT](LICENSE)。可选视频渲染依赖 Remotion，其使用受 [Remotion 特殊许可](https://www.remotion.dev/docs/license)约束；详见 [第三方声明](THIRD_PARTY_NOTICES.md)。
