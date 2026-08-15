#!/usr/bin/env python3
"""Deterministically render a validated canonical analysis as Markdown."""
import argparse, json, re
from datetime import datetime
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
UNSAFE_EVIDENCE_WARNING = "未核验高风险观点，不是操作建议"


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
    coverage = f"页面显示 {total} 条 · 实际采集 {got} 条" if total else f"实际采集 {got} 条 · 页面总量未知"
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


def _likes_label(value):
    return "赞数未知" if value is None or value == "" else f"赞 {value}"


def _evidence_warning(classification):
    flags = classification.get("risk_flags", [])
    return UNSAFE_EVIDENCE_WARNING if classification.get("category") == "unsafe_advice" or "unsafe_advice" in flags else ""


def _evidence_lines(note_id, canonical_comments, classified):
    lines = []
    for raw in canonical_comments:
        if raw.get("note_id") != note_id: continue
        classification = classified.get(raw["comment_id"], {})
        category = classification.get("category")
        warning = _evidence_warning(classification)
        warning_part = f"｜警示：{warning}" if warning else ""
        lines.append(f"- `{raw['comment_id']}`｜{CATEGORY_ZH.get(category, '未分类')}｜{raw['author']}｜{_likes_label(raw.get('likes'))}｜thread `{raw['thread_id']}`{warning_part}｜{_short(raw['content'])}")
    return lines


def _conflict_lines(conflicts):
    lines = []
    for conflict in conflicts:
        lines.append(f"- **{conflict.get('topic', '未命名分歧')}**")
        lines.extend(f"  - {p['claim']}（证据：{', '.join(p['evidence_comment_ids'])}）" for p in conflict.get("positions", []))
    return lines or ["- 无已识别冲突"]


def _reader_time(value):
    if not value: return "时间未知"
    try: return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M")
    except ValueError: return _short(value, 24)


def _reader_source(value):
    return {"synthetic_fixture": "合成演示数据", "browser": "公开页面采集", "export": "用户提供的导出数据", "unknown": "来源未知"}.get(value, "其他已授权来源")


def _reader_failure(value):
    return {"reached_limit": "达到采集上限", "rate_limited": "采集频率受限", "login_required": "需要登录后继续", "timeout": "采集超时"}.get(value, "采集未完整完成")


def _safety_cta(unknowns):
    raw = _clean(unknowns[0] if unknowns else "关键现场条件和停止边界")
    raw = raw.replace("？", "").replace("?", "").strip("。！；;，, ")
    raw = re.sub(r"(?:尚)?(?:未提供|未知|待确认|不清楚|未确认|未核实)$", "", raw).strip("。！；;，, ")
    subject = raw or "关键现场条件和停止边界"
    return f"关于「{subject}」，你目前能确认哪一项？"


def _card(card_id, index, role, title, blocks, evidence_ids=()):
    return {"card_id": card_id, "index": index, "role": role, "title": title,
            "blocks": blocks, "evidence_comment_ids": list(evidence_ids)}


def build_card_decks(canonical, analysis):
    """Build the stable machine contract used by Markdown and image renderers."""
    errors = validate(canonical, analysis)
    if errors: raise ValueError("analysis validation failed:\n" + "\n".join(errors))
    notes = {row["note_id"]: row for row in canonical if row.get("kind") == "note"}
    canonical_comments = [row for row in canonical if row.get("kind") == "comment"]
    question_posts = [post for post in analysis["posts"] if post["is_question"]]
    excluded_count = sum(not post["is_question"] for post in analysis["posts"])
    decks = []
    for post in question_posts:
        note, solution = notes[post["note_id"]], post["solution"]
        capture = note.get("capture", {})
        classified = {item["comment_id"]: item for item in post["comments"]}
        groups = {}
        for item in post["comments"]: groups.setdefault(item["category"], []).append(item)
        total, collected = capture.get("comments_total", 0), capture.get("comments_collected", 0)
        coverage = f"页面显示 {total} 条 · 实际采集 {collected} 条" if total else f"实际采集 {collected} 条 · 页面总量未知"
        cards, note_id = [], post["note_id"]

        notices = []
        if capture.get("source") == "synthetic_fixture": notices.append("合成演示")
        if solution["risk_level"] == "high" and solution["publish_status"] == "needs_review": notices.extend(("高风险", "发布前人工复核"))
        cover_blocks = [{"type": "notice", "tone": "warning", "text": " · ".join(notices)}] if notices else []
        cover_blocks.append({"type": "paragraph", "text": solution["summary"]})
        cards.append(_card(f"{note_id}:01", 1, "cover", post.get("social_title", post["question"]), cover_blocks))

        scope_blocks = [
            {"type": "paragraph", "text": f"本次纳入 {len(analysis['posts'])} 篇候选，其中 {len(question_posts)} 篇问题帖，排除 {excluded_count} 篇。"},
            {"type": "field", "label": "数据来源", "value": _reader_source(capture.get("source"))},
            {"type": "field", "label": "采集时间", "value": _reader_time(capture.get("captured_at"))},
            {"type": "field", "label": "评论范围", "value": coverage},
            {"type": "paragraph", "text": "点赞只表示关注，不代表真实。"},
        ]
        if capture.get("is_truncated"):
            scope_blocks.append({"type": "notice", "tone": "caution", "text": f"评论未完整采集：{_reader_failure(capture.get('failure_reason'))}。"})
        cards.append(_card(f"{note_id}:02", 2, "scope", "数据范围", scope_blocks))

        for step_number, step in enumerate(solution["steps"], 1):
            index = len(cards) + 1
            blocks = [{"type": "paragraph", "text": step["text"]},
                      {"type": "field", "label": "证据", "value": "、".join(step["evidence_comment_ids"])},
                      {"type": "field", "label": "适用", "value": _items(step["applies_when"])},
                      {"type": "field", "label": "验证", "value": step["verification"]},
                      {"type": "field", "label": "停止", "value": _items(step["stop_conditions"])}]
            cards.append(_card(f"{note_id}:{index:02d}", index, "action", f"第 {step_number} 步", blocks, step["evidence_comment_ids"]))

        experiences = groups.get("firsthand_experience", [])
        experience_title = "一个亲历个案" if len(experiences) == 1 else ("亲历经验" if experiences else "暂无亲历个案")
        experience_blocks = [{"type": "bullet", "text": f"{item['claim']}【{item['comment_id']}】"} for item in experiences]
        if not experience_blocks: experience_blocks = [{"type": "paragraph", "text": "当前样本没有可用的亲历个案。"}]
        elif len(experiences) == 1: experience_blocks.append({"type": "notice", "tone": "caution", "text": "仅为一个评论个案，不可外推为普遍结果。"})
        elif len(experiences) <= 2: experience_blocks.append({"type": "notice", "tone": "caution", "text": "仅为少量评论个案，需对照场景与适用条件。"})
        index = len(cards) + 1
        cards.append(_card(f"{note_id}:{index:02d}", index, "experience", experience_title, experience_blocks, [item["comment_id"] for item in experiences]))

        counterexamples = groups.get("counterexample", [])
        counter_blocks = [{"type": "bullet", "text": f"{item['claim']}【{item['comment_id']}】"} for item in counterexamples]
        if not counter_blocks: counter_blocks = [{"type": "paragraph", "text": "当前样本没有明确失败反例。"}]
        elif len(counterexamples) == 1: counter_blocks.append({"type": "notice", "tone": "caution", "text": "单个失败反例只提示风险，需对照场景与条件。"})
        elif len(counterexamples) <= 2: counter_blocks.append({"type": "notice", "tone": "caution", "text": "少量失败反例不能证明所有方案都会失败。"})
        index = len(cards) + 1
        cards.append(_card(f"{note_id}:{index:02d}", index, "counterexample", "失败反例", counter_blocks, [item["comment_id"] for item in counterexamples]))

        conflict_ids, conflict_blocks = [], []
        if solution["risk_level"] == "high":
            conflict_blocks.append({"type": "notice", "tone": "warning", "text": "以下为评论中的冲突观点，不是操作建议；高风险内容待权威复核"})
        for conflict in solution.get("conflicts", []):
            conflict_blocks.append({"type": "paragraph", "text": conflict.get("topic", "未命名分歧")})
            for position in conflict.get("positions", []):
                ids = position["evidence_comment_ids"]; conflict_ids.extend(ids)
                evidence_items = [classified[cid] for cid in ids]
                if any("unsafe_advice" in item["risk_flags"] for item in evidence_items): prefix = "未核验高风险观点："
                elif any(item["category"] == "risk_warning" for item in evidence_items): prefix = "风险提醒："
                else: prefix = "观点："
                conflict_blocks.append({"type": "bullet", "text": f"{prefix}{position['claim']}（证据：{'、'.join(ids)}）"})
        if not solution.get("conflicts"): conflict_blocks.append({"type": "paragraph", "text": "当前样本没有足够证据形成明确冲突。"})
        conflict_blocks.append({"type": "field", "label": "风险等级", "value": _zh(solution["risk_level"], RISK_LEVEL_ZH)})
        index = len(cards) + 1
        cards.append(_card(f"{note_id}:{index:02d}", index, "conflicts_risks", "分歧与风险", conflict_blocks, conflict_ids))

        unknown_blocks = [{"type": "bullet", "text": value} for value in solution.get("unknowns", [])]
        if not unknown_blocks: unknown_blocks = [{"type": "paragraph", "text": "当前没有额外待确认项。"}]
        index = len(cards) + 1
        cards.append(_card(f"{note_id}:{index:02d}", index, "unknowns", "待确认", unknown_blocks))

        index = len(cards) + 1
        disclosure_blocks = [
            {"type": "paragraph", "text": f"{coverage}；发布状态：{_zh(solution['publish_status'], PUBLISH_STATUS_ZH)}。"},
            {"type": "paragraph", "text": "内容由 AI 辅助整理；经验不等于事实；利益关系：未知，发布前人工确认。"},
            {"type": "notice", "tone": "cta", "text": _safety_cta(solution.get("unknowns", []))},
        ]
        cards.append(_card(f"{note_id}:{index:02d}", index, "disclosure", "发布前确认", disclosure_blocks))

        evidence = []
        for raw in canonical_comments:
            if raw.get("note_id") != note_id: continue
            item = classified[raw["comment_id"]]
            warning = _evidence_warning(item)
            category_label = CATEGORY_ZH.get(item["category"], item["category"])
            evidence_item = {"comment_id": raw["comment_id"], "category": item["category"],
                             "category_label": category_label, "author": raw["author"],
                             "likes": raw.get("likes"), "likes_label": _likes_label(raw.get("likes")),
                             "thread_id": raw["thread_id"], "excerpt": _short(raw["content"])}
            if warning: evidence_item["safety_warning"] = warning
            evidence.append(evidence_item)
        decks.append({"deck_id": f"note:{note_id}", "note_id": note_id,
                      "meta": {"candidate_count": len(analysis["posts"]), "question_count": len(question_posts),
                               "excluded_count": excluded_count, "source": capture.get("source", "unknown"),
                               "captured_at": capture.get("captured_at", ""), "comments_total": total,
                               "comments_collected": collected, "is_truncated": bool(capture.get("is_truncated")),
                               "failure_reason": capture.get("failure_reason", ""), "risk_level": solution["risk_level"],
                               "publish_status": solution["publish_status"], "ai_assisted": True,
                               "interest_disclosure": "unknown_requires_manual_confirmation"},
                      "cards": cards, "appendix": {"evidence": evidence}})
    return {"schema": "xhs-card-deck/v1", "decks": decks}


def render_card_decks_markdown(card_decks):
    if not isinstance(card_decks, dict) or card_decks.get("schema") != "xhs-card-deck/v1":
        raise ValueError("unsupported card deck schema")
    lines = ["# 小红书图文卡片脚本"]
    multiple = len(card_decks.get("decks", [])) > 1
    for deck_number, deck in enumerate(card_decks.get("decks", []), 1):
        if multiple: lines.append(f"\n# 卡组 {deck_number}｜{deck['note_id']}")
        for card in deck["cards"]:
            lines.append(f"\n## 卡片 {card['index']}｜{card['title']}")
            for block in card["blocks"]:
                kind = block["type"]
                if kind == "paragraph": lines.append(block["text"])
                elif kind == "field": lines.append(f"- **{block['label']}：** {block['value']}")
                elif kind == "bullet": lines.append(f"- {block['text']}")
                elif kind == "notice":
                    label = {"cta": "安全提示", "caution": "边界提示", "warning": "重要披露"}.get(block.get("tone"), "提示")
                    lines.append(f"> **{label}：** {block['text']}")
                else: raise ValueError(f"unsupported card block type: {kind}")
    lines.append("\n# 附录｜完整证据索引")
    for deck in card_decks.get("decks", []):
        if len(card_decks["decks"]) > 1: lines.append(f"\n## {deck['note_id']}")
        for item in deck["appendix"]["evidence"]:
            likes_label = item.get("likes_label", _likes_label(item.get("likes")))
            warning = f"｜警示：{item['safety_warning']}" if item.get("safety_warning") else ""
            lines.append(f"- `{item['comment_id']}`｜{item['category_label']}｜{item['author']}｜{likes_label}｜thread `{item['thread_id']}`{warning}｜{item['excerpt']}")
    return "\n".join(lines).replace("。；", "；").replace("。。", "。").rstrip() + "\n"


def serialize_card_decks_json(card_decks):
    if card_decks.get("schema") != "xhs-card-deck/v1": raise ValueError("unsupported card deck schema")
    return json.dumps(card_decks, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def render(canonical, analysis, format_name="report"):
    if format_name == "xhs-cards":
        return render_card_decks_markdown(build_card_decks(canonical, analysis))
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
    parser.add_argument("--structured-output", type=Path, help="optional xhs-card-deck/v1 JSON sidecar")
    args = parser.parse_args(); canonical = load_jsonl(args.canonical); analysis = json.loads(args.analysis.read_text(encoding="utf-8-sig"))
    if args.structured_output and args.format != "xhs-cards": parser.error("--structured-output requires --format xhs-cards")
    if args.format == "xhs-cards":
        card_decks = build_card_decks(canonical, analysis); content = render_card_decks_markdown(card_decks)
        if args.structured_output:
            args.structured_output.parent.mkdir(parents=True, exist_ok=True)
            args.structured_output.write_text(serialize_card_decks_json(card_decks), encoding="utf-8")
    else: content = render(canonical, analysis, args.format)
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(content, encoding="utf-8")
    print(f"rendered {args.format}: {args.output}")


if __name__ == "__main__": main()
