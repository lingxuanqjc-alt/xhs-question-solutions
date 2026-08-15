import json, sys, unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / ".agents/skills/xhs-question-solutions/scripts"
sys.path.insert(0, str(SCRIPTS))
from normalize_xhs_export import normalize, number
from validate_result import validate
from render_result import render


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
            out = render(canonical(), good(), fmt); self.assertIn("评论覆盖：2/2", out); self.assertIn("thread `c1`", out)
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
    def test_cards_are_nine_disclosed_cards(self):
        out = render(canonical(), good(), "xhs-cards"); self.assertEqual(9, out.count("## 卡片 ")); self.assertIn("AI 辅助", out)
        self.assertIn("利益关系：未知，发布前人工确认", out)
        self.assertIn("停止：无法启动", out)
    def test_card_four_separates_action_verification_and_stop(self):
        a = good(); a["posts"][0]["solution"]["steps"].append({"text": "复查", "claim_ids": ["cl1"], "evidence_comment_ids": ["c2"], "applies_when": ["仍有问题"], "verification": "记录结果", "stop_conditions": ["故障加重"]})
        out = render(canonical(), a, "xhs-cards"); self.assertIn("### 动作 2", out); self.assertIn("- 动作：复查", out); self.assertIn("- 验证：记录结果", out); self.assertIn("- 停止：故障加重", out)
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
        self.assertIn("评论覆盖：9/12", report); self.assertIn("排除的候选", report); self.assertEqual(9, cards.count("## 卡片 ")); self.assertIn("80–90 秒", video)


if __name__ == "__main__": unittest.main()
