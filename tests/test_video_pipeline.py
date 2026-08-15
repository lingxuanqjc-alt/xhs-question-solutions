import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
