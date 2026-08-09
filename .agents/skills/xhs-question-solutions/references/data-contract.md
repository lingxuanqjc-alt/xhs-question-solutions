# 数据契约

规范化 JSONL 每行一个对象。笔记行字段：`kind=note`、`note_id`、`url`、`title`、`content`、`author`、`likes`、`comments_count`。评论行字段：`kind=comment`、`comment_id`、`note_id`、`parent_id`、`author`、`content`、`likes`、`created_at`。

```json
{"query":"主题","posts":[{"note_id":"n1","is_question":true,"question":"问题","confidence":0.9,"comments":[{"comment_id":"c1","category":"firsthand_experience","claim":"主张","confidence":0.8}],"solution":{"summary":"摘要","steps":[{"text":"步骤","evidence_comment_ids":["c1"]}],"constraints":["条件"],"conflicts":[{"topic":"冲突","positions":[{"claim":"观点","evidence_comment_ids":["c1"]}]}],"unknowns":["待验证"]}}]}
```

非问题帖可省略 `comments` 和 `solution`。所有证据 ID 必须属于同一笔记。
