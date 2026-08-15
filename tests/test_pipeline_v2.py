import json, subprocess, sys, tempfile, unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / ".agents/skills/xhs-question-solutions/scripts"
sys.path.insert(0, str(SCRIPTS))
from normalize_xhs_export import normalize, number
from validate_result import validate
from render_result import build_card_decks, render, render_card_decks_markdown, serialize_card_decks_json


def canonical(two=False):
    notes = [{"id": "n1", "author": {"id": "u0"}, "comments_count": 2, "comments": [
        {"id": "c1", "author": {"id": "u1"}, "content": "先重启", "likes": "1.2万", "replies": [{"id": "c2", "author": {"id": "u1"}, "content": "亲测"}]}]}]
    if two: notes.append({"id": "n2", "comments": []})
    return normalize({"source": "export", "captured_at": "2026-08-15", "data": {"notes": notes}})


def good(two=False):
    post = {"note_id": "n1", "is_question": True, "question": "怎么修", "question_type": "how_to", "confidence": .9,
            "comments": [
                {"comment_id": "c1", "category": "direct_answer", "claim": "重启", "confidence": .8, "evidence_quality": "moderate", "risk_flags": []},
                {"comment_id": "c2", "category": "firsthand_experience", "claim": "有效", "confidence": .7, "evidence_quality": "weak", "risk_flags": []}],
            "solution": {"summary": "先重启并观察", "risk_level": "low", "publish_status": "ready",
                         "claims": [{"claim_id": "cl1", "kind": "community_advice", "status": "supported", "text": "重启可能有效", "evidence_comment_ids": ["c1", "c2"], "external_sources": []}],
                         "steps": [{"text": "重启", "claim_ids": ["cl1"], "evidence_comment_ids": ["c1"], "applies_when": ["可安全重启"], "verification": "故障消失", "stop_conditions": ["无法启动"]}],
                         "constraints": [], "conflicts": [], "unknowns": []}}
    posts = [post]
    if two: posts.append({"note_id": "n2", "is_question": False, "confidence": .9, "exclusion_reason": "分享帖"})
    return {"posts": posts}


class NormalizeTests(unittest.TestCase):
    def test_short_numbers_preserve_attention_not_truth(self): self.assertEqual(12000, number("1.2万"))
    def test_nested_reply_keeps_root_thread(self):
        rows = canonical(); reply = next(x for x in rows if x.get("comment_id") == "c2"); self.assertEqual(("c1", "c1"), (reply["parent_id"], reply["thread_id"]))
    def test_authors_are_stable_and_anonymous_within_post(self):
        rows = canonical(); authors = [x["author"] for x in rows if x.get("comment_id") in {"c1", "c2"}]; self.assertEqual(authors[0], authors[1]); self.assertNotIn("u1", authors[0])
    def test_capture_exposes_coverage(self):
        cap = canonical()[0]["capture"]; self.assertEqual(("export", 2, 2, False), (cap["source"], cap["comments_total"], cap["comments_collected"], cap["is_truncated"]))
    def test_conflicting_duplicate_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "conflicting"): normalize({"id": "n1", "comments": [{"id": "c", "content": "a"}, {"id": "c", "content": "b"}]})
    def test_loose_unknown_note_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown"): normalize([{"id": "n1"}, {"kind": "comment", "note_id": "missing", "id": "c"}])
    def test_invalid_flat_parent_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid parent"): normalize([{"id": "n1"}, {"kind": "comment", "note_id": "n1", "id": "c", "parent_id": "x"}])
    def test_flat_reply_thread_is_recomputed_to_actual_root(self):
        rows = normalize([{"id": "n1"}, {"kind": "comment", "note_id": "n1", "id": "c1"}, {"kind": "reply", "note_id": "n1", "id": "c2", "parent_id": "c1"}, {"kind": "reply", "note_id": "n1", "id": "c3", "parent_id": "c2"}])
        self.assertEqual("c1", next(x for x in rows if x.get("comment_id") == "c3")["thread_id"])
    def test_duplicate_author_conflict_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "conflicting"): normalize({"id": "n1", "comments": [{"id": "c", "author": "甲", "content": "同文"}, {"id": "c", "author": "乙", "content": "同文"}]})
    def test_unknown_total_is_not_invented(self):
        cap = normalize({"id": "n1", "comments": [{"id": "c1", "content": "回答"}]})[0]["capture"]
        self.assertEqual((0, 1, False), (cap["comments_total"], cap["comments_collected"], cap["is_truncated"]))
    def test_string_false_is_not_treated_as_truncated(self):
        cap = normalize({"id": "n1", "capture": {"is_truncated": "false"}})[0]["capture"]
        self.assertFalse(cap["is_truncated"])
    def test_nested_non_object_reply_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "JSON objects"): normalize({"id": "n1", "comments": [{"id": "c1", "replies": ["bad"]}]})


class ValidateTests(unittest.TestCase):
    def test_complete_analysis_passes(self): self.assertEqual([], validate(canonical(True), good(True)))
    def test_social_title_is_optional_but_strict_when_present(self):
        self.assertEqual([], validate(canonical(), good()))
        valid = good(); valid["posts"][0]["social_title"] = "墙面反复发霉怎么办？"; self.assertEqual([], validate(canonical(), valid))
        invalid_values = [123, "太短", "长" * 29, "必看保证根治墙面发霉方法"]
        for value in invalid_values:
            analysis = good(); analysis["posts"][0]["social_title"] = value
            with self.subTest(value=value): self.assertTrue(any("social_title" in error for error in validate(canonical(), analysis)))
    def test_every_candidate_must_be_classified_once(self): self.assertTrue(any("candidate" in x for x in validate(canonical(True), good())))
    def test_every_comment_must_be_classified_once(self):
        a = good(); a["posts"][0]["comments"].pop(); self.assertTrue(any("exactly once" in x for x in validate(canonical(), a)))
    def test_non_question_cannot_smuggle_solution(self):
        a = good(True); a["posts"][1]["solution"] = {}; self.assertTrue(any("must not" in x for x in validate(canonical(True), a)))
    def test_step_cannot_use_speculation(self):
        a = good(); a["posts"][0]["comments"][0]["category"] = "speculation"; self.assertTrue(any("ineligible" in x for x in validate(canonical(), a)))
    def test_step_evidence_must_be_covered_by_claim(self):
        a = good(); a["posts"][0]["solution"]["claims"][0]["evidence_comment_ids"] = ["c2"]; self.assertTrue(any("covered" in x for x in validate(canonical(), a)))
    def test_external_supported_fact_needs_url(self):
        a = good(); a["posts"][0]["solution"]["claims"].append({"claim_id": "ext", "text": "外部事实", "kind": "external_fact", "status": "supported", "evidence_comment_ids": [], "external_sources": []}); self.assertTrue(any("requires URL" in x for x in validate(canonical(), a)))
    def test_non_external_claim_needs_comment_evidence(self):
        a = good(); a["posts"][0]["solution"]["claims"][0]["evidence_comment_ids"] = []
        self.assertTrue(any("non-external claim" in x for x in validate(canonical(), a)))
    def test_high_risk_cannot_be_ready_without_verification(self):
        a = good(); a["posts"][0]["solution"]["risk_level"] = "high"; self.assertTrue(any("high-risk" in x for x in validate(canonical(), a)))
    def test_strict_enums_reject_invented_labels(self):
        a = good(); a["posts"][0]["question_type"] = "viral"; a["posts"][0]["comments"][0]["risk_flags"] = ["medical"]
        errors = validate(canonical(), a); self.assertTrue(any("question_type" in x for x in errors)); self.assertTrue(any("risk_flags" in x for x in errors))
    def test_step_requires_operational_boundaries(self):
        a = good(); del a["posts"][0]["solution"]["steps"][0]["verification"]
        self.assertTrue(any("verification" in x for x in validate(canonical(), a)))
    def test_step_requires_non_empty_applicability_and_stop_conditions(self):
        a = good(); step = a["posts"][0]["solution"]["steps"][0]; step["applies_when"] = []; step["stop_conditions"] = []
        errors = validate(canonical(), a); self.assertTrue(any("applies_when" in x for x in errors)); self.assertTrue(any("stop_conditions" in x for x in errors))
    def test_conflict_requires_two_valid_sides(self):
        a = good(); a["posts"][0]["solution"]["conflicts"] = [{"topic": "是否有效", "positions": [{"claim": "有效", "evidence_comment_ids": ["c1"]}]}]
        self.assertTrue(any("two positions" in x for x in validate(canonical(), a)))
    def test_malformed_nodes_return_errors_instead_of_crashing(self):
        cases = [None, {"posts": {}}, {"posts": ["bad"]}, {"posts": [{"note_id": "n1", "comments": ["bad"]}]},
                 {"posts": [{"note_id": "n1", "solution": {"claims": ["bad"]}}]},
                 {"posts": [{"note_id": "n1", "solution": {"steps": ["bad"]}}]},
                 {"posts": [{"note_id": "n1", "solution": {"conflicts": ["bad"]}}]}]
        for value in cases:
            with self.subTest(value=value): self.assertTrue(any("must" in error for error in validate(canonical(), value)))
    def test_one_verified_fact_cannot_mask_unverified_external_fact(self):
        a = good(); solution = a["posts"][0]["solution"]; solution["risk_level"] = "high"
        solution["claims"] += [
            {"claim_id": "ext1", "kind": "external_fact", "status": "supported", "text": "已核验", "evidence_comment_ids": [], "external_sources": ["https://example.org/a"]},
            {"claim_id": "ext2", "kind": "external_fact", "status": "needs_external_verification", "text": "未核验", "evidence_comment_ids": [], "external_sources": []}]
        self.assertTrue(any("every external fact" in error for error in validate(canonical(), a)))
    def test_unsafe_step_requires_high_and_review_until_fully_verified(self):
        a = good(); a["posts"][0]["comments"][0]["risk_flags"] = ["unsafe_advice"]
        errors = validate(canonical(), a); self.assertTrue(any("high risk_level" in error for error in errors)); self.assertTrue(any("needs_review" in error for error in errors))
        solution = a["posts"][0]["solution"]; solution["risk_level"] = "high"; solution["publish_status"] = "needs_review"
        self.assertEqual([], validate(canonical(), a))
    def test_unsafe_step_can_be_ready_only_when_all_external_facts_verified(self):
        a = good(); post = a["posts"][0]; post["comments"][0]["risk_flags"] = ["unsafe_advice"]
        solution = post["solution"]; solution["risk_level"] = "high"; solution["claims"].append(
            {"claim_id": "ext", "kind": "external_fact", "status": "supported", "text": "权威复核", "evidence_comment_ids": [], "external_sources": ["https://example.org/safety"]})
        self.assertEqual([], validate(canonical(), a))


class RenderTests(unittest.TestCase):
    def test_renderer_uses_canonical_evidence_not_model_copy(self):
        a = good(); a["posts"][0]["comments"][0]["quote"] = "伪造引文"; out = render(canonical(), a); self.assertIn("先重启", out); self.assertNotIn("伪造引文", out)
    def test_all_formats_show_coverage_and_thread(self):
        for fmt in ("report", "xhs-cards", "short-video"):
            out = render(canonical(), good(), fmt); self.assertIn("页面显示 2 条 · 实际采集 2 条", out); self.assertIn("thread `c1`", out)
    def test_report_contains_decision_sections_and_exclusions(self):
        out = render(canonical(True), good(True));
        for heading in ("一句话答案", "可执行步骤", "主张账本", "风险与未知", "排除的候选"): self.assertIn(heading, out)
    def test_reader_facing_enums_are_chinese(self):
        out = render(canonical(), good())
        for label in ("直接答案", "中等", "评论区建议", "已有支持", "风险等级：低", "发布状态：可发布", "导出文件（export）"): self.assertIn(label, out)
        for code in ("direct_answer", "moderate", "community_advice", "supported"): self.assertNotIn(code, out)
    def test_report_lists_each_constraint_and_unknown(self):
        a = good(); solution = a["posts"][0]["solution"]; solution["constraints"] = ["边界甲", "边界乙"]; solution["unknowns"] = ["未知甲", "未知乙"]
        out = render(canonical(), a)
        for item in ("- 边界甲", "- 边界乙", "- 未知甲", "- 未知乙"): self.assertIn(item, out)
    def test_cards_are_dynamic_and_disclosed(self):
        out = render(canonical(), good(), "xhs-cards"); self.assertEqual(8, out.count("## 卡片 ")); self.assertIn("AI 辅助", out)
        self.assertIn("利益关系：未知，发布前人工确认", out)
        self.assertIn("- **停止：** 无法启动", out)
    def test_card_four_separates_action_verification_and_stop(self):
        a = good(); a["posts"][0]["solution"]["steps"].append({"text": "复查", "claim_ids": ["cl1"], "evidence_comment_ids": ["c2"], "applies_when": ["仍有问题"], "verification": "记录结果", "stop_conditions": ["故障加重"]})
        out = render(canonical(), a, "xhs-cards"); self.assertIn("## 卡片 4｜第 2 步", out); self.assertIn("复查", out); self.assertIn("- **验证：** 记录结果", out); self.assertIn("- **停止：** 故障加重", out)
    def test_video_is_timed_storyboard(self):
        out = render(canonical(), good(), "short-video"); self.assertIn("| 时段 | 画面 | 口播 | 字幕 | 证据 |", out); self.assertIn("80–90 秒", out)
    def test_video_splits_steps_without_truncating_sentences(self):
        a = good(); steps = a["posts"][0]["solution"]["steps"]
        steps += [{"text": "第二个完整动作", "claim_ids": ["cl1"], "evidence_comment_ids": ["c2"], "applies_when": ["条件二"], "verification": "完整验证二", "stop_conditions": ["停止条件二"]},
                  {"text": "第三个完整动作", "claim_ids": ["cl1"], "evidence_comment_ids": ["c1"], "applies_when": ["条件三"], "verification": "完整验证三", "stop_conditions": ["停止条件三"]}]
        out = render(canonical(), a, "short-video")
        for value in ("15–26 秒", "26–38 秒", "38–50 秒", "第二个完整动作", "完整验证二", "第三个完整动作", "完整验证三"): self.assertIn(value, out)
        self.assertNotIn("第二个完整动…", out)
    def test_video_uses_chinese_status_and_clean_punctuation(self):
        a = good(); a["posts"][0]["solution"]["summary"] = "先观察。。"
        out = render(canonical(), a, "short-video"); self.assertIn("风险等级是低", out); self.assertIn("发布状态为可发布", out); self.assertNotIn("。。", out); self.assertNotIn("。；", out)
    def test_evidence_excerpt_is_bounded(self):
        rows = canonical(); next(x for x in rows if x.get("comment_id") == "c1")["content"] = "长" * 100
        out = render(rows, good()); self.assertNotIn("长" * 81, out); self.assertIn("…", out)
    def test_repository_example_runs_end_to_end(self):
        root = Path(__file__).resolve().parents[1]
        payload = json.loads((root / "examples/sample-input.json").read_text(encoding="utf-8"))
        analysis = json.loads((root / "examples/sample-analysis.json").read_text(encoding="utf-8"))
        rows = normalize(payload); self.assertEqual([], validate(rows, analysis))
        report = render(rows, analysis, "report"); cards = render(rows, analysis, "xhs-cards"); video = render(rows, analysis, "short-video")
        self.assertIn("页面显示 12 条 · 实际采集 9 条", report); self.assertIn("排除的候选", report); self.assertEqual(10, cards.count("## 卡片 ")); self.assertIn("80–90 秒", video)
    def test_card_ir_has_versioned_dynamic_structure(self):
        deck = build_card_decks(canonical(), good())["decks"][0]
        self.assertEqual(8, len(deck["cards"]))
        self.assertEqual(list(range(1, 9)), [card["index"] for card in deck["cards"]])
        self.assertEqual(["cover", "scope", "action", "experience", "counterexample", "conflicts_risks", "unknowns", "disclosure"], [card["role"] for card in deck["cards"]])
    def test_each_action_card_keeps_all_operational_evidence(self):
        action = next(card for card in build_card_decks(canonical(), good())["decks"][0]["cards"] if card["role"] == "action")
        fields = {block["label"]: block["value"] for block in action["blocks"] if block["type"] == "field"}
        self.assertEqual(["c1"], action["evidence_comment_ids"])
        self.assertEqual({"证据", "适用", "验证", "停止"}, set(fields))
    def test_single_experience_is_not_presented_as_effective(self):
        card = next(card for card in build_card_decks(canonical(), good())["decks"][0]["cards"] if card["role"] == "experience")
        self.assertEqual("一个亲历个案", card["title"]); self.assertNotIn("有效", card["title"])
        self.assertTrue(any(block.get("tone") == "caution" and "不可外推" in block.get("text", "") for block in card["blocks"]))
    def test_small_counterexample_card_keeps_non_extrapolation_notice(self):
        analysis = good(); analysis["posts"][0]["comments"][0]["category"] = "counterexample"
        card = next(card for card in build_card_decks(canonical(), analysis)["decks"][0]["cards"] if card["role"] == "counterexample")
        self.assertTrue(any(block.get("tone") == "caution" and "对照场景" in block.get("text", "") for block in card["blocks"]))
    def test_social_title_is_used_only_for_card_cover(self):
        analysis = good(); post = analysis["posts"][0]; post["question"] = "这是用于报告和视频的完整问题描述吗？"; post["social_title"] = "墙面反复发霉怎么办？"
        card_decks = build_card_decks(canonical(), analysis)
        self.assertEqual("墙面反复发霉怎么办？", card_decks["decks"][0]["cards"][0]["title"])
        self.assertIn(post["question"], render(canonical(), analysis, "report")); self.assertIn(post["question"], render(canonical(), analysis, "short-video"))
        self.assertNotIn(post["question"], render_card_decks_markdown(card_decks))
    def test_synthetic_high_risk_cover_and_scope_are_reader_facing(self):
        root = Path(__file__).resolve().parents[1]
        rows = normalize(json.loads((root / "examples/sample-input.json").read_text(encoding="utf-8")))
        analysis = json.loads((root / "examples/sample-analysis.json").read_text(encoding="utf-8"))
        cards = build_card_decks(rows, analysis)["decks"][0]["cards"]
        cover = " ".join(block["text"] for block in cards[0]["blocks"] if "text" in block)
        scope = json.dumps(cards[1], ensure_ascii=False)
        self.assertIn("合成演示 · 高风险 · 发布前人工复核", cover); self.assertIn("合成演示数据", scope); self.assertIn("达到采集上限", scope)
        self.assertIn("页面显示 12 条 · 实际采集 9 条", scope)
        for raw in ("synthetic_fixture", "reached_limit", "2026-08-15T10:00:00+08:00"): self.assertNotIn(raw, scope)
    def test_unknown_page_total_is_stated_without_a_ratio(self):
        rows = canonical(); rows[0]["capture"]["comments_total"] = 0
        scope = next(card for card in build_card_decks(rows, good())["decks"][0]["cards"] if card["role"] == "scope")
        text = json.dumps(scope, ensure_ascii=False); self.assertIn("实际采集 2 条 · 页面总量未知", text); self.assertNotIn("2/0", text)
    def test_high_risk_conflict_labels_unsafe_and_warning_positions(self):
        root = Path(__file__).resolve().parents[1]
        rows = normalize(json.loads((root / "examples/sample-input.json").read_text(encoding="utf-8")))
        analysis = json.loads((root / "examples/sample-analysis.json").read_text(encoding="utf-8"))
        card = next(card for card in build_card_decks(rows, analysis)["decks"][0]["cards"] if card["role"] == "conflicts_risks")
        self.assertEqual({"type": "notice", "tone": "warning", "text": "以下为评论中的冲突观点，不是操作建议；高风险内容待权威复核"}, card["blocks"][0])
        bullets = [block["text"] for block in card["blocks"] if block["type"] == "bullet"]
        self.assertTrue(any(text.startswith("未核验高风险观点：可以直接喷酒精") for text in bullets))
        self.assertTrue(any(text.startswith("风险提醒：大面积使用存在风险") for text in bullets))
        self.assertFalse(any(text.startswith("可以直接喷酒精") for text in bullets))
        self.assertNotIn("\n- 可以直接喷酒精", render_card_decks_markdown({"schema": "xhs-card-deck/v1", "decks": [build_card_decks(rows, analysis)["decks"][0]]}))
    def test_disclosure_is_brief_with_exactly_one_safety_cta(self):
        card = next(card for card in build_card_decks(canonical(), good())["decks"][0]["cards"] if card["role"] == "disclosure")
        text = " ".join(block.get("text", "") for block in card["blocks"])
        self.assertEqual(1, text.count("？")); self.assertIn("你目前能确认哪一项", text); self.assertEqual([], card["evidence_comment_ids"]); self.assertNotIn("完整证据", text)
    def test_disclosure_cta_uses_first_unknown_without_dangerous_prompt(self):
        analysis = good(); analysis["posts"][0]["solution"]["unknowns"] = ["现场通风条件未知。", "其他条件未提供。"]
        card = next(card for card in build_card_decks(canonical(), analysis)["decks"][0]["cards"] if card["role"] == "disclosure")
        cta = next(block["text"] for block in card["blocks"] if block.get("tone") == "cta")
        self.assertEqual("关于「现场通风条件」，你目前能确认哪一项？", cta); self.assertNotIn("未知", cta); self.assertNotIn("未提供", cta); self.assertEqual(1, cta.count("？")); self.assertNotIn("尝试", cta)
    def test_markdown_is_only_a_serialization_of_card_ir(self):
        card_decks = build_card_decks(canonical(), good())
        self.assertEqual(render_card_decks_markdown(card_decks), render(canonical(), good(), "xhs-cards"))
        self.assertIn("# 附录｜完整证据索引", render_card_decks_markdown(card_decks))
    def test_card_ir_is_deterministic_and_rejects_unknown_schema(self):
        first = serialize_card_decks_json(build_card_decks(canonical(), good()))
        second = serialize_card_decks_json(build_card_decks(canonical(), good()))
        self.assertEqual(first, second); self.assertEqual("xhs-card-deck/v1", json.loads(first)["schema"])
        with self.assertRaisesRegex(ValueError, "unsupported"): render_card_decks_markdown({"schema": "xhs-card-deck/v2"})
    def test_appendix_evidence_comes_only_from_canonical(self):
        analysis = good(); analysis["posts"][0]["comments"][0]["quote"] = "模型伪造短摘"
        evidence = build_card_decks(canonical(), analysis)["decks"][0]["appendix"]["evidence"]
        self.assertEqual(["c1", "c2"], [item["comment_id"] for item in evidence]); self.assertEqual("先重启", evidence[0]["excerpt"]); self.assertNotIn("模型伪造", json.dumps(evidence, ensure_ascii=False))
    def test_unsafe_appendix_entry_carries_a_standalone_warning(self):
        analysis = good(); analysis["posts"][0]["comments"][0]["risk_flags"] = ["unsafe_advice"]
        solution = analysis["posts"][0]["solution"]; solution["risk_level"] = "high"; solution["publish_status"] = "needs_review"
        deck = build_card_decks(canonical(), analysis)["decks"][0]
        evidence = deck["appendix"]["evidence"][0]
        self.assertEqual("未核验高风险观点，不是操作建议", evidence["safety_warning"])
        self.assertEqual("直接答案", evidence["category_label"])
        markdown = render_card_decks_markdown({"schema": "xhs-card-deck/v1", "decks": [deck]})
        entry = next(line for line in markdown.splitlines() if "`c1`" in line)
        self.assertIn("未核验高风险观点，不是操作建议", entry); self.assertIn("先重启", entry)
    def test_missing_likes_are_explicitly_unknown_in_ir_and_markdown(self):
        rows = canonical(); del next(row for row in rows if row.get("comment_id") == "c1")["likes"]
        deck = build_card_decks(rows, good())["decks"][0]
        evidence = deck["appendix"]["evidence"][0]
        self.assertIsNone(evidence["likes"]); self.assertEqual("赞数未知", evidence["likes_label"])
        markdown = render_card_decks_markdown({"schema": "xhs-card-deck/v1", "decks": [deck]})
        self.assertIn("｜赞数未知｜", markdown); self.assertNotIn("｜赞｜", markdown)
        self.assertIn("｜赞数未知｜", render(rows, good(), "report"))
    def test_multiple_question_posts_become_separate_decks(self):
        rows = normalize({"notes": [{"id": "a", "comments": [{"id": "a1", "content": "做A"}]}, {"id": "b", "comments": [{"id": "b1", "content": "做B"}]}]})
        def post(note_id, comment_id):
            return {"note_id": note_id, "is_question": True, "question": f"{note_id}怎么做", "question_type": "how_to", "confidence": .9,
                    "comments": [{"comment_id": comment_id, "category": "direct_answer", "claim": "执行", "confidence": .8, "evidence_quality": "moderate", "risk_flags": []}],
                    "solution": {"summary": "执行并验证", "risk_level": "low", "publish_status": "ready", "claims": [{"claim_id": f"claim-{note_id}", "kind": "community_advice", "status": "supported", "text": "执行", "evidence_comment_ids": [comment_id], "external_sources": []}],
                                 "steps": [{"text": "执行", "claim_ids": [f"claim-{note_id}"], "evidence_comment_ids": [comment_id], "applies_when": ["条件满足"], "verification": "结果出现", "stop_conditions": ["结果异常"]}], "constraints": [], "conflicts": [], "unknowns": []}}
        card_decks = build_card_decks(rows, {"posts": [post("a", "a1"), post("b", "b1")]})
        self.assertEqual(["note:a", "note:b"], [deck["deck_id"] for deck in card_decks["decks"]]); self.assertEqual(8, len(card_decks["decks"][0]["cards"])); self.assertEqual(8, len(card_decks["decks"][1]["cards"]))
    def test_cli_can_write_structured_sidecar_without_changing_markdown(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp); canonical_path, analysis_path = temp / "canonical.jsonl", temp / "analysis.json"
            canonical_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in canonical()), encoding="utf-8")
            analysis_path.write_text(json.dumps(good(), ensure_ascii=False), encoding="utf-8")
            output, sidecar = temp / "cards.md", temp / "cards.json"
            subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / "render_result.py"), str(canonical_path), str(analysis_path), str(output), "--format", "xhs-cards", "--structured-output", str(sidecar)], check=True, cwd=root, capture_output=True, text=True)
            self.assertEqual("xhs-card-deck/v1", json.loads(sidecar.read_text(encoding="utf-8"))["schema"])
            self.assertEqual(render(canonical(), good(), "xhs-cards"), output.read_text(encoding="utf-8"))


if __name__ == "__main__": unittest.main()
