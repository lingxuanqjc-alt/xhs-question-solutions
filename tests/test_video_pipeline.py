import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents/skills/xhs-question-solutions/scripts"
sys.path.insert(0, str(SCRIPTS))

from normalize_xhs_export import normalize
from render_result import build_card_decks, render
from render_video import (
    PROFILE,
    SCHEMA,
    UNSAFE_NOTICE_CODE,
    UNSAFE_WARNING,
    _caption_chunks,
    _stop_message,
    build_video_ir,
    display_units,
    render_video_markdown,
    render_mp4s,
    serialize_video_ir,
    validate_video_ir,
    write_video_projects,
)


def sample():
    payload = json.loads((ROOT / "examples/sample-input.json").read_text(encoding="utf-8"))
    analysis = json.loads((ROOT / "examples/sample-analysis.json").read_text(encoding="utf-8"))
    return normalize(payload), analysis


class VideoContractTests(unittest.TestCase):
    def test_hook_frontloads_title_bounded_answer_and_a_specific_view_path(self):
        canonical, analysis = sample()
        post = analysis["posts"][0]
        hook = build_video_ir(canonical, analysis)["videos"][0]["scenes"][0]
        title = post["social_title"]
        path = f"继续看{len(post['solution']['steps'])}步"

        self.assertEqual(3_000, hook["end_ms"] - hook["start_ms"])
        self.assertEqual(post["solution"]["summary"], hook["content"]["summary"])
        self.assertTrue(hook["narration"].startswith(title))
        self.assertIn(path, hook["narration"])
        excerpt = hook["narration"].removeprefix(title).removesuffix("。").removesuffix(path).strip("。！？!?；;，,：: ")
        self.assertTrue(excerpt)
        self.assertIn(excerpt, post["solution"]["summary"])
        self.assertEqual(hook["narration"], "".join(caption["text"] for caption in hook["captions"]))
        self.assertLessEqual(display_units(hook["narration"]), 28)
        self.assertLessEqual(hook["captions"][-1]["endMs"], 3_000)

    def test_hook_excerpt_keeps_a_negative_safety_boundary_instead_of_clipping_it(self):
        canonical, analysis = sample()
        post = analysis["posts"][0]
        post["solution"]["summary"] = "不要直接喷酒精，先查潮湿根因再处理。"
        hook = build_video_ir(canonical, analysis)["videos"][0]["scenes"][0]
        title = post["social_title"]
        path = f"继续看{len(post['solution']['steps'])}步"
        excerpt = hook["narration"].removeprefix(title).removesuffix("。").removesuffix(path).strip("。！？!?；;，,：: ")

        self.assertIn(excerpt, post["solution"]["summary"])
        self.assertIn("不要", excerpt)
        self.assertIn("喷酒精", excerpt)

    def test_hook_uses_a_short_route_when_title_or_summary_cannot_safely_fit(self):
        cases = (
            ("28-character title", lambda post: post.__setitem__("social_title", "甲" * 28)),
            ("long question fallback", lambda post: (post.pop("social_title", None), post.__setitem__("question", "这个没有短标题的问题描述包含很多现场限制且显然无法在三秒口播中完整读完应该怎么办"))),
            ("single long safety clause", lambda post: post["solution"].__setitem__("summary", "不要在无法确认通风火源敏感人群产品说明和现场条件之前自行进行任何高风险处理")),
        )
        for label, mutate in cases:
            with self.subTest(case=label):
                canonical, analysis = sample()
                post = analysis["posts"][0]
                mutate(post)
                expected_title = post.get("social_title") or post["question"]
                expected_summary = post["solution"]["summary"]

                hook = build_video_ir(canonical, analysis)["videos"][0]["scenes"][0]

                self.assertEqual(expected_title, hook["content"]["social_title"])
                self.assertEqual(expected_summary, hook["content"]["summary"])
                self.assertEqual("问题、证据、行动，继续看3步。", hook["narration"])
                self.assertNotIn(expected_summary.strip("。"), hook["narration"])

    def test_cta_copies_the_analysis_selected_primary_stop_condition(self):
        canonical, analysis = sample()
        solution = analysis["posts"][0]["solution"]
        steps = solution["steps"]
        solution["primary_stop_condition"] = steps[1]["stop_conditions"][0]
        cta = next(scene for scene in build_video_ir(canonical, analysis)["videos"][0]["scenes"] if scene["role"] == "cta")

        self.assertIn(solution["primary_stop_condition"], cta["content"]["stop_message"])
        self.assertIn("设备过热或冒烟", _stop_message([{"stop_conditions": ["设备过热或冒烟。"]}], "设备过热或冒烟。"))

    def test_primary_stop_condition_must_be_one_of_the_step_boundaries(self):
        canonical, analysis = sample()
        analysis["posts"][0]["solution"]["primary_stop_condition"] = "模型自行编造的停止条件"

        with self.assertRaisesRegex(ValueError, "primary_stop_condition"):
            build_video_ir(canonical, analysis)

    def test_missing_primary_stop_condition_keeps_every_boundary_without_ranking_words(self):
        canonical, analysis = sample()
        solution = analysis["posts"][0]["solution"]
        solution.pop("primary_stop_condition", None)
        for index, step in enumerate(solution["steps"], 1):
            step["stop_conditions"] = [f"停止边界{index}"]
        cta = next(scene for scene in build_video_ir(canonical, analysis)["videos"][0]["scenes"] if scene["role"] == "cta")

        for condition in (condition for step in solution["steps"] for condition in step["stop_conditions"]):
            self.assertIn(condition, cta["content"]["stop_message"])
        self.assertEqual("出现无法安全判断或情况恶化时，请停止自行处理并寻求合适的专业帮助。", _stop_message([], None))

    def test_missing_primary_stop_condition_fails_early_when_all_boundaries_do_not_fit(self):
        canonical, analysis = sample()
        analysis["posts"][0]["solution"].pop("primary_stop_condition", None)

        with self.assertRaisesRegex(ValueError, "CTA_STOP_CONDITIONS_TOO_LONG"):
            build_video_ir(canonical, analysis)

    def test_stop_boundaries_reject_leading_or_trailing_whitespace_before_rendering(self):
        cases = (
            ("step boundary", lambda solution: solution["steps"][0]["stop_conditions"].__setitem__(0, "  存在火源、通风不足或敏感人群  "), "stop_conditions.*leading or trailing whitespace"),
            ("primary boundary", lambda solution: solution.__setitem__("primary_stop_condition", f" {solution['primary_stop_condition']} "), "primary_stop_condition.*leading or trailing whitespace"),
        )
        for label, mutate, expected in cases:
            with self.subTest(case=label):
                canonical, analysis = sample()
                mutate(analysis["posts"][0]["solution"])

                with self.assertRaisesRegex(ValueError, expected):
                    build_video_ir(canonical, analysis)

    def test_representative_experience_and_counterexample_ignore_comment_order(self):
        canonical, analysis = sample()
        post = analysis["posts"][0]
        post["comments"].extend([
            {"comment_id": "c9", "category": "firsthand_experience", "claim": "较弱亲历", "confidence": 0.99, "evidence_quality": "weak", "risk_flags": []},
            {"comment_id": "c10", "category": "firsthand_experience", "claim": "同质量但置信度更低", "confidence": 0.80, "evidence_quality": "strong", "risk_flags": []},
            {"comment_id": "c11", "category": "counterexample", "claim": "较强反例", "confidence": 0.91, "evidence_quality": "strong", "risk_flags": []},
            {"comment_id": "c12", "category": "firsthand_experience", "claim": "高置信但有商业偏差", "confidence": 0.99, "evidence_quality": "strong", "risk_flags": ["commercial_bias"]},
            {"comment_id": "c13", "category": "counterexample", "claim": "同分反例", "confidence": 0.91, "evidence_quality": "strong", "risk_flags": []},
        ])
        canonical.extend([
            {"kind": "comment", "comment_id": "c9", "note_id": post["note_id"], "parent_id": None, "thread_id": "c9", "author": "用户-test9", "content": "较弱亲历", "likes": 0, "created_at": ""},
            {"kind": "comment", "comment_id": "c10", "note_id": post["note_id"], "parent_id": None, "thread_id": "c10", "author": "用户-test10", "content": "同质量但置信度更低", "likes": 0, "created_at": ""},
            {"kind": "comment", "comment_id": "c11", "note_id": post["note_id"], "parent_id": None, "thread_id": "c11", "author": "用户-test11", "content": "较强反例", "likes": 0, "created_at": ""},
            {"kind": "comment", "comment_id": "c12", "note_id": post["note_id"], "parent_id": None, "thread_id": "c12", "author": "用户-test12", "content": "高置信但有商业偏差", "likes": 0, "created_at": ""},
            {"kind": "comment", "comment_id": "c13", "note_id": post["note_id"], "parent_id": None, "thread_id": "c13", "author": "用户-test13", "content": "同分反例", "likes": 0, "created_at": ""},
        ])
        first = next(scene for scene in build_video_ir(canonical, analysis)["videos"][0]["scenes"] if scene["role"] == "evidence")
        post["comments"].reverse()
        second = next(scene for scene in build_video_ir(canonical, analysis)["videos"][0]["scenes"] if scene["role"] == "evidence")

        self.assertEqual(first["content"], second["content"])
        self.assertEqual("c1", first["content"]["experience"]["comment_id"])
        self.assertEqual("c11", first["content"]["counterexample"]["comment_id"])

    def test_caption_chunks_prefer_boundaries_without_orphaning_words_or_units(self):
        narration = "先确认AI Agent配置，再观察30 分钟；异常时停止。"
        chunks = _caption_chunks(narration, max_units=10)

        self.assertEqual(narration, "".join(chunks))
        for protected in ("AI", "Agent", "30 分钟"):
            self.assertTrue(any(protected in chunk for chunk in chunks), (protected, chunks))
        for chunk in chunks[1:]:
            visible = chunk.strip("。！？!?；;，,：: ")
            self.assertIsNone(re.fullmatch(r"[\u3400-\u9fff]{1,2}", visible), chunks)
        self.assertTrue(any(chunk.endswith(("，", "；", "。")) for chunk in chunks[:-1]))
        balanced = _caption_chunks("一二三四五六七八九十甲乙", max_units=10)
        self.assertEqual("一二三四五六七八九十甲乙", "".join(balanced))
        self.assertGreaterEqual(len(balanced[-1]), 3)

    def test_sample_has_a_75_second_mobile_first_story_arc(self):
        canonical, analysis = sample()
        video = build_video_ir(canonical, analysis)["videos"][0]
        self.assertEqual((SCHEMA, PROFILE), ("xhs-video/v1", "xhs-vertical-1080x1920-v1"))
        self.assertEqual(75_000, video["duration_ms"])
        self.assertEqual(2_250, video["duration_in_frames"])
        self.assertEqual(
            ["hook", "scope", "action", "action", "action", "evidence", "conflict_risk", "risk_unknowns", "disclosure", "cta"],
            [scene["role"] for scene in video["scenes"]],
        )
        self.assertEqual(
            [0, 3_000, 8_000, 17_000, 26_000, 35_000, 45_000, 56_000, 64_000, 71_000],
            [scene["start_ms"] for scene in video["scenes"]],
        )
        self.assertEqual([], validate_video_ir(build_video_ir(canonical, analysis), canonical, analysis))

    def test_action_scenes_keep_every_operational_boundary(self):
        canonical, analysis = sample()
        actions = [scene for scene in build_video_ir(canonical, analysis)["videos"][0]["scenes"] if scene["role"] == "action"]
        steps = analysis["posts"][0]["solution"]["steps"]
        self.assertEqual(len(steps), len(actions))
        for number, (scene, step) in enumerate(zip(actions, steps), 1):
            self.assertEqual(number, scene["content"]["step_number"])
            for field in ("text", "applies_when", "verification", "stop_conditions"):
                self.assertEqual(step[field], scene["content"][field])
            self.assertEqual(step["evidence_comment_ids"], scene["evidence_comment_ids"])

    def test_unsafe_comment_never_appears_without_a_persistent_warning(self):
        canonical, analysis = sample()
        scenes = build_video_ir(canonical, analysis)["videos"][0]["scenes"]
        unsafe_scenes = [scene for scene in scenes if "c2" in scene["evidence_comment_ids"]]
        self.assertEqual(["c2"], build_video_ir(canonical, analysis)["videos"][0]["unsafe_evidence_comment_ids"])
        self.assertTrue(unsafe_scenes)
        for scene in unsafe_scenes:
            self.assertIn(UNSAFE_NOTICE_CODE, scene["persistent_notices"])
            self.assertTrue(scene["narration"].startswith(UNSAFE_WARNING))
            self.assertTrue(scene["captions"][0]["text"].startswith(UNSAFE_WARNING))

    def test_captions_are_lossless_timed_and_use_the_portable_shape(self):
        canonical, analysis = sample()
        scenes = build_video_ir(canonical, analysis)["videos"][0]["scenes"]
        for scene in scenes:
            self.assertEqual(scene["narration"], "".join(caption["text"] for caption in scene["captions"]))
            previous_end = scene["start_ms"]
            for caption in scene["captions"]:
                self.assertEqual(
                    {"text", "startMs", "endMs", "timestampMs", "confidence"},
                    set(caption),
                )
                self.assertGreaterEqual(caption["startMs"], previous_end)
                self.assertGreater(caption["endMs"], caption["startMs"])
                self.assertLessEqual(caption["endMs"], scene["end_ms"])
                seconds = (caption["endMs"] - caption["startMs"]) / 1000
                self.assertLessEqual(display_units(caption["text"]) / seconds, 10.0)
                self.assertNotIn(caption["text"], set("。！？!?；;，,：:"))
                self.assertFalse(all(char in "。！？!?；;，,：:" for char in caption["text"]))
                visible = caption["text"].strip("。！？!?；;，,：: ")
                self.assertIsNone(re.fullmatch(r"[\u3400-\u9fff]{1,2}", visible), scene["scene_id"])
                previous_end = caption["endMs"]

    def test_generated_narration_has_no_duplicate_sentence_punctuation(self):
        canonical, analysis = sample()
        scenes = build_video_ir(canonical, analysis)["videos"][0]["scenes"]
        for scene in scenes:
            self.assertNotIn("。。", scene["narration"], scene["scene_id"])

    def test_final_disclosure_and_cta_are_self_contained(self):
        canonical, analysis = sample()
        scenes = build_video_ir(canonical, analysis)["videos"][0]["scenes"]
        disclosure = next(scene for scene in scenes if scene["role"] == "disclosure")
        cta = next(scene for scene in scenes if scene["role"] == "cta")
        self.assertEqual("合成演示数据", disclosure["content"]["source_label"])
        self.assertTrue(disclosure["content"]["is_truncated"])
        self.assertIn("评论未完整采集", disclosure["narration"])
        self.assertIn("霉斑面积、墙体含水情况和实际水分来源", cta["content"]["question"])

    def test_video_reuses_the_canonical_evidence_appendix(self):
        canonical, analysis = sample()
        video = build_video_ir(canonical, analysis)["videos"][0]
        cards = build_card_decks(canonical, analysis)["decks"][0]
        self.assertEqual(cards["appendix"], video["appendix"])

    def test_markdown_is_serialized_from_video_ir_and_keeps_the_public_shape(self):
        canonical, analysis = sample()
        ir = build_video_ir(canonical, analysis)
        markdown = render_video_markdown(ir)
        self.assertEqual(markdown, render(canonical, analysis, "short-video"))
        self.assertIn("| 时段 | 画面 | 口播 | 字幕 | 证据 |", markdown)
        self.assertIn("0–3 秒", markdown)
        self.assertIn(UNSAFE_WARNING, markdown)
        self.assertIn("### 描述区披露", markdown)
        self.assertIn("### 证据索引", markdown)

    def test_serialization_is_byte_deterministic(self):
        canonical, analysis = sample()
        self.assertEqual(
            serialize_video_ir(build_video_ir(canonical, analysis)),
            serialize_video_ir(build_video_ir(canonical, analysis)),
        )

    def test_committed_video_examples_are_reproducible_from_the_builder(self):
        canonical, analysis = sample()
        ir = build_video_ir(canonical, analysis)
        example = ROOT / "examples/sample-video"
        self.assertEqual(ir, json.loads((example / "video-projects.json").read_text(encoding="utf-8")))
        self.assertEqual(render_video_markdown(ir), (example / "short-video.md").read_text(encoding="utf-8"))
        self.assertEqual(render_video_markdown(ir), (ROOT / "examples/sample-short-video.md").read_text(encoding="utf-8"))
        props = next(example.glob("*.props.json"))
        self.assertEqual({"schema": SCHEMA, "video": ir["videos"][0]}, json.loads(props.read_text(encoding="utf-8")))

    def test_validator_rejects_caption_drift_unknown_fields_and_timing_gaps(self):
        canonical, analysis = sample()
        for mutate, expected in (
            (lambda ir: ir["videos"][0]["scenes"][0]["captions"][0].__setitem__("text", "被改写"), "CAPTION_NARRATION_MISMATCH"),
            (lambda ir: ir["videos"][0]["scenes"][0].__setitem__("surprise", True), "UNKNOWN_FIELD"),
            (lambda ir: ir["videos"][0]["scenes"][1].__setitem__("start_ms", 3_001), "SCENE_TIMING"),
        ):
            with self.subTest(expected=expected):
                ir = build_video_ir(canonical, analysis)
                mutate(ir)
                self.assertTrue(any(expected in error for error in validate_video_ir(ir, canonical, analysis)))

    def test_validator_rejects_missing_fields_and_unsafe_conflict_evidence_drift(self):
        canonical, analysis = sample()
        for mutate, expected in (
            (lambda ir: ir["videos"][0].pop("video_id"), "MISSING_FIELD"),
            (lambda ir: ir["videos"][0].pop("unsafe_evidence_comment_ids"), "MISSING_FIELD"),
            (lambda ir: ir["videos"][0]["scenes"][0]["content"].pop("summary"), "MISSING_FIELD"),
            (lambda ir: ir["videos"][0].pop("appendix"), "MISSING_FIELD"),
            (lambda ir: ir["videos"][0]["unsafe_evidence_comment_ids"].clear(), "VIDEO_CONTENT_MISMATCH"),
            (lambda ir: next(scene for scene in ir["videos"][0]["scenes"] if scene["role"] == "conflict_risk")["evidence_comment_ids"].remove("c2"), "VIDEO_CONTENT_MISMATCH"),
        ):
            with self.subTest(expected=expected):
                ir = build_video_ir(canonical, analysis)
                mutate(ir)
                self.assertTrue(any(expected in error for error in validate_video_ir(ir, canonical, analysis)))

    def test_python_validator_rejects_ambiguous_v1_scalar_types(self):
        canonical, analysis = sample()
        mutations = (
            ("null video_id", lambda video: video.__setitem__("video_id", None)),
            ("empty video_id", lambda video: video.__setitem__("video_id", "")),
            ("null note_id", lambda video: video.__setitem__("note_id", None)),
            ("empty note_id", lambda video: video.__setitem__("note_id", "")),
            ("null scene_id", lambda video: video["scenes"][0].__setitem__("scene_id", None)),
            ("empty scene_id", lambda video: video["scenes"][0].__setitem__("scene_id", "")),
            ("float width", lambda video: video.__setitem__("width", 1080.5)),
            ("float height", lambda video: video.__setitem__("height", 1920.5)),
            ("float fps", lambda video: video.__setitem__("fps", 30.5)),
            ("float duration milliseconds", lambda video: video.__setitem__("duration_ms", 75000.5)),
            ("float duration frames", lambda video: video.__setitem__("duration_in_frames", 2250.5)),
            ("float scene index", lambda video: video["scenes"][0].__setitem__("index", 1.5)),
            ("bool scene start", lambda video: video["scenes"][0].__setitem__("start_ms", True)),
            ("bool scene end", lambda video: video["scenes"][0].__setitem__("end_ms", True)),
            ("bool caption start", lambda video: video["scenes"][0]["captions"][0].__setitem__("startMs", True)),
            ("bool caption end", lambda video: video["scenes"][0]["captions"][0].__setitem__("endMs", True)),
            ("empty evidence id", lambda video: video["scenes"][2].__setitem__("evidence_comment_ids", [""])),
            ("non-string evidence id", lambda video: video["scenes"][2].__setitem__("evidence_comment_ids", [{}])),
            ("empty appendix evidence id", lambda video: video["appendix"]["evidence"][0].__setitem__("comment_id", "")),
            ("non-string unsafe evidence id", lambda video: video.__setitem__("unsafe_evidence_comment_ids", [{}])),
            ("nonhashable scene role", lambda video: video["scenes"][0].__setitem__("role", [])),
        )
        for label, mutate in mutations:
            with self.subTest(case=label):
                video = copy.deepcopy(build_video_ir(canonical, analysis)["videos"][0])
                mutate(video)
                errors = validate_video_ir({"schema": SCHEMA, "videos": [video]})
                self.assertTrue(any("TYPE" in error for error in errors), errors)

    def test_node_props_boundary_rejects_invalid_v1_scalars_before_browser_lookup(self):
        canonical, analysis = sample()
        renderer = SCRIPTS / "render_video.mjs"
        mutations = (
            ("null video", lambda props: props.__setitem__("video", None)),
            ("null video_id", lambda props: props["video"].__setitem__("video_id", None)),
            ("empty video_id", lambda props: props["video"].__setitem__("video_id", "")),
            ("null note_id", lambda props: props["video"].__setitem__("note_id", None)),
            ("empty note_id", lambda props: props["video"].__setitem__("note_id", "")),
            ("null scene_id", lambda props: props["video"]["scenes"][0].__setitem__("scene_id", None)),
            ("empty scene_id", lambda props: props["video"]["scenes"][0].__setitem__("scene_id", "")),
            ("float width", lambda props: props["video"].__setitem__("width", 1080.5)),
            ("float height", lambda props: props["video"].__setitem__("height", 1920.5)),
            ("float fps", lambda props: props["video"].__setitem__("fps", 30.5)),
            ("float duration milliseconds", lambda props: props["video"].__setitem__("duration_ms", 75000.5)),
            ("float duration frames", lambda props: props["video"].__setitem__("duration_in_frames", 2250.5)),
            ("float scene index", lambda props: props["video"]["scenes"][0].__setitem__("index", 1.5)),
            ("bool scene start", lambda props: props["video"]["scenes"][0].__setitem__("start_ms", True)),
            ("bool scene end", lambda props: props["video"]["scenes"][0].__setitem__("end_ms", True)),
            ("bool caption start", lambda props: props["video"]["scenes"][0]["captions"][0].__setitem__("startMs", True)),
            ("bool caption end", lambda props: props["video"]["scenes"][0]["captions"][0].__setitem__("endMs", True)),
            ("empty evidence id", lambda props: props["video"]["scenes"][2].__setitem__("evidence_comment_ids", [""])),
            ("non-string evidence id", lambda props: props["video"]["scenes"][2].__setitem__("evidence_comment_ids", [{}])),
            ("empty appendix evidence id", lambda props: props["video"]["appendix"]["evidence"][0].__setitem__("comment_id", "")),
            ("non-string unsafe evidence id", lambda props: props["video"].__setitem__("unsafe_evidence_comment_ids", [{}])),
        )
        for label, mutate in mutations:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                props = {"schema": SCHEMA, "video": copy.deepcopy(build_video_ir(canonical, analysis)["videos"][0])}
                mutate(props)
                props_path = Path(tmp) / "invalid.props.json"
                props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
                output_path = Path(tmp) / "out.mp4"
                output_path.write_bytes(b"existing")
                result = subprocess.run(
                    ["node", str(renderer), "--props", str(props_path), "--output", str(output_path)],
                    text=True, encoding="utf-8", capture_output=True, check=False,
                )
                message = result.stderr + result.stdout
                self.assertEqual(3, result.returncode, message)
                self.assertIn("Invalid xhs-video/v1 props", message)
                self.assertNotIn("TypeError", message)
                self.assertNotIn("browser", message.lower())

    def test_node_caption_density_matches_python_for_cjk_emoji_and_latin(self):
        canonical, analysis = sample()
        renderer = SCRIPTS / "render_video.mjs"
        for label, text in (
            ("cjk", "密" * 13), ("emoji", "😀" * 13), ("supplementary han", "𠀀" * 13),
            ("common symbol", "☀" * 13), ("ambiguous", "Ω" * 25), ("latin", "a" * 26),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                video = copy.deepcopy(build_video_ir(canonical, analysis)["videos"][0])
                caption = video["scenes"][0]["captions"][0]
                caption["text"] = text
                caption["endMs"] = caption["startMs"] + 1_200
                video["scenes"][0]["narration"] = "".join(item["text"] for item in video["scenes"][0]["captions"])
                python_errors = validate_video_ir({"schema": SCHEMA, "videos": [video]})
                self.assertTrue(any("CAPTION_DENSITY" in error for error in python_errors), python_errors)
                props = Path(tmp) / "dense.props.json"
                props.write_text(json.dumps({"schema": SCHEMA, "video": video}, ensure_ascii=False), encoding="utf-8")
                result = subprocess.run(
                    ["node", str(renderer), "--props", str(props), "--output", str(Path(tmp) / "out.mp4")],
                    text=True, encoding="utf-8", capture_output=True, check=False,
                )
                self.assertEqual(3, result.returncode, result.stderr + result.stdout)
                self.assertIn("CAPTION_DENSITY", result.stderr + result.stdout)
                self.assertNotIn("browser", (result.stderr + result.stdout).lower())

    def test_half_and_zero_width_caption_policy_matches_in_python_and_node(self):
        canonical, analysis = sample()
        for label, text in (
            ("ambiguous", "Ω" * 13), ("combining acute", "\u0301" * 25),
            ("new combining mark", "\u1acf" * 25), ("variation selector", "\ufe0f" * 25),
            ("supplementary variation selector", "\U000e0100" * 25), ("zwj", "\u200d" * 25),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                video = copy.deepcopy(build_video_ir(canonical, analysis)["videos"][0])
                caption = video["scenes"][0]["captions"][0]
                caption["text"] = text
                caption["endMs"] = caption["startMs"] + 1_200
                video["scenes"][0]["narration"] = "".join(item["text"] for item in video["scenes"][0]["captions"])
                python_errors = validate_video_ir({"schema": SCHEMA, "videos": [video]})
                self.assertFalse(any("CAPTION_DENSITY" in error for error in python_errors), python_errors)
                props = Path(tmp) / "width-policy.props.json"
                output = Path(tmp) / "existing.mp4"
                props.write_text(json.dumps({"schema": SCHEMA, "video": video}, ensure_ascii=False), encoding="utf-8")
                output.write_bytes(b"existing")
                result = subprocess.run(
                    ["node", str(SCRIPTS / "render_video.mjs"), "--props", str(props), "--output", str(output)],
                    text=True, encoding="utf-8", capture_output=True, check=False,
                )
                self.assertNotIn("CAPTION_DENSITY", result.stderr + result.stdout)
                self.assertEqual(2, result.returncode, result.stderr + result.stdout)

    def test_node_boundary_rejects_combined_removal_of_every_display_warning(self):
        canonical, analysis = sample()
        video = build_video_ir(canonical, analysis)["videos"][0]
        next(item for item in video["appendix"]["evidence"] if item["comment_id"] == "c2").pop("safety_warning")
        for scene in video["scenes"]:
            if "c2" not in scene["evidence_comment_ids"]:
                continue
            scene["persistent_notices"].remove(UNSAFE_NOTICE_CODE)
            scene["narration"] = scene["narration"].removeprefix(f"{UNSAFE_WARNING}。")
            scene["captions"] = scene["captions"][1:]
        with tempfile.TemporaryDirectory() as tmp:
            props = Path(tmp) / "unsafe.props.json"
            props.write_text(json.dumps({"schema": SCHEMA, "video": video}, ensure_ascii=False), encoding="utf-8")
            renderer = SCRIPTS / "render_video.mjs"
            result = subprocess.run(["node", str(renderer), "--props", str(props), "--output", str(Path(tmp) / "out.mp4")],
                                    text=True, encoding="utf-8", capture_output=True, check=False)
            self.assertNotEqual(0, result.returncode)
            message = (result.stderr + result.stdout).lower()
            self.assertIn("unsafe evidence c2 lost its appendix warning", message)
            self.assertNotIn("browser", message)

    def test_more_than_five_steps_fails_instead_of_cramming_or_truncating(self):
        canonical, analysis = sample()
        step = analysis["posts"][0]["solution"]["steps"][0]
        analysis["posts"][0]["solution"]["steps"] = [copy.deepcopy(step) for _ in range(6)]
        with self.assertRaisesRegex(ValueError, "1-5 steps"):
            build_video_ir(canonical, analysis)

    def test_project_writer_emits_portable_props_for_every_video(self):
        canonical, analysis = sample()
        with tempfile.TemporaryDirectory() as tmp:
            ir, written = write_video_projects(canonical, analysis, Path(tmp))
            self.assertEqual(1, len(written))
            video, props_path, mp4_path = written[0]
            self.assertEqual({"schema": SCHEMA, "video": video}, json.loads(props_path.read_text(encoding="utf-8")))
            self.assertEqual(".mp4", mp4_path.suffix)
            self.assertEqual(ir, json.loads((Path(tmp) / "video-projects.json").read_text(encoding="utf-8")))

    def test_failed_mp4_render_keeps_previous_complete_video(self):
        canonical, analysis = sample()
        with tempfile.TemporaryDirectory() as tmp:
            _, written = write_video_projects(canonical, analysis, Path(tmp))
            target = written[0][2]; target.write_bytes(b"old-complete-video")

            def fail_runner(command, **_kwargs):
                output = Path(command[command.index("--output") + 1])
                output.write_bytes(b"partial")
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="forced failure")

            with self.assertRaisesRegex(RuntimeError, "previous MP4 is unchanged"):
                render_mp4s(written, node="node", runner=fail_runner)
            self.assertEqual(b"old-complete-video", target.read_bytes())
            self.assertFalse(list(Path(tmp).glob(".*.rendering-*.mp4")))

    def test_successful_mp4_render_atomically_replaces_previous_video(self):
        canonical, analysis = sample()
        with tempfile.TemporaryDirectory() as tmp:
            _, written = write_video_projects(canonical, analysis, Path(tmp))
            target = written[0][2]; target.write_bytes(b"old-complete-video")

            def success_runner(command, **_kwargs):
                output = Path(command[command.index("--output") + 1])
                payload = b"\x00\x00\x00\x18ftypisom" + b"rendered-video"
                output.write_bytes(payload)
                summary = {"codec": "h264", "width": 1080, "height": 1920, "fps": 30,
                           "duration_in_frames": 2250, "rendered_frame_range": None,
                           "audio": "none", "file_size": len(payload),
                           "probe": {"codec": "h264", "width": 1080, "height": 1920, "audio_streams": 0, "duration_seconds": 75.0}}
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps(summary), stderr="")

            summaries = render_mp4s(written, node="node", runner=success_runner)
            self.assertIn(b"ftyp", target.read_bytes()[:32])
            self.assertEqual(target.name, summaries[0]["output"])
            self.assertFalse(list(Path(tmp).glob(".*.backup-*.mp4")))

    def test_mp4_metadata_mismatch_never_replaces_the_previous_video(self):
        canonical, analysis = sample()
        with tempfile.TemporaryDirectory() as tmp:
            _, written = write_video_projects(canonical, analysis, Path(tmp))
            target = written[0][2]; target.write_bytes(b"old-complete-video")

            def wrong_metadata(command, **_kwargs):
                output = Path(command[command.index("--output") + 1])
                payload = b"\x00\x00\x00\x18ftypisom" + b"wrong-duration"
                output.write_bytes(payload)
                summary = {"codec": "h264", "width": 1080, "height": 1920, "fps": 30,
                           "duration_in_frames": 2249, "rendered_frame_range": None,
                           "audio": "none", "file_size": len(payload),
                           "probe": {"codec": "h264", "width": 1080, "height": 1920, "audio_streams": 0, "duration_seconds": 75.0}}
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps(summary), stderr="")

            with self.assertRaisesRegex(RuntimeError, "metadata mismatch"):
                render_mp4s(written, node="node", runner=wrong_metadata)
            self.assertEqual(b"old-complete-video", target.read_bytes())

    def test_multi_video_render_is_all_or_nothing(self):
        canonical, analysis = sample()
        with tempfile.TemporaryDirectory() as tmp:
            _, written = write_video_projects(canonical, analysis, Path(tmp))
            video, props, first = written[0]
            second = first.with_name("second.mp4")
            first.write_bytes(b"old-first"); second.write_bytes(b"old-second")
            batch = [(video, props, first), (video, props, second)]

            def second_fails(command, **_kwargs):
                output = Path(command[command.index("--output") + 1])
                if "second" in output.name:
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="forced second failure")
                payload = b"\x00\x00\x00\x18ftypisom" + b"new-first"
                output.write_bytes(payload)
                summary = {"codec": "h264", "width": 1080, "height": 1920, "fps": 30,
                           "duration_in_frames": 2250, "rendered_frame_range": None,
                           "audio": "none", "file_size": len(payload),
                           "probe": {"codec": "h264", "width": 1080, "height": 1920, "audio_streams": 0, "duration_seconds": 75.0}}
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps(summary), stderr="")

            with self.assertRaisesRegex(RuntimeError, "previous MP4"):
                render_mp4s(batch, node="node", runner=second_fails)
            self.assertEqual(b"old-first", first.read_bytes())
            self.assertEqual(b"old-second", second.read_bytes())

    def test_installed_mp4_survives_backup_and_lock_cleanup_failures_with_precise_warnings(self):
        canonical, analysis = sample()
        with tempfile.TemporaryDirectory() as tmp:
            _, written = write_video_projects(canonical, analysis, Path(tmp))
            video, _props, target = written[0]
            target.write_bytes(b"old-complete-video")

            def success_runner(command, **_kwargs):
                output = Path(command[command.index("--output") + 1])
                payload = b"\x00\x00\x00\x18ftypisom" + b"new-complete-video"
                output.write_bytes(payload)
                summary = {"codec": "h264", "width": 1080, "height": 1920, "fps": 30,
                           "duration_in_frames": 2250, "rendered_frame_range": None, "audio": "none", "file_size": len(payload),
                           "probe": {"codec": "h264", "width": 1080, "height": 1920, "audio_streams": 0, "duration_seconds": 75.0}}
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps(summary), stderr="")

            original_unlink = Path.unlink
            def fail_cleanup_unlink(path, *args, **kwargs):
                if ".backup-" in path.name or path.name.endswith(".render.lock"):
                    raise PermissionError(f"{path.name} busy")
                return original_unlink(path, *args, **kwargs)

            with warnings.catch_warnings(record=True) as caught, mock.patch.object(Path, "unlink", fail_cleanup_unlink):
                warnings.simplefilter("always")
                summaries = render_mp4s(written, node="node", runner=success_runner)
            self.assertIn(b"new-complete-video", target.read_bytes())
            self.assertTrue(list(Path(tmp).glob(".*.backup-*.mp4")))
            self.assertTrue(any("backup" in warning for warning in summaries[0]["cleanup_warnings"]))
            self.assertTrue(target.with_name(f".{target.name}.render.lock").exists())
            self.assertTrue(any("output lock cleanup" in warning for warning in summaries[0]["cleanup_warnings"]))
            emitted = [str(item.message) for item in caught]
            self.assertTrue(any("backup cleanup" in warning for warning in emitted))
            self.assertTrue(any("output lock cleanup" in warning for warning in emitted))

    def test_output_lock_contention_fails_before_render_and_releases_acquired_locks(self):
        canonical, analysis = sample()
        with tempfile.TemporaryDirectory() as tmp:
            _, written = write_video_projects(canonical, analysis, Path(tmp))
            video, props, first = written[0]
            second = first.with_name("second.mp4")
            first.write_bytes(b"old-first"); second.write_bytes(b"old-second")
            second_lock = second.with_name(f".{second.name}.render.lock")
            second_lock.write_text("busy", encoding="utf-8")
            calls = []
            with self.assertRaisesRegex(RuntimeError, "output lock"):
                render_mp4s([(video, props, second), (video, props, first)], node="node", runner=lambda *args, **kwargs: calls.append(args))
            self.assertEqual([], calls)
            self.assertFalse(first.with_name(f".{first.name}.render.lock").exists())
            self.assertTrue(second_lock.exists())
            self.assertEqual(b"old-first", first.read_bytes())
            self.assertEqual(b"old-second", second.read_bytes())


if __name__ == "__main__":
    unittest.main()
