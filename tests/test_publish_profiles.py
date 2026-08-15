import copy
import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents/skills/xhs-question-solutions"
SCRIPTS = SKILL / "scripts"
PROFILES = SKILL / "references/platform-profiles.json"
sys.path.insert(0, str(SCRIPTS))

from check_publish_profile import (  # noqa: E402
    _ai_disclosure,
    _duration,
    _has_matching_first_frame_ai_label,
    evaluate_publish_profile,
    load_profile_catalog,
    serialize_publish_check,
    validate_profile_catalog,
)
from import_voiceover import build_voiceover_project, init_manifest  # noqa: E402
from normalize_xhs_export import normalize  # noqa: E402
from render_video import build_video_ir  # noqa: E402


def sample_video_ir(step_count=3):
    payload = json.loads((ROOT / "examples/sample-input.json").read_text(encoding="utf-8"))
    analysis = json.loads((ROOT / "examples/sample-analysis.json").read_text(encoding="utf-8"))
    steps = analysis["posts"][0]["solution"]["steps"]
    while len(steps) < step_count:
        step = copy.deepcopy(steps[-1])
        number = len(steps) + 1
        step["text"] = f"第 {number} 个完整动作"
        step["verification"] = f"第 {number} 个验证信号"
        step["stop_conditions"] = [f"第 {number} 个停止条件"]
        steps.append(step)
    return build_video_ir(normalize(payload), analysis)


def by_check(report, check_id):
    return next(item for item in report["videos"][0]["checks"] if item["check_id"] == check_id)


def voiced_video_ir(root, source, origins, label=None):
    if isinstance(origins, str):
        origins = [origins] * len(source["videos"])
    label = label or "-".join(origins)
    manifest_root = Path(root) / f"{label}-manifest"
    output_root = Path(root) / f"{label}-output"
    manifest = init_manifest(source)
    for manifest_video, video, origin in zip(manifest["videos"], source["videos"], origins):
        rights_basis = "self_recorded" if origin == "human_recorded" else "synthetic_service_terms_confirmed"
        manifest_video["origin"] = origin
        manifest_video["rights_basis"] = rights_basis
        for item, scene in zip(manifest_video["scenes"], video["scenes"]):
            samples = (scene["end_ms"] - scene["start_ms"]) * 48
            path = manifest_root / item["file"]
            path.parent.mkdir(parents=True, exist_ok=True)
            pair = struct.pack("<hh", 2048, -2048)
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(48_000)
                handle.writeframes((pair * math.ceil(samples / 2))[:samples * 2])
            for cue, caption in zip(item["cues"], scene["captions"]):
                cue["start_sample"] = (caption["startMs"] - scene["start_ms"]) * 48
                cue["end_sample"] = (caption["endMs"] - scene["start_ms"]) * 48
    return build_voiceover_project(
        source, manifest, manifest_root, output_root,
        confirmed_audio_reviewed=True, confirmed_audio_rights=True,
    ), output_root


class PlatformProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_profile_catalog(PROFILES)
        cls.ir75 = sample_video_ir(3)
        cls.ir90 = sample_video_ir(5)
        cls.voiceover_tmp = tempfile.TemporaryDirectory()
        cls.v2_human, cls.v2_human_root = voiced_video_ir(
            cls.voiceover_tmp.name, copy.deepcopy(cls.ir75), "human_recorded"
        )
        cls.v2_synthetic, cls.v2_synthetic_root = voiced_video_ir(
            cls.voiceover_tmp.name, copy.deepcopy(cls.ir75), "synthetic_ai"
        )
        mixed_source = copy.deepcopy(cls.ir75)
        second = copy.deepcopy(mixed_source["videos"][0])
        second["video_id"] += ":synthetic"
        second["note_id"] += ":synthetic"
        mixed_source["videos"].append(second)
        cls.v2_mixed, cls.v2_mixed_root = voiced_video_ir(
            cls.voiceover_tmp.name,
            mixed_source,
            ["human_recorded", "synthetic_ai"],
            label="mixed",
        )

    @classmethod
    def tearDownClass(cls):
        cls.voiceover_tmp.cleanup()

    def test_catalog_covers_requested_targets_and_traces_every_rule_to_dated_urls(self):
        requested = {
            "xhs_cn", "douyin_cn", "tiktok_organic", "tiktok_ads",
            "youtube_shorts", "instagram_reels", "instagram_boost", "cross_platform_master_60",
        }
        self.assertEqual(requested, set(self.catalog["profiles"]))
        for profile_id, profile in self.catalog["profiles"].items():
            with self.subTest(profile=profile_id):
                self.assertTrue(profile["applicability"])
                self.assertTrue(profile["sources"])
                source_ids = {source["source_id"] for source in profile["sources"]}
                for source in profile["sources"]:
                    self.assertRegex(source["url"], r"^https://")
                    self.assertRegex(source["checked_at"], r"^\d{4}-\d{2}-\d{2}$")
                    self.assertIn(source["authority"], {"official", "official_recommendation", "regulator", "project_policy"})
                    self.assertIn(source["evidence_status"], {"supports", "no_public_value", "conflicting", "project_policy"})
                for rule in profile["rules"].values():
                    self.assertTrue(set(rule["source_ids"]) <= source_ids)
        xhs_duration = self.catalog["profiles"]["xhs_cn"]["rules"]["duration"]
        self.assertEqual("unknown", xhs_duration["knowledge"])
        self.assertIsNone(xhs_duration["max_ms"])
        self.assertTrue(xhs_duration["manual_check"])

    def test_75_second_profiles_surface_eligibility_and_delivery_gates_separately(self):
        cross = evaluate_publish_profile(self.ir75, self.catalog, "cross_platform_master_60", ["assistive_text_only"])
        boost = evaluate_publish_profile(self.ir75, self.catalog, "instagram_boost", ["assistive_text_only"])
        shorts = evaluate_publish_profile(self.ir75, self.catalog, "youtube_shorts", ["assistive_text_only"])
        reels = evaluate_publish_profile(self.ir75, self.catalog, "instagram_reels", ["assistive_text_only"])

        self.assertEqual("blocked", by_check(cross, "duration")["status"])
        self.assertEqual("pass", by_check(boost, "duration")["status"])
        self.assertEqual("blocked", by_check(boost, "audio")["status"])
        self.assertEqual("blocked", boost["overall_status"])
        self.assertEqual("pass", by_check(shorts, "duration")["status"])
        self.assertEqual("pass", by_check(reels, "duration")["status"])
        self.assertEqual("needs_review", shorts["overall_status"])
        self.assertEqual("needs_review", reels["overall_status"])

    def test_90_second_instagram_boost_is_rejected_by_exclusive_duration_boundary(self):
        report = evaluate_publish_profile(self.ir90, self.catalog, "instagram_boost", ["assistive_text_only"])
        duration = by_check(report, "duration")
        self.assertEqual("blocked", duration["status"])
        self.assertEqual(90_000, duration["actual"]["duration_ms"])
        self.assertIn("< 90000 ms", duration["message"])

    def test_youtube_text_assistance_is_not_misreported_as_realistic_altered_media(self):
        text_only = evaluate_publish_profile(self.ir75, self.catalog, "youtube_shorts", ["assistive_text_only"])
        altered = evaluate_publish_profile(self.ir75, self.catalog, "youtube_shorts", ["realistic_altered"])
        text_check = by_check(text_only, "ai_disclosure")
        altered_check = by_check(altered, "ai_disclosure")
        self.assertEqual("pass", text_check["status"])
        self.assertFalse(text_check["actual"]["platform_disclosure_required"])
        self.assertNotIn("realistic altered", text_check["message"].lower())
        self.assertEqual("needs_review", altered_check["status"])
        self.assertTrue(altered_check["actual"]["platform_disclosure_required"])
        self.assertIn("YouTube Studio", altered_check["manual_actions"][0])

    def test_unknown_input_fields_and_ai_kinds_fail_closed_with_code_and_path(self):
        ir = copy.deepcopy(self.ir75)
        ir["surprise"] = True
        bad_ir = evaluate_publish_profile(ir, self.catalog, "youtube_shorts", ["assistive_text_only"])
        self.assertEqual("blocked", bad_ir["overall_status"])
        self.assertTrue(any(error["code"] == "UNKNOWN_FIELD" and error["path"] == "$" for error in bad_ir["errors"]))

        bad_kind = evaluate_publish_profile(self.ir75, self.catalog, "youtube_shorts", ["made_up"])
        self.assertEqual(
            {"code": "ENUM", "path": "$.ai_content_kinds", "message": "unsupported AI content kind: made_up"},
            bad_kind["errors"][0],
        )

    def test_unknown_profile_fields_fail_closed_with_code_and_path(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["profiles"]["youtube_shorts"]["rules"]["duration"]["invented_limit"] = 42
        report = evaluate_publish_profile(self.ir75, catalog, "youtube_shorts", ["assistive_text_only"])
        self.assertEqual("blocked", report["overall_status"])
        self.assertIn(
            {"code": "UNKNOWN_FIELD", "path": "$.profiles.youtube_shorts.rules.duration", "message": "invented_limit"},
            report["errors"],
        )

    def test_report_json_is_byte_deterministic(self):
        first = evaluate_publish_profile(self.ir75, self.catalog, "youtube_shorts", ["synthetic_audio", "synthetic_visual"])
        second = evaluate_publish_profile(copy.deepcopy(self.ir75), copy.deepcopy(self.catalog), "youtube_shorts", ["synthetic_visual", "synthetic_audio"])
        self.assertEqual(serialize_publish_check(first), serialize_publish_check(second))
        self.assertTrue(serialize_publish_check(first).endswith("\n"))

    def test_tiktok_reservation_profile_is_vertical_sound_on_and_format_specific(self):
        profile = self.catalog["profiles"]["tiktok_ads"]
        self.assertIn("Reservation In-Feed", profile["label"])
        self.assertIn("Non-Spark", profile["applicability"])
        self.assertEqual(["9:16"], profile["rules"]["aspect_ratio"]["allowed_exact"])
        self.assertEqual(True, profile["rules"]["audio"]["required"])
        self.assertEqual("hard", profile["rules"]["audio"]["enforcement"])
        report = evaluate_publish_profile(self.ir75, self.catalog, "tiktok_ads", ["none"])
        self.assertEqual("blocked", by_check(report, "audio")["status"])
        self.assertTrue(by_check(report, "audio")["manual_actions"])

    def test_douyin_fifteen_minutes_is_an_inclusive_boundary(self):
        rule = self.catalog["profiles"]["douyin_cn"]["rules"]["duration"]
        self.assertTrue(rule["max_inclusive"])
        self.assertEqual("pass", _duration({"duration_ms": 900_000}, rule)["status"])
        self.assertEqual("blocked", _duration({"duration_ms": 900_001}, rule)["status"])

    def test_china_profiles_require_matching_first_frame_labels_for_synthetic_media(self):
        for profile_id in ("xhs_cn", "douyin_cn"):
            with self.subTest(profile=profile_id):
                report = evaluate_publish_profile(self.ir75, self.catalog, profile_id, ["synthetic_visual"])
                check = by_check(report, "ai_disclosure")
                self.assertEqual("blocked", check["status"])
                self.assertIn("first frame", check["message"].lower())

    def test_ai_kinds_are_a_set_and_union_all_selected_obligations(self):
        report = evaluate_publish_profile(
            self.ir75, self.catalog, "youtube_shorts", ["synthetic_audio", "synthetic_visual"]
        )
        check = by_check(report, "ai_disclosure")
        self.assertEqual(["synthetic_visual", "synthetic_audio"], report["ai_content_kinds"])
        self.assertEqual(2, len(check["actual"]["obligations"]))
        self.assertEqual("needs_review", check["status"])

        for invalid in (["none", "synthetic_visual"], ["assistive_text_only", "synthetic_audio"]):
            with self.subTest(kinds=invalid):
                bad = evaluate_publish_profile(self.ir75, self.catalog, "youtube_shorts", invalid)
                self.assertEqual("blocked", bad["overall_status"])
                self.assertEqual("AI_KIND_CONFLICT", bad["errors"][0]["code"])

    def test_catalog_rejects_semantically_inconsistent_unknown_and_ai_rules(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["profiles"]["xhs_cn"]["rules"]["duration"]["enforcement"] = "hard"
        errors = validate_profile_catalog(catalog)
        self.assertTrue(any(error["code"] == "KNOWLEDGE_ENFORCEMENT" for error in errors))

        catalog = copy.deepcopy(self.catalog)
        item = catalog["profiles"]["youtube_shorts"]["rules"]["ai_disclosure"]["kinds"]["assistive_text_only"]
        item["required"] = True
        item["verification"] = "not_required"
        errors = validate_profile_catalog(catalog)
        self.assertTrue(any(error["code"] == "AI_SEMANTICS" for error in errors))

    def test_cli_emits_structured_invalid_input_instead_of_an_argparse_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "bad.json"
            output_path = Path(tmp) / "check.json"
            input_path.write_text('{"schema":"xhs-video/v1","videos":[],"extra":true}', encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "check_publish_profile.py"), str(input_path),
                 "--profiles", str(PROFILES), "--profile", "youtube_shorts",
                 "--ai-content-kind", "assistive_text_only", "--output", str(output_path)],
                text=True, encoding="utf-8", capture_output=True, check=False,
            )
            self.assertEqual(2, result.returncode)
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual("xhs-publish-check/v1", report["schema"])
            self.assertEqual("blocked", report["overall_status"])
            self.assertTrue(all({"code", "path", "message"} <= set(error) for error in report["errors"]))

    def test_cli_parses_repeated_and_comma_ai_kinds_and_needs_review_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "video.json"
            input_path.write_text(json.dumps(self.ir75, ensure_ascii=False), encoding="utf-8")
            command = [
                sys.executable, str(SCRIPTS / "check_publish_profile.py"), str(input_path),
                "--profiles", str(PROFILES), "--profile", "youtube_shorts",
                "--ai-content-kind", "synthetic_visual,synthetic_audio",
            ]
            result = subprocess.run(command, capture_output=True, check=False)
            self.assertEqual(3, result.returncode)
            report = json.loads(result.stdout.decode("utf-8"))
            self.assertEqual(["synthetic_visual", "synthetic_audio"], report["ai_content_kinds"])

            allowed = subprocess.run(command + ["--allow-needs-review"], capture_output=True, check=False)
            self.assertEqual(0, allowed.returncode)
            self.assertEqual("needs_review", json.loads(allowed.stdout.decode("utf-8"))["overall_status"])

    def test_nested_malformed_inputs_always_return_structured_exit_two(self):
        mutations = []
        unsafe_manifest = copy.deepcopy(self.ir75)
        unsafe_manifest["videos"][0]["unsafe_evidence_comment_ids"] = [{}]
        mutations.append(unsafe_manifest)
        evidence_ids = copy.deepcopy(self.ir75)
        evidence_ids["videos"][0]["scenes"][0]["evidence_comment_ids"] = [{}]
        mutations.append(evidence_ids)
        caption_text = copy.deepcopy(self.ir75)
        caption_text["videos"][0]["scenes"][0]["captions"][0]["text"] = 7
        mutations.append(caption_text)

        with tempfile.TemporaryDirectory() as tmp:
            for index, payload in enumerate(mutations):
                with self.subTest(index=index):
                    input_path = Path(tmp) / f"bad-{index}.json"
                    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                    result = subprocess.run(
                        [sys.executable, str(SCRIPTS / "check_publish_profile.py"), str(input_path),
                         "--profiles", str(PROFILES), "--profile", "youtube_shorts",
                         "--ai-content-kind", "none"],
                        capture_output=True, check=False,
                    )
                    self.assertEqual(2, result.returncode)
                    report = json.loads(result.stdout.decode("utf-8"))
                    self.assertEqual("blocked", report["overall_status"])
                    self.assertTrue(report["errors"])
                    self.assertNotIn(b"Traceback", result.stderr)

    def test_catalog_rejects_malformed_source_ids_and_non_finite_numbers(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["profiles"]["youtube_shorts"]["rules"]["duration"]["source_ids"] = [{}]
        errors = validate_profile_catalog(catalog)
        self.assertTrue(any(error["code"] == "SOURCE_REF" for error in errors))

        catalog = copy.deepcopy(self.catalog)
        catalog["profiles"]["youtube_shorts"]["rules"]["duration"]["max_ms"] = float("nan")
        errors = validate_profile_catalog(catalog)
        self.assertTrue(any(error["code"] == "FINITE" for error in errors))
        with self.assertRaises(ValueError):
            serialize_publish_check({"bad": float("nan")})

    def test_cli_rejects_json_nan_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "nan.json"
            input_path.write_text('{"schema":"xhs-video/v1","videos":[],"bad":NaN}', encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "check_publish_profile.py"), str(input_path),
                 "--profiles", str(PROFILES), "--profile", "youtube_shorts",
                 "--ai-content-kind", "none"],
                capture_output=True, check=False,
            )
            self.assertEqual(2, result.returncode)
            self.assertEqual("blocked", json.loads(result.stdout.decode("utf-8"))["overall_status"])
            self.assertNotIn(b"Traceback", result.stderr)

    def test_publish_ir_rejects_float_scalars_and_empty_ids_without_tracebacks(self):
        mutations = (
            ("width", 1080.0), ("height", 1920.0), ("fps", 30.0),
            ("duration_ms", 75_000.0), ("duration_in_frames", 2250.0),
            ("video_id", None), ("note_id", ""),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                ir = copy.deepcopy(self.ir75)
                ir["videos"][0][field] = value
                report = evaluate_publish_profile(ir, self.catalog, "youtube_shorts", ["none"])
                self.assertEqual("blocked", report["overall_status"])
                self.assertTrue(report["errors"])

        nested = copy.deepcopy(self.ir75)
        nested["videos"][0]["scenes"][0]["index"] = 1.0
        nested["videos"][0]["scenes"][0]["captions"][0]["startMs"] = False
        nested["videos"][0]["scenes"][0]["evidence_comment_ids"] = [""]
        report = evaluate_publish_profile(nested, self.catalog, "youtube_shorts", ["none"])
        self.assertEqual("blocked", report["overall_status"])
        self.assertGreaterEqual(len(report["errors"]), 3)

    def test_cli_float_dimensions_are_structured_invalid_exit_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = copy.deepcopy(self.ir75)
            payload["videos"][0]["width"] = 1080.0
            input_path = Path(tmp) / "float-width.json"
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "check_publish_profile.py"), str(input_path),
                 "--profiles", str(PROFILES), "--profile", "youtube_shorts", "--ai-content-kind", "none"],
                capture_output=True, check=False,
            )
            self.assertEqual(2, result.returncode)
            self.assertEqual("blocked", json.loads(result.stdout.decode("utf-8"))["overall_status"])
            self.assertNotIn(b"Traceback", result.stderr)

    def test_first_frame_ai_label_accepts_only_canonical_positive_labels(self):
        def video_with_label(text):
            return {"scenes": [{"start_ms": 0, "captions": [{"text": text, "startMs": 0, "endMs": 1000}], "persistent_notices": []}]}

        visual_pass = ("画面由AI生成", "非真人实拍，画面由AI生成")
        visual_fail = (
            "无AI生成画面", "未经AI生成画面", "不含AI生成画面", "画面未由AI生成",
            "不是画面由AI生成", "请勿误认这是画面由AI生成", "画面由AI生成吗",
        )
        for text in visual_pass:
            with self.subTest(kind="visual-pass", text=text):
                self.assertTrue(_has_matching_first_frame_ai_label(video_with_label(text), "synthetic_visual"))
        for text in visual_fail:
            with self.subTest(kind="visual-fail", text=text):
                self.assertFalse(_has_matching_first_frame_ai_label(video_with_label(text), "synthetic_visual"))

        for text in ("旁白由AI合成",):
            with self.subTest(kind="audio-pass", text=text):
                self.assertTrue(_has_matching_first_frame_ai_label(video_with_label(text), "synthetic_audio"))
        for text in ("无AI合成旁白", "旁白未由AI合成", "AI生成音频", "画面由AI生成"):
            with self.subTest(kind="audio-fail", text=text):
                self.assertFalse(_has_matching_first_frame_ai_label(video_with_label(text), "synthetic_audio"))

        self.assertTrue(_has_matching_first_frame_ai_label(video_with_label("画面经AI修改"), "realistic_altered"))
        self.assertFalse(_has_matching_first_frame_ai_label(video_with_label("画面未经AI修改"), "realistic_altered"))

    def test_china_ai_report_states_the_exact_expected_first_frame_label(self):
        report = evaluate_publish_profile(self.ir75, self.catalog, "xhs_cn", ["synthetic_visual"])
        check = by_check(report, "ai_disclosure")
        obligation = check["actual"]["obligations"][0]
        self.assertEqual("画面由AI生成", obligation["expected_label"])
        self.assertIn("画面由AI生成", check["message"])

    def test_china_profiles_accept_one_canonical_combined_label_for_all_selected_kinds(self):
        cases = (
            (["synthetic_visual", "synthetic_audio"], "画面由AI生成，旁白由AI合成"),
            (["synthetic_visual", "realistic_altered"], "画面由AI生成并经AI修改"),
            (["synthetic_audio", "realistic_altered"], "画面经AI修改，旁白由AI合成"),
            (["synthetic_visual", "synthetic_audio", "realistic_altered"], "画面由AI生成并经AI修改，旁白由AI合成"),
        )

        def with_first_frame_label(text, duplicate=False):
            labels = [{"text": text, "startMs": 0, "endMs": 1000}]
            if duplicate:
                labels.append({"text": text, "startMs": 0, "endMs": 1000})
            return {"scenes": [{"start_ms": 0, "captions": labels, "persistent_notices": []}]}

        for profile_id in ("xhs_cn", "douyin_cn"):
            for kinds, label in cases:
                with self.subTest(profile=profile_id, kinds=kinds):
                    rule = self.catalog["profiles"][profile_id]["rules"]["ai_disclosure"]
                    check = _ai_disclosure(with_first_frame_label(label), rule, kinds)
                    self.assertEqual("pass", check["status"])
                    self.assertEqual(set(kinds), set(check["actual"]["first_frame_declared_kinds"]))
                    self.assertEqual([label], check["actual"]["expected_first_frame_labels"])

        missing = _ai_disclosure(
            with_first_frame_label("画面由AI生成"),
            self.catalog["profiles"]["xhs_cn"]["rules"]["ai_disclosure"],
            ["synthetic_visual", "synthetic_audio"],
        )
        self.assertEqual("blocked", missing["status"])

        duplicate = _ai_disclosure(
            with_first_frame_label("画面由AI生成，旁白由AI合成", duplicate=True),
            self.catalog["profiles"]["douyin_cn"]["rules"]["ai_disclosure"],
            ["synthetic_visual", "synthetic_audio"],
        )
        self.assertEqual("blocked", duplicate["status"])

    def test_combined_first_frame_labels_reject_negative_questions_and_cross_media(self):
        def video_with_label(text):
            return {"scenes": [{"start_ms": 0, "captions": [{"text": text, "startMs": 0, "endMs": 1000}], "persistent_notices": []}]}

        invalid = (
            "画面未由AI生成，旁白由AI合成",
            "画面由AI生成，旁白不是AI合成",
            "画面由AI生成，旁白由AI合成吗",
            "画面由AI生成，音频由AI生成",
        )
        for text in invalid:
            with self.subTest(text=text):
                self.assertFalse(_has_matching_first_frame_ai_label(video_with_label(text), "synthetic_visual"))
                self.assertFalse(_has_matching_first_frame_ai_label(video_with_label(text), "synthetic_audio"))

    def test_official_sources_are_bound_to_profile_source_host_and_path(self):
        mutations = []
        catalog = copy.deepcopy(self.catalog)
        catalog["profiles"]["youtube_shorts"]["sources"][0]["url"] = (
            "https://support.tiktok.com/en/using-tiktok/creating-videos/creator-tools-on-tiktok"
        )
        mutations.append(catalog)

        catalog = copy.deepcopy(self.catalog)
        catalog["profiles"]["youtube_shorts"]["sources"][0]["url"] = (
            "https://support.google.com/accounts/answer/15424877"
        )
        mutations.append(catalog)

        catalog = copy.deepcopy(self.catalog)
        catalog["profiles"]["instagram_reels"]["sources"][0]["url"] = (
            "https://www.facebook.com/random-user/posts/1038071743007909"
        )
        mutations.append(catalog)

        for index, catalog in enumerate(mutations):
            with self.subTest(mutation=index):
                self.assertTrue(any(error["code"] == "SOURCE_POLICY" for error in validate_profile_catalog(catalog)))

    def test_bundled_source_paths_are_exact_and_queries_follow_source_policy(self):
        invalid_paths = (
            "/youtube/answer/154248770",
            "/youtube/answer/15424877/extra",
            "/youtube/answer/15424877-user",
        )
        for path in invalid_paths:
            with self.subTest(path=path):
                catalog = copy.deepcopy(self.catalog)
                catalog["profiles"]["youtube_shorts"]["sources"][0]["url"] = f"https://support.google.com{path}"
                self.assertTrue(any(error["code"] == "SOURCE_POLICY" for error in validate_profile_catalog(catalog)))

        catalog = copy.deepcopy(self.catalog)
        catalog["profiles"]["youtube_shorts"]["sources"][0]["url"] = (
            "https://support.google.com/youtube/answer/15424877?hl=zh-CN"
        )
        self.assertFalse(any(error["code"] == "SOURCE_POLICY" for error in validate_profile_catalog(catalog)))

        catalog = copy.deepcopy(self.catalog)
        catalog["profiles"]["youtube_shorts"]["sources"][0]["url"] = (
            "https://support.google.com/youtube/answer/15424877?id=1"
        )
        self.assertTrue(any(error["code"] == "SOURCE_POLICY" for error in validate_profile_catalog(catalog)))

    def test_douyin_agreement_requires_the_exact_content_identifying_query(self):
        original = copy.deepcopy(self.catalog)
        self.assertFalse(any(error["code"] == "SOURCE_POLICY" for error in validate_profile_catalog(original)))

        invalid_urls = (
            "https://www.douyin.com/agreements/",
            "https://www.douyin.com/agreements/?id=1",
            "https://www.douyin.com/agreements/?id=6773906068725565448&x=1",
        )
        for url in invalid_urls:
            with self.subTest(url=url):
                catalog = copy.deepcopy(self.catalog)
                source = next(
                    item for item in catalog["profiles"]["douyin_cn"]["sources"]
                    if item["source_id"] == "douyin_user_agreement"
                )
                source["url"] = url
                self.assertTrue(any(error["code"] == "SOURCE_POLICY" for error in validate_profile_catalog(catalog)))

        catalog = copy.deepcopy(self.catalog)
        catalog["profiles"]["instagram_reels"]["sources"][0]["url"] += "?user=random"
        self.assertTrue(any(error["code"] == "SOURCE_POLICY" for error in validate_profile_catalog(catalog)))

    def test_china_first_frame_declaration_must_exactly_equal_required_kinds(self):
        def video_with_label(text):
            return {"scenes": [{"start_ms": 0, "captions": [{"text": text, "startMs": 0, "endMs": 1000}], "persistent_notices": []}]}

        cases = (
            (["synthetic_visual"], "画面由AI生成，旁白由AI合成", {"synthetic_visual", "synthetic_audio"}),
            (["synthetic_visual", "synthetic_audio"], "画面由AI生成并经AI修改，旁白由AI合成", {"synthetic_visual", "synthetic_audio", "realistic_altered"}),
        )
        for profile_id in ("xhs_cn", "douyin_cn"):
            rule = self.catalog["profiles"][profile_id]["rules"]["ai_disclosure"]
            for required, label, declared in cases:
                with self.subTest(profile=profile_id, required=required):
                    check = _ai_disclosure(video_with_label(label), rule, required)
                    self.assertEqual("blocked", check["status"])
                    self.assertEqual(declared, set(check["actual"]["first_frame_declared_kinds"]))
                    self.assertNotEqual(
                        set(required), set(check["actual"]["first_frame_declared_kinds"]),
                    )

    def test_platform_disclosure_required_preserves_unknown_as_null(self):
        visual = evaluate_publish_profile(self.ir75, self.catalog, "youtube_shorts", ["synthetic_visual"])
        combined = evaluate_publish_profile(
            self.ir75, self.catalog, "youtube_shorts", ["synthetic_visual", "synthetic_audio"]
        )
        required = evaluate_publish_profile(
            self.ir75, self.catalog, "youtube_shorts", ["synthetic_visual", "realistic_altered"]
        )
        self.assertIsNone(by_check(visual, "ai_disclosure")["actual"]["platform_disclosure_required"])
        self.assertIsNone(by_check(combined, "ai_disclosure")["actual"]["platform_disclosure_required"])
        self.assertTrue(by_check(required, "ai_disclosure")["actual"]["platform_disclosure_required"])
        self.assertTrue(by_check(required, "ai_disclosure")["actual"]["determination_pending"])

    def test_catalog_rejects_authority_evidence_spoofing_unknown_support_and_invalid_ranges(self):
        mutations = []
        catalog = copy.deepcopy(self.catalog)
        source = catalog["profiles"]["cross_platform_master_60"]["sources"][0]
        source["evidence_status"] = "supports"
        mutations.append((catalog, "AUTHORITY_EVIDENCE"))

        catalog = copy.deepcopy(self.catalog)
        source = catalog["profiles"]["youtube_shorts"]["sources"][0]
        source["url"] = "https://example.invalid/fake-official"
        mutations.append((catalog, "OFFICIAL_DOMAIN"))

        catalog = copy.deepcopy(self.catalog)
        profile = catalog["profiles"]["youtube_shorts"]
        profile["rules"]["fps"]["source_ids"] = ["youtube_three_minute_shorts"]
        mutations.append((catalog, "UNKNOWN_EVIDENCE"))

        catalog = copy.deepcopy(self.catalog)
        duration = catalog["profiles"]["tiktok_ads"]["rules"]["duration"]
        duration["min_ms"], duration["max_ms"] = 60_000, 5_000
        mutations.append((catalog, "RANGE_ORDER"))

        for catalog, code in mutations:
            with self.subTest(code=code):
                self.assertTrue(any(error["code"] == code for error in validate_profile_catalog(catalog)))

    def test_tiktok_official_audio_failure_is_not_called_a_project_gate(self):
        report = evaluate_publish_profile(self.ir75, self.catalog, "tiktok_ads", ["none"])
        message = by_check(report, "audio")["message"].lower()
        self.assertIn("official hard rule", message)
        self.assertNotIn("project gate", message)

    def test_v2_schema_dispatch_uses_the_final_validator_and_preserves_v1(self):
        v1 = evaluate_publish_profile(self.ir75, self.catalog, "youtube_shorts", ["none"])
        human = evaluate_publish_profile(self.v2_human, self.catalog, "youtube_shorts", ["none"])
        malformed = copy.deepcopy(self.v2_human)
        malformed["videos"][0]["scenes"][0]["audio"]["codec"] = "mp3"
        rejected = evaluate_publish_profile(malformed, self.catalog, "youtube_shorts", ["none"])

        self.assertFalse(v1["errors"])
        self.assertFalse(human["errors"])
        self.assertEqual("xhs-video/v2", self.v2_human["schema"])
        self.assertTrue(rejected["errors"])
        self.assertEqual("blocked", rejected["overall_status"])

    def test_v2_ai_kind_must_match_the_audio_origin_but_visual_kinds_remain_explicit(self):
        cases = (
            (self.v2_synthetic, ["none"], True),
            (self.v2_synthetic, ["synthetic_visual"], True),
            (self.v2_human, ["synthetic_audio"], True),
            (self.v2_human, ["synthetic_visual"], False),
            (self.v2_human, ["realistic_altered"], False),
            (self.v2_synthetic, ["synthetic_audio", "synthetic_visual"], False),
            (self.v2_synthetic, ["synthetic_audio", "realistic_altered"], False),
        )
        for ir, kinds, invalid in cases:
            with self.subTest(origin=ir["videos"][0]["meta"]["audio"]["origin"], kinds=kinds):
                report = evaluate_publish_profile(ir, self.catalog, "xhs_cn", kinds)
                errors = [item for item in report["errors"] if item["code"] == "AI_KIND_DERIVATION_MISMATCH"]
                self.assertEqual(invalid, bool(errors))

    def test_v2_mixed_origins_reconcile_global_audio_union_and_per_video_effective_kinds(self):
        audio_only = evaluate_publish_profile(
            self.v2_mixed, self.catalog, "youtube_shorts", ["synthetic_audio"]
        )
        self.assertFalse(audio_only["errors"])
        self.assertEqual(["synthetic_audio"], audio_only["derived_ai_kinds"])
        self.assertEqual([], audio_only["videos"][0]["derived_ai_kinds"])
        self.assertEqual([], audio_only["videos"][0]["effective_ai_kinds"])
        self.assertEqual(["synthetic_audio"], audio_only["videos"][1]["derived_ai_kinds"])
        self.assertEqual(["synthetic_audio"], audio_only["videos"][1]["effective_ai_kinds"])

        with_visual = evaluate_publish_profile(
            self.v2_mixed,
            self.catalog,
            "youtube_shorts",
            ["synthetic_audio", "synthetic_visual"],
        )
        self.assertFalse(with_visual["errors"])
        self.assertEqual(["synthetic_visual"], with_visual["videos"][0]["effective_ai_kinds"])
        self.assertEqual(
            ["synthetic_visual", "synthetic_audio"],
            with_visual["videos"][1]["effective_ai_kinds"],
        )

        missing_union = evaluate_publish_profile(
            self.v2_mixed, self.catalog, "youtube_shorts", ["none"]
        )
        self.assertEqual("AI_KIND_DERIVATION_MISMATCH", missing_union["errors"][0]["code"])

    def test_v2_origin_rights_and_attestation_tampering_is_structured_invalid(self):
        mutations = (
            lambda ir: ir["videos"][0]["meta"]["audio"].__setitem__("origin", "unknown"),
            lambda ir: ir["videos"][0]["meta"]["audio"].__setitem__("rights_basis", "public_domain"),
            lambda ir: ir["videos"][0]["meta"]["audio"]["attestation"].__setitem__("license_verified_by_tool", True),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                ir = copy.deepcopy(self.v2_human)
                mutate(ir)
                report = evaluate_publish_profile(ir, self.catalog, "youtube_shorts", ["none"])
                self.assertEqual("blocked", report["overall_status"])
                self.assertTrue(report["errors"])
                self.assertTrue(all(set(error) == {"code", "path", "message"} for error in report["errors"]))

    def test_v2_synthetic_hook_notice_is_the_canonical_audio_label_only(self):
        audio_only = evaluate_publish_profile(self.v2_synthetic, self.catalog, "xhs_cn", ["synthetic_audio"])
        combined_visual = evaluate_publish_profile(
            self.v2_synthetic, self.catalog, "xhs_cn", ["synthetic_visual", "synthetic_audio"]
        )
        combined_altered = evaluate_publish_profile(
            self.v2_synthetic, self.catalog, "xhs_cn", ["synthetic_audio", "realistic_altered"]
        )
        audio_check = by_check(audio_only, "ai_disclosure")
        combined_visual_check = by_check(combined_visual, "ai_disclosure")
        combined_altered_check = by_check(combined_altered, "ai_disclosure")

        self.assertEqual("pass", audio_check["status"])
        self.assertTrue(_has_matching_first_frame_ai_label(self.v2_synthetic["videos"][0], "synthetic_audio"))
        self.assertEqual(["synthetic_audio"], audio_check["actual"]["first_frame_declared_kinds"])
        self.assertEqual("structured_notice", audio_check["actual"]["first_frame_declaration_source"])
        self.assertEqual(["旁白由AI合成"], audio_check["actual"]["expected_first_frame_labels"])
        self.assertEqual("blocked", combined_visual_check["status"])
        self.assertIn("画面由AI生成，旁白由AI合成", combined_visual_check["actual"]["expected_first_frame_labels"])
        self.assertEqual("blocked", combined_altered_check["status"])
        self.assertIn("画面经AI修改，旁白由AI合成", combined_altered_check["actual"]["expected_first_frame_labels"])

        video = copy.deepcopy(self.v2_synthetic["videos"][0])
        hook = next(scene for scene in video["scenes"] if scene["role"] == "hook")
        hook["persistent_notices"].remove("synthetic_audio")
        rule = self.catalog["profiles"]["xhs_cn"]["rules"]["ai_disclosure"]
        late_only = _ai_disclosure(video, rule, ["synthetic_audio"])
        self.assertEqual("blocked", late_only["status"])

    def test_v2_structured_first_frame_label_conflicts_with_any_caption_ai_declaration(self):
        rule = self.catalog["profiles"]["xhs_cn"]["rules"]["ai_disclosure"]
        unique = _ai_disclosure(
            self.v2_synthetic["videos"][0], rule, ["synthetic_audio"], ["synthetic_audio"]
        )
        self.assertEqual("pass", unique["status"])
        self.assertEqual(1, unique["actual"]["first_frame_label_count"])
        self.assertEqual(["structured_notice"], unique["actual"]["first_frame_label_sources"])
        self.assertFalse(unique["actual"]["first_frame_label_conflict"])

        for text in (
            "旁白由AI合成",
            "旁白不是由AI合成",
            "旁白由AI合成吗？",
            "这不是人工智能合成旁白",
            "这不是AI配音",
            "无AI旁白",
            "非AI旁白",
            "AI旁白",
            "旁白来自AI",
            "旁白非AI制作",
            "这不是AI声音",
            "ａｉ语音",
        ):
            with self.subTest(text=text):
                video = copy.deepcopy(self.v2_synthetic["videos"][0])
                hook = next(scene for scene in video["scenes"] if scene["role"] == "hook")
                hook["captions"].append({
                    "text": text,
                    "startMs": 0,
                    "endMs": 1000,
                    "timestampMs": None,
                    "confidence": None,
                })
                check = _ai_disclosure(video, rule, ["synthetic_audio"], ["synthetic_audio"])
                self.assertEqual("blocked", check["status"])
                self.assertEqual(2, check["actual"]["first_frame_label_count"])
                self.assertEqual(
                    {"caption", "structured_notice"},
                    set(check["actual"]["first_frame_label_sources"]),
                )
                self.assertTrue(check["actual"]["first_frame_label_conflict"])
                self.assertIn("conflict", check["message"].lower())

    def test_v2_api_distinguishes_declared_audio_from_verified_asset_bytes(self):
        declared = evaluate_publish_profile(
            self.v2_human, self.catalog, "instagram_boost", ["none"]
        )
        declared_audio = by_check(declared, "audio")["actual"]
        self.assertTrue(declared_audio["declared_audio_clips"])
        self.assertEqual("basic_pcm_activity", declared_audio["declared_basic_signal_check"])
        self.assertIsNone(declared_audio["asset_bytes_verified"])
        self.assertIsNone(declared_audio["technical_audio_presence"])
        self.assertIsNone(declared_audio["basic_signal_verified"])
        self.assertIn("declares", by_check(declared, "audio")["message"].lower())
        self.assertNotIn("technical audio is present", by_check(declared, "audio")["message"].lower())

        verified = evaluate_publish_profile(
            self.v2_human,
            self.catalog,
            "instagram_boost",
            ["none"],
            asset_root=self.v2_human_root,
        )
        verified_audio = by_check(verified, "audio")["actual"]
        self.assertTrue(verified_audio["asset_bytes_verified"])
        self.assertTrue(verified_audio["technical_audio_presence"])
        self.assertTrue(verified_audio["basic_signal_verified"])
        self.assertEqual(len(self.v2_human["videos"][0]["scenes"]), verified_audio["verified_asset_count"])

    def test_v2_audio_presence_stays_manual_for_official_sound_and_project_rights_gates(self):
        cases = (
            ("tiktok_ads", "official sound requirement"),
            ("instagram_boost", "project delivery gate"),
        )
        for profile_id, wording in cases:
            with self.subTest(profile=profile_id):
                report = evaluate_publish_profile(self.v2_human, self.catalog, profile_id, ["none"])
                check = by_check(report, "audio")
                self.assertEqual("needs_review", check["status"])
                self.assertIsNone(check["actual"]["technical_audio_presence"])
                self.assertIsNone(check["actual"]["asset_bytes_verified"])
                self.assertTrue(check["actual"]["manual_audibility_review"])
                self.assertTrue(check["actual"]["rights_manual_review"])
                self.assertFalse(check["actual"]["audibility_verified"])
                self.assertFalse(check["actual"]["license_verified_by_tool"])
                self.assertIn(wording, check["message"].lower())
                self.assertEqual([], report["videos"][0]["derived_ai_kinds"])

        silent = evaluate_publish_profile(self.ir75, self.catalog, "tiktok_ads", ["none"])
        self.assertEqual("blocked", by_check(silent, "audio")["status"])

    def test_v2_synthetic_report_and_cli_preserve_manual_review_and_exit_codes(self):
        report = evaluate_publish_profile(
            self.v2_synthetic, self.catalog, "tiktok_ads", ["synthetic_audio"]
        )
        self.assertEqual(["synthetic_audio"], report["derived_ai_kinds"])
        self.assertEqual(["synthetic_audio"], report["videos"][0]["derived_ai_kinds"])
        self.assertEqual("needs_review", by_check(report, "audio")["status"])

        input_path = self.v2_human_root / "video-projects.json"
        base = [
            sys.executable, str(SCRIPTS / "check_publish_profile.py"), str(input_path),
            "--profile", "instagram_boost", "--ai-content-kind", "none",
        ]
        needs_review = subprocess.run(base, text=True, encoding="utf-8", capture_output=True, check=False)
        allowed = subprocess.run(base + ["--allow-needs-review"], text=True, encoding="utf-8", capture_output=True, check=False)
        self.assertEqual(3, needs_review.returncode, needs_review.stdout + needs_review.stderr)
        self.assertEqual(0, allowed.returncode, allowed.stdout + allowed.stderr)
        self.assertNotIn("Traceback", needs_review.stdout + needs_review.stderr)
        cli_audio = by_check(json.loads(needs_review.stdout), "audio")["actual"]
        self.assertTrue(cli_audio["asset_bytes_verified"])
        self.assertTrue(cli_audio["basic_signal_verified"])

        with tempfile.TemporaryDirectory() as directory:
            copied_root = Path(directory) / "copied-output"
            shutil.copytree(self.v2_human_root, copied_root)
            copied_input = copied_root / "video-projects.json"
            tampered = copy.deepcopy(self.v2_human)
            tampered["videos"][0]["meta"]["audio"]["origin"] = "unknown"
            copied_input.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
            invalid_base = [
                sys.executable, str(SCRIPTS / "check_publish_profile.py"), str(copied_input),
                "--profile", "instagram_boost", "--ai-content-kind", "none",
            ]
            invalid = subprocess.run(invalid_base, text=True, encoding="utf-8", capture_output=True, check=False)
            self.assertEqual(2, invalid.returncode, invalid.stdout + invalid.stderr)
            self.assertTrue(json.loads(invalid.stdout)["errors"])
            self.assertNotIn("Traceback", invalid.stdout + invalid.stderr)

        omitted_base = [
            sys.executable, str(SCRIPTS / "check_publish_profile.py"),
            str(self.v2_synthetic_root / "video-projects.json"),
            "--profile", "instagram_boost", "--ai-content-kind", "none",
        ]
        omitted = subprocess.run(omitted_base, text=True, encoding="utf-8", capture_output=True, check=False)
        self.assertEqual(2, omitted.returncode, omitted.stdout + omitted.stderr)
        self.assertEqual("AI_KIND_DERIVATION_MISMATCH", json.loads(omitted.stdout)["errors"][0]["code"])
        self.assertNotIn("Traceback", omitted.stdout + omitted.stderr)

    def test_v2_cli_fails_closed_when_wav_is_missing_tampered_or_malformed(self):
        first_relative = self.v2_human["videos"][0]["scenes"][0]["audio"]["path"]
        for mutation in ("missing", "tampered", "malformed"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                copied_root = Path(directory) / "copied-output"
                shutil.copytree(self.v2_human_root, copied_root)
                asset = copied_root / Path(first_relative)
                if mutation == "missing":
                    asset.unlink()
                elif mutation == "tampered":
                    payload = bytearray(asset.read_bytes())
                    payload[-1] ^= 1
                    asset.write_bytes(payload)
                else:
                    asset.write_bytes(b"not a WAV")
                result = subprocess.run(
                    [sys.executable, str(SCRIPTS / "check_publish_profile.py"),
                     str(copied_root / "video-projects.json"), "--profile", "instagram_boost",
                     "--ai-content-kind", "none"],
                    text=True, encoding="utf-8", capture_output=True, check=False,
                )
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                report = json.loads(result.stdout)
                self.assertTrue(report["errors"])
                self.assertTrue(all(set(error) == {"code", "path", "message"} for error in report["errors"]))
                self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_v2_asset_verifier_enforces_producer_pcm_size_and_signal_gates(self):
        first_relative = self.v2_human["videos"][0]["scenes"][0]["audio"]["path"]
        cases = (
            ("wrong_rate", "VOICEOVER_WAV_FORMAT"),
            ("stereo", "VOICEOVER_WAV_FORMAT"),
            ("silent", "VOICEOVER_WAV_SIGNAL"),
            ("oversized", "VOICEOVER_WAV_TOO_LARGE"),
        )
        for mutation, expected_code in cases:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                copied_root = Path(directory) / "copied-output"
                shutil.copytree(self.v2_human_root, copied_root)
                asset = copied_root / Path(first_relative)
                with wave.open(str(asset), "rb") as handle:
                    frames = handle.readframes(handle.getnframes())
                if mutation == "oversized":
                    asset.write_bytes(b"\0" * 9_000_001)
                else:
                    with wave.open(str(asset), "wb") as handle:
                        handle.setnchannels(2 if mutation == "stereo" else 1)
                        handle.setsampwidth(2)
                        handle.setframerate(44_100 if mutation == "wrong_rate" else 48_000)
                        handle.writeframes(b"\0" * len(frames) if mutation == "silent" else frames)
                result = subprocess.run(
                    [sys.executable, str(SCRIPTS / "check_publish_profile.py"),
                     str(copied_root / "video-projects.json"), "--profile", "instagram_boost",
                     "--ai-content-kind", "none"],
                    text=True, encoding="utf-8", capture_output=True, check=False,
                )
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(expected_code, report["errors"][0]["code"])
                self.assertNotIn("Traceback", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
