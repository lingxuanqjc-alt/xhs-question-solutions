# xhs-question-solutions

面向 Codex 与 Claude Code 的小红书问题帖答案提炼 Skill：筛选真实问题帖，将评论区的直接答案、亲历经验、风险提醒、反例和猜测分开整理，再生成带评论证据的可执行方案。

> English: A cross-agent skill that turns Xiaohongshu question posts and comment evidence into traceable, actionable solutions.

## 能做什么

- 判断笔记是否真的在提问，而不只依赖问号或标题。
- 区分直接答案、亲历经验、风险提醒、反例、追问、猜测和无关内容。
- 表面化共识、冲突、适用条件和信息缺口。
- 要求每个方案步骤引用同一笔记下的评论 ID。
- 使用确定性脚本规范化 JSON/JSONL，并拒绝缺失或串帖的证据引用。

## 快速使用

克隆后从项目根目录启动 Agent，即可使用项目级 Skill。

Codex：

```text
$xhs-question-solutions 分析“小户型墙面发霉”相关的问题帖，并从评论中提炼有证据的解决方案。
```

Claude Code：

```text
/xhs-question-solutions 分析这些小红书问题帖和评论。
```

也可以安装为个人 Skill：

```powershell
.\installers\install.ps1
```

```bash
bash installers/install.sh
```

目标目录已存在时，安装器默认停止。显式传入 `-Force` 或 `--force` 会先把旧目录移动为带时间戳的备份，再安装新版本。

## 输入方式

按优先级支持：

1. 当前环境可用的小红书专用 Agent、Skill 或已登录浏览器。
2. 用户有权访问的笔记和公开可见评论。
3. 用户提供的 JSON、JSONL 或少量内联文本。

示例见 [examples/sample-input.json](examples/sample-input.json)。仅有链接但无法读取评论时，Skill 会明确要求导出数据，不会假装已经分析评论。

## 输出内容

1. 发帖者真正的问题。
2. 带评论 ID 和适用条件的执行步骤。
3. 直接答案与亲历经验。
4. 共识、冲突、失败案例和风险。
5. 待验证信息与匿名化证据索引。

示例见 [examples/sample-output.md](examples/sample-output.md)。

## 验证数据

```powershell
python .agents/skills/xhs-question-solutions/scripts/normalize_xhs_export.py examples/sample-input.json build/sample.jsonl
python .agents/skills/xhs-question-solutions/scripts/validate_result.py build/sample.jsonl examples/sample-analysis.json
python -m unittest discover -s .agents/skills/xhs-question-solutions/tests -v
```

## 隐私与边界

- 只处理公开内容或用户有权访问的数据。
- 不绕过登录、风控或访问限制，不调用未经验证的私有评论接口。
- 不要提交 `cookies.json`、`.env`、真实用户导出或未经脱敏的评论。
- 点赞数只表示关注度，不等于真实性。
- 医疗、法律、金融和人身安全建议需要权威来源复核。

## 项目结构

```text
.agents/skills/xhs-question-solutions/   Codex 核心 Skill
.claude/skills/xhs-question-solutions/   Claude Code 项目入口
examples/                                脱敏输入、分析和输出
installers/                              Windows 与 macOS/Linux 安装器
demo/                                    竖屏演示视频脚本
```

## 致谢

笔记正文获取方式参考 [chenxiachan/xhs-claude-skills](https://github.com/chenxiachan/xhs-claude-skills)。该项目覆盖 Cookie + HTTP 的笔记正文提取；本项目没有把它描述为评论接口。

## 许可证

[MIT](LICENSE)
