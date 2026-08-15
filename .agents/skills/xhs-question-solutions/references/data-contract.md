# 数据契约

## Canonical JSONL

每行一个对象。原始导出必须另行保留；canonical 只保存帖内稳定匿名代号。

笔记行：

```json
{"kind":"note","note_id":"n1","url":"https://...","title":"标题","content":"正文","author":"用户-a1b2c3d4","likes":12,"comments_count":30,"capture":{"source":"browser","captured_at":"2026-08-15T10:00:00+08:00","comments_total":30,"comments_collected":18,"is_truncated":true,"failure_reason":"reached_limit"}}
```

评论行：

```json
{"kind":"comment","comment_id":"c1","note_id":"n1","parent_id":null,"thread_id":"c1","author":"用户-a1b2c3d4","content":"原评论正文","likes":4,"created_at":"2026-08-15T09:00:00+08:00"}
```

- `thread_id` 指向回复树的根评论；同一 `thread_id` 默认只算一个独立证据组。
- `comments_total` 表示来源显示的总量，`comments_collected` 表示实际取得量。两者不可比较时也要明确 `is_truncated` 和失败原因。
- 没有来源值时使用空字符串或 `0`，不要猜测时间、作者或互动量。

## Analysis JSON

每篇 canonical 笔记必须在 `posts` 中恰好出现一次；每条已取得评论也必须恰好分类一次。

```json
{
  "query": "主题",
  "posts": [
    {
      "note_id": "n1",
      "is_question": true,
      "question": "发帖者真正要解决的问题",
      "social_title": "墙面反复发霉怎么办？",
      "question_type": "how_to",
      "confidence": 0.92,
      "comments": [
        {
          "comment_id": "c1",
          "category": "firsthand_experience",
          "claim": "在什么条件下做了什么并得到什么结果",
          "confidence": 0.82,
          "evidence_quality": "strong",
          "risk_flags": []
        }
      ],
      "solution": {
        "summary": "一句话答案",
        "primary_stop_condition": "何时停止或升级处理",
        "risk_level": "medium",
        "publish_status": "needs_review",
        "claims": [
          {
            "claim_id": "claim-1",
            "kind": "experience_summary",
            "text": "限定条件后的主张",
            "status": "supported",
            "evidence_comment_ids": ["c1"],
            "external_sources": []
          }
        ],
        "steps": [
          {
            "text": "动作",
            "claim_ids": ["claim-1"],
            "evidence_comment_ids": ["c1"],
            "applies_when": ["适用条件"],
            "verification": "如何判断有效",
            "stop_conditions": ["何时停止或升级处理"]
          }
        ],
        "constraints": ["不能外推的边界"],
        "conflicts": [
          {
            "topic": "冲突主题",
            "positions": [
              {"claim":"观点 A","evidence_comment_ids":["c1"]},
              {"claim":"观点 B","evidence_comment_ids":["c2"]}
            ]
          }
        ],
        "unknowns": ["待验证"]
      }
    },
    {
      "note_id": "n2",
      "is_question": false,
      "confidence": 0.96,
      "exclusion_reason": "教程标题中的反问是互动钩子，正文没有求助意图"
    }
  ]
}
```

## 枚举

`social_title` 是可选的社交短标题，用于卡片封面和视频前三秒钩子，不替代报告、选题与证据语境中的 `question`。提供时必须是单行字符串，包含 8–28 个非空白可见字符；不得使用“震惊”“必看”“百分百”“根治”“保证”等无证据承诺。缺失时回退为 `question`。

- `question_type`：`how_to`、`choice`、`diagnosis`、`recommendation`、`experience_request`、`fact_check`、`other`
- `category`：`direct_answer`、`firsthand_experience`、`risk_warning`、`counterexample`、`clarifying_question`、`speculation`、`off_topic`
- `evidence_quality`：`strong`、`moderate`、`weak`
- `risk_flags`：`commercial_bias`、`copy_pattern`、`prompt_injection`、`outdated`、`identity_unverified`、`unsafe_advice`
- `kind`：`experience_summary`、`community_advice`、`risk`、`external_fact`
- `status`：`supported`、`contested`、`needs_external_verification`
- `risk_level`：`low`、`medium`、`high`
- `publish_status`：`ready`、`needs_review`

`external_sources` 是放在 `external_fact` 主张中的 URL 字符串列表。高风险结果要标为 `ready` 时，所有外部事实主张都必须为 `supported` 且带有效 URL。所有评论 ID 必须属于当前笔记；所有步骤评论 ID 必须被其 `claim_ids` 对应的主张覆盖。

`solution.primary_stop_condition` 是可选的语义选择字段，供短视频 CTA 突出一个主停止边界；提供时必须逐字等于某个 `steps[*].stop_conditions[*]`。`primary_stop_condition` 与每个 `stop_conditions` 条目都禁止首尾空白，避免分析校验与渲染复制采用不同字符串。该选择由分析阶段完成，渲染代码只复制，不按关键词猜测“最安全”的条件。缺失时视频 CTA 保留全部停止条件；完整停止文案超过 60 个显示单位时，构建器必须提前以 `CTA_STOP_CONDITIONS_TOO_LONG` 失败，不能等浏览器版式检测。显式主条件生成的文案超限时对应 `CTA_PRIMARY_STOP_TOO_LONG`。

## Video IR: `xhs-video/v1`

短视频 Markdown、Remotion Studio 与 MP4 共用确定性视频 IR：

```json
{
  "schema": "xhs-video/v1",
  "videos": [
    {
      "video_id": "note:demo-mold-001",
      "note_id": "demo-mold-001",
      "profile": "xhs-vertical-1080x1920-v1",
      "width": 1080,
      "height": 1920,
      "fps": 30,
      "duration_ms": 75000,
      "duration_in_frames": 2250,
      "unsafe_evidence_comment_ids": [],
      "meta": {"audio": {"kind": "none"}},
      "scenes": [],
      "appendix": {"evidence": []}
    }
  ]
}
```

- `videos` 每项对应一个确认的问题帖；仅接受 1–5 个方案步骤，总时长按步骤数确定为 60–90 秒。
- `scenes` 顺序固定为 `hook`、`scope`、逐步 `action`、`evidence`、`conflict_risk`、`risk_unknowns`、`disclosure`、`cta`。每个字幕含 `text`、`startMs`、`endMs`、`timestampMs` 与 `confidence`，按顺序拼接后必须与该场景口播一致。
- 引用了 `unsafe_advice` 证据的场景必须携带 `unsafe_unverified_not_advice` 警示，并让视觉、口播与首条字幕同场呈现。
- `unsafe_evidence_comment_ids` 是 Python 从已校验评论 `risk_flags` 生成的必填安全清单。Node 必须以此清单核对附录固定警示及每个引用场景，不得从可选展示文案反推危险性。
- `appendix` 复用规范化评论的证据字段，不让模型复制或改写证据索引。
- 当前渲染配置为 1080×1920、30 fps、H.264、无音轨；`meta.audio.kind` 必须为 `none`，不表示已生成 TTS 或配音。
- 发布前检对视频 IR 使用严格标量类型：`width`、`height`、`fps`、`duration_ms`、`duration_in_frames`、场景 `index/start_ms/end_ms` 和字幕时间必须为 JSON 整数且不能是布尔值；视频、笔记、场景和证据 ID 必须是非空字符串。`1080.0` 即使数值等于 `1080` 也属于无效契约。

## Publish Check: `xhs-publish-check/v1`

发布前检必须提供一个精确的 `profile_id` 和 `ai_content_kinds` 集合。集合按 `none`、`assistive_text_only`、`synthetic_visual`、`synthetic_audio`、`realistic_altered` 的稳定顺序输出；`none` 与其他类型互斥，`assistive_text_only` 与三个合成媒体类型互斥，多个合成媒体类型可以并存并取披露义务并集。

每个 profile 来源都记录 `authority`、`evidence_status`、`checked_at` 与 `applies_to`。`evidence_status` 只允许 `supports`、`no_public_value`、`conflicting`、`project_policy`；公开官方资料没有数值时必须保留 `unknown/manual`。`cross_platform_master_60` 是项目母版策略，不是平台发布资格。

来源身份不仅按大厂域名判断；每个 `profile_id + source_id` 都绑定明确的 HTTPS 主机、完整页面路径和 query 策略。路径必须精确相等；抖音用户协议必须且只能携带 `id=6773906068725565448`，YouTube 帮助页只允许零个或一个非空 `hl` 参数，其他当前内置来源默认要求空 query。相邻数字、附加子路径、`-user` 后缀、未知或额外 query、其他平台官方页面、同域随机用户路径或未登记 source ID 都必须拒绝。

当前视频 IR 只能证明静音素材，所以所有要求实际音轨的 `hard` 或 `project_gate` 都必须阻断。`platform_setting`、目标账号预览和未知官方数值只能产生 `needs_review`，不能伪造为已完成。

`actual.platform_disclosure_required` 是三态值：任一已知义务为真时是 `true`；没有已知真值但仍有待判定义务时是 `null`；全部明确无需时才是 `false`。`actual.determination_pending` 单独说明义务集合中是否仍含待判定项。

中国首帧 AI 标签从一条完整 canonical 字幕解析为 `first_frame_declared_kinds`；`expected_first_frame_labels` 给出当前 kind 组合允许的有限标签。声明集合必须与 required kinds 完全相等；少报、多报以及两条同时从 0 ms 开始的标签字幕都会失败关闭。
