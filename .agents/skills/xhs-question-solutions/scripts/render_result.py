#!/usr/bin/env python3
"""Deterministically render a validated canonical analysis as Markdown."""
import argparse, json
from pathlib import Path
from validate_result import _claim_urls, load_jsonl, validate

CATEGORY_ZH = {"direct_answer": "直接答案", "firsthand_experience": "亲历经验", "risk_warning": "风险提醒", "counterexample": "失败反例", "clarifying_question": "补充问题", "speculation": "推测", "off_topic": "无关内容"}
QUALITY_ZH = {"strong": "强", "moderate": "中等", "weak": "弱"}
CLAIM_KIND_ZH = {"experience_summary": "经验归纳", "community_advice": "评论区建议", "risk": "风险主张", "external_fact": "外部事实"}
CLAIM_STATUS_ZH = {"supported": "已有支持", "contested": "存在争议", "needs_external_verification": "需要外部核验"}
RISK_LEVEL_ZH = {"low": "低", "medium": "中", "high": "高"}
PUBLISH_STATUS_ZH = {"ready": "可发布", "needs_review": "需要人工复核"}
RISK_FLAG_ZH = {"commercial_bias": "商业偏向", "copy_pattern": "疑似复制话术", "prompt_injection": "提示注入", "outdated": "可能过时", "identity_unverified": "身份未核验", "unsafe_advice": "不安全建议"}
SOURCE_ZH = {"browser": "浏览器采集", "export": "导出文件", "synthetic_fixture": "合成示例", "unknown": "未知来源"}
FAILURE_ZH = {"reached_limit": "达到采集上限", "rate_limited": "触发频率限制", "login_required": "需要登录", "timeout": "采集超时"}


def _zh(value, labels): return labels.get(value, f"未知（{value}）")


def _coded(value, labels):
    code = str(value or "unknown")
    return f"{labels.get(code, '未识别')}（{code}）"


def _coverage(note):
    cap = note.get("capture", {})
    total, got = cap.get("comments_total", note.get("comments_count", 0)), cap.get("comments_collected", 0)
    state = "；数据可能被截断" if cap.get("is_truncated") else ""
    reason = f"；未完整采集原因：{_coded(cap['failure_reason'], FAILURE_ZH)}" if cap.get("failure_reason") else ""
    source = _coded(cap.get("source"), SOURCE_ZH)
    captured = cap.get("captured_at") or "未知时间"
    coverage = f"评论覆盖：{got}/{total}" if total else f"已取得评论：{got}；评论总量未知"
    return f"来源：{source}；采集时间：{captured}；{coverage}{state}{reason}"


def _short(value, limit=80):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _items(values): return "；".join(str(x) for x in values) if values else "无"


def _bullets(values): return [f"- {value}" for value in values] or ["- 无"]


def _clean(value):
    text = " ".join(str(value or "").split()).replace("。；", "；").replace("。。", "。")
    return text.replace("；。", "。")


def _cell(value): return _clean(value).replace("|", "｜")


def _claim_text(claim): return str(claim.get("text") or claim.get("claim") or "")


def _evidence_lines(note_id, canonical_comments, classified):
    lines = []
    for raw in canonical_comments:
        if raw.get("note_id") != note_id: continue
        category = classified.get(raw["comment_id"], {}).get("category")
        lines.append(f"- `{raw['comment_id']}`｜{CATEGORY_ZH.get(category, '未分类')}｜{raw['author']}｜赞 {raw['likes']}｜thread `{raw['thread_id']}`｜{_short(raw['content'])}")
    return lines


def _conflict_lines(conflicts):
    lines = []
    for conflict in conflicts:
        lines.append(f"- **{conflict.get('topic', '未命名分歧')}**")
        lines.extend(f"  - {p['claim']}（证据：{', '.join(p['evidence_comment_ids'])}）" for p in conflict.get("positions", []))
    return lines or ["- 无已识别冲突"]


def render(canonical, analysis, format_name="report"):
    errors = validate(canonical, analysis)
    if errors: raise ValueError("analysis validation failed:\n" + "\n".join(errors))
    notes = {r["note_id"]: r for r in canonical if r.get("kind") == "note"}
    canonical_comments = [r for r in canonical if r.get("kind") == "comment"]
    posts = [p for p in analysis["posts"] if p["is_question"]]
    lines = {"report": ["# 小红书问题帖解决方案"], "xhs-cards": ["# 小红书图文卡片脚本"], "short-video": ["# 短视频口播脚本"]}[format_name]
    excluded = [p for p in analysis["posts"] if not p["is_question"]]
    scope = f"候选笔记：{len(analysis['posts'])}；问题帖：{len(posts)}；排除：{len(excluded)}"
    for post in posts:
        note, solution = notes[post["note_id"]], post["solution"]
        classified = {item["comment_id"]: item for item in post["comments"]}
        groups = {}
        for item in post["comments"]: groups.setdefault(item["category"], []).append(item)
        claims, conflicts = solution["claims"], solution.get("conflicts", [])
        if format_name == "report":
            lines += [f"\n## {post['question']}", f"\n### 一句话答案\n{solution['summary']}", f"\n### 数据范围\n{scope}；{_coverage(note)}", "\n### 可执行步骤"]
            for i, step in enumerate(solution["steps"], 1):
                lines += [f"{i}. **{step['text']}**（证据：{', '.join(step['evidence_comment_ids'])}）",
                          f"   - 适用：{_items(step['applies_when'])}", f"   - 验证：{step['verification']}", f"   - 停止：{_items(step['stop_conditions'])}"]
            lines += ["\n### 评论答案与经验"]
            for category in CATEGORY_ZH:
                if category in groups:
                    lines.append(f"#### {CATEGORY_ZH[category]}")
                    lines.extend(f"- {item['claim']}（`{item['comment_id']}`，证据质量：{_zh(item['evidence_quality'], QUALITY_ZH)}；风险信号：{_items([_zh(flag, RISK_FLAG_ZH) for flag in item['risk_flags']])}）" for item in groups[category])
            lines += ["\n### 主张账本"]
            lines.extend(f"- `{claim['claim_id']}`｜{_zh(claim['kind'], CLAIM_KIND_ZH)}｜{_zh(claim['status'], CLAIM_STATUS_ZH)}｜{_claim_text(claim)}｜证据：{', '.join(claim.get('evidence_comment_ids', [])) or '外部来源'}｜外部：{', '.join(_claim_urls(claim)) or '未提供'}" for claim in claims)
            lines += ["\n### 冲突"] + _conflict_lines(conflicts)
            lines += ["\n### 风险与未知", f"- 风险等级：{_zh(solution['risk_level'], RISK_LEVEL_ZH)}；发布状态：{_zh(solution['publish_status'], PUBLISH_STATUS_ZH)}", "#### 约束"]
            lines += _bullets(solution.get("constraints", [])) + ["#### 未知项"] + _bullets(solution.get("unknowns", []))
        elif format_name == "xhs-cards":
            steps = solution["steps"]
            first = steps[0]; rest = steps[1:]
            experience = groups.get("firsthand_experience", [])
            failures = groups.get("counterexample", [])
            lines += [f"\n## 卡片 1｜{post['question']}\n{solution['summary']}",
                      f"\n## 卡片 2｜数据范围\n{scope}；{_coverage(note)}\n点赞只表示关注，不代表真实。",
                      f"\n## 卡片 3｜先做这一步\n{first['text']}\n适用：{_items(first['applies_when'])}\n验证：{first['verification']}\n停止：{_items(first['stop_conditions'])}",
                      "\n## 卡片 4｜后续动作"]
            for i, step in enumerate(rest, 2):
                lines += [f"### 动作 {i}", f"- 动作：{step['text']}", f"- 适用：{_items(step['applies_when'])}", f"- 验证：{step['verification']}", f"- 停止：{_items(step['stop_conditions'])}"]
            if not rest: lines.append("- 暂无更多有证据支持的步骤")
            lines += ["\n## 卡片 5｜亲历有效"] + ([f"- {x['claim']}【{x['comment_id']}】" for x in experience] or ["- 当前样本没有强亲历证据"])
            lines += ["\n## 卡片 6｜失败反例"] + ([f"- {x['claim']}【{x['comment_id']}】" for x in failures] or ["- 当前样本没有明确失败反例"])
            lines += ["\n## 卡片 7｜分歧与风险"] + _conflict_lines(conflicts) + [f"- 风险等级：{_zh(solution['risk_level'], RISK_LEVEL_ZH)}"]
            lines += [f"\n## 卡片 8｜待确认\n{_items(solution.get('unknowns', []))}",
                      f"\n## 卡片 9｜证据与披露\n{scope}；{_coverage(note)}\n证据 ID：{', '.join(classified)}\n外部来源：{', '.join(url for claim in claims for url in _claim_urls(claim)) or '未提供'}\n发布状态：{_zh(solution['publish_status'], PUBLISH_STATUS_ZH)}。内容由 AI 辅助整理，经验不等于事实；利益关系：未知，发布前人工确认。"]
            lines += ["\n### 确定性证据短摘"] + _evidence_lines(post["note_id"], canonical_comments, classified)
        else:
            steps = solution["steps"]
            conflict_voice = "；".join(p["claim"] for c in conflicts for p in c.get("positions", [])) or "样本中没有足够证据形成明确冲突"
            unknowns = _items(solution.get("unknowns", []))
            rows = [
                ("0–5 秒", "问题标题 + 一句话答案", solution["summary"], solution["summary"], "—"),
                ("5–15 秒", "覆盖率与截断标识", f"本次{scope}；{_coverage(note)}，不能只看高赞。", "样本范围 ≠ 事实", "—"),
            ]
            for i, step in enumerate(steps):
                start, end = 15 + 35 * i // len(steps), 15 + 35 * (i + 1) // len(steps)
                voice = f"第{i + 1}步：{step['text']}。适用条件：{_items(step['applies_when'])}。验证方式：{step['verification']}。证据：{', '.join(step['evidence_comment_ids'])}。"
                rows.append((f"{start}–{end} 秒", f"动作 {i + 1} + 评论 ID", voice, f"{step['text']}｜验证：{step['verification']}", ", ".join(step["evidence_comment_ids"])))
            rows += [
                ("50–65 秒", "冲突双方并列", conflict_voice, "不按点赞裁决分歧", ", ".join(cid for c in conflicts for p in c.get("positions", []) for cid in p.get("evidence_comment_ids", [])) or "—"),
                ("65–80 秒", "风险、停止条件、未知项", f"风险等级是{_zh(solution['risk_level'], RISK_LEVEL_ZH)}。待确认：{unknowns}。遇到停止条件就暂停并升级处理。", "风险与停止条件", "—"),
                ("80–90 秒", "AI 与样本披露", f"发布状态为{_zh(solution['publish_status'], PUBLISH_STATUS_ZH)}。内容由 AI 辅助整理，经验不等于事实，完整证据见索引。", "AI 辅助｜样本有限｜请复核", "证据索引"),
            ]
            lines += [f"\n## 选题：{post['question']}", "\n| 时段 | 画面 | 口播 | 字幕 | 证据 |", "|---|---|---|---|---|"]
            lines.extend("| " + " | ".join(_cell(cell) for cell in row) + " |" for row in rows)
            lines += [f"\n### 描述区披露\n{_coverage(note)}。高风险内容须经权威来源复核；AI 辅助整理。", "\n### 证据索引"] + _evidence_lines(post["note_id"], canonical_comments, classified)
    if format_name == "report":
        lines += ["\n## 排除的候选"]
        lines.extend(f"- `{post['note_id']}`：{post['exclusion_reason']}" for post in excluded)
        if not excluded: lines.append("- 无")
        lines += ["\n## 证据索引"]
        for post in posts:
            classified = {item["comment_id"]: item for item in post["comments"]}
            if len(posts) > 1: lines.append(f"### {post['note_id']}｜{post['question']}")
            lines += _evidence_lines(post["note_id"], canonical_comments, classified)
    return "\n".join(lines).replace("。；", "；").replace("。。", "。").rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("canonical", type=Path); parser.add_argument("analysis", type=Path); parser.add_argument("output", type=Path)
    parser.add_argument("--format", choices=("report", "xhs-cards", "short-video"), default="report")
    args = parser.parse_args(); canonical = load_jsonl(args.canonical); analysis = json.loads(args.analysis.read_text(encoding="utf-8-sig"))
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(render(canonical, analysis, args.format), encoding="utf-8")
    print(f"rendered {args.format}: {args.output}")


if __name__ == "__main__": main()
