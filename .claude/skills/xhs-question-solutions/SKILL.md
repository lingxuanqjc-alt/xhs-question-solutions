---
name: xhs-question-solutions
description: 筛选小红书真实问题帖，分类评论答案、亲历、反例、风险与猜测，并输出带原评论证据的报告、HTML/PNG 图文卡片、静音视频或用户确认的外部 WAV 配音项目。
---

# Claude Code 兼容入口

读取并严格遵循 `../../../.agents/skills/xhs-question-solutions/SKILL.md`。脚本和 references 路径均相对于该核心 Skill 目录解析；不要在此复制或分叉核心流程。

不得自行添加 `--confirm-audio-reviewed` 或 `--confirm-audio-rights`。只有用户已逐段听审并明确确认所声明的音频使用权时，才能把用户提供的外部 WAV 构建为 `xhs-video/v2`；本项目不提供 TTS，也不读取 API Key。
