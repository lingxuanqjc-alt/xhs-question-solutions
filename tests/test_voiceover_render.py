import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents/skills/xhs-question-solutions"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

from import_voiceover import build_voiceover_project  # noqa: E402
from render_video import render_mp4s, validate_video_ir  # noqa: E402
from tests.test_voiceover_import import ready_manifest, source_ir, write_wav  # noqa: E402
from tests.test_video_pipeline import build_video_ir, sample  # noqa: E402


class VoiceoverRenderTests(unittest.TestCase):
    AI_AUDIO_LABEL_MUTATIONS = (
        "无AI旁白", "非AI旁白", "AI旁白", "旁白来自AI", "旁白非AI制作", "这不是AI声音",
        "AI配音", "人工智能音频", "ａｉ语音",
    )

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.source = source_ir()
        cls.manifest_dir = cls.root / "manifest"
        cls.manifest = ready_manifest(cls.manifest_dir, cls.source, origin="synthetic_ai")
        cls.project_dir = cls.root / "project"
        cls.project = build_voiceover_project(
            cls.source, cls.manifest, cls.manifest_dir, cls.project_dir,
            confirmed_audio_reviewed=True, confirmed_audio_rights=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_python_dispatch_accepts_v2_and_rejects_v2_binding_mutations(self):
        self.assertEqual([], validate_video_ir(copy.deepcopy(self.project)))
        mutations = (
            ("frame timeline", lambda ir: ir["videos"][0]["scenes"][1].__setitem__("start_frame", 1)),
            ("audio hash order", lambda ir: ir["videos"][0]["meta"]["audio"]["attestation"]["audio_sha256"].reverse()),
            ("attestation digest", lambda ir: ir["videos"][0]["meta"]["audio"]["attestation"].__setitem__("sha256", "sha256:" + "0" * 64)),
            ("synthetic notice", lambda ir: next(s for s in ir["videos"][0]["scenes"] if s["role"] == "hook")["persistent_notices"].remove("synthetic_audio")),
            ("unsafe notice", lambda ir: next(s for s in ir["videos"][0]["scenes"] if "unsafe_unverified_not_advice" in s["persistent_notices"])["persistent_notices"].remove("unsafe_unverified_not_advice")),
            ("scene audio metadata", lambda ir: ir["videos"][0]["scenes"][0]["audio"].__setitem__("sample_rate_hz", 44_100)),
            ("nonhashable rights basis", lambda ir: ir["videos"][0]["meta"]["audio"].__setitem__("rights_basis", [])),
        )
        for label, mutate in mutations:
            with self.subTest(case=label):
                value = copy.deepcopy(self.project)
                mutate(value)
                self.assertTrue(validate_video_ir(value), label)

    def test_v2_validator_rejects_nonstring_roles_and_mapping_keys_without_raw_exceptions(self):
        mutations = (
            ("object role", lambda ir: ir["videos"][0]["scenes"][0].__setitem__("role", {})),
            ("boolean source", lambda ir: ir.__setitem__("source", True)),
            ("list note ID", lambda ir: ir["videos"][0].__setitem__("note_id", [])),
            ("object note ID", lambda ir: ir["videos"][0].__setitem__("note_id", {})),
            ("non-string root key", lambda ir: ir.__setitem__(7, "unexpected")),
            ("non-string audio key", lambda ir: ir["videos"][0]["meta"]["audio"].__setitem__(7, "unexpected")),
        )
        for label, mutate in mutations:
            with self.subTest(case=label):
                value = copy.deepcopy(self.project)
                mutate(value)
                errors = validate_video_ir(value)
                self.assertTrue(errors, label)
                self.assertTrue(any(error.startswith(("TYPE ", "ROLE ", "SHAPE ")) for error in errors), errors)

    def test_standalone_v1_rejects_common_nested_nulls_without_raw_exceptions(self):
        canonical, analysis = sample()
        mutations = (
            lambda video: video["meta"].__setitem__("candidate_count", None),
            lambda video: video["appendix"]["evidence"][0].__setitem__("author", None),
            lambda video: video["scenes"][0]["content"].__setitem__("social_title", None),
            lambda video: video["scenes"][2]["content"].__setitem__("applies_when", None),
            lambda video: next(scene for scene in video["scenes"] if scene["role"] == "conflict_risk")["content"]["conflicts"][0].__setitem__("positions", None),
        )
        for mutate in mutations:
            video = copy.deepcopy(build_video_ir(canonical, analysis)["videos"][0])
            mutate(video)
            self.assertTrue(validate_video_ir({"schema": "xhs-video/v1", "videos": [video]}))

    def test_node_rehashes_assets_before_browser_start(self):
        project = self.root / "tampered"
        shutil.copytree(self.project_dir, project)
        props = next(project.glob("*.props.json"))
        payload = json.loads(props.read_text(encoding="utf-8"))
        asset = project / payload["video"]["scenes"][0]["audio"]["path"]
        raw = bytearray(asset.read_bytes())
        raw[-1] ^= 1
        asset.write_bytes(raw)
        result = subprocess.run(
            ["node", str(SCRIPTS / "render_video.mjs"), "--props", str(props), "--output", str(project / "out.mp4")],
            text=True, encoding="utf-8", capture_output=True, check=False,
        )
        self.assertEqual(3, result.returncode, result.stderr + result.stdout)
        self.assertIn("audio hash", (result.stderr + result.stdout).lower())
        self.assertNotIn("browser", (result.stderr + result.stdout).lower())

    def test_node_v2_mutations_fail_closed_before_browser_start(self):
        mutations = (
            ("attestation", lambda payload, root: payload["video"]["meta"]["audio"]["attestation"].__setitem__("sha256", "sha256:" + "0" * 64)),
            ("synthetic_audio notice", lambda payload, root: next(s for s in payload["video"]["scenes"] if s["role"] == "hook")["persistent_notices"].remove("synthetic_audio")),
            ("path", lambda payload, root: payload["video"]["scenes"][0]["audio"].__setitem__("path", "../outside.wav")),
            ("metadata", lambda payload, root: payload["video"]["scenes"][0]["audio"].__setitem__("sample_count", 1)),
            ("near-silence", self._silence_first_asset),
        )
        for label, mutate in mutations:
            with self.subTest(case=label):
                project = self.root / f"mutation-{label.replace(' ', '-')}"
                shutil.copytree(self.project_dir, project)
                props = next(project.glob("*.props.json"))
                payload = json.loads(props.read_text(encoding="utf-8"))
                mutate(payload, project)
                props.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                result = subprocess.run(
                    ["node", str(SCRIPTS / "render_video.mjs"), "--props", str(props), "--output", str(project / "out.mp4")],
                    text=True, encoding="utf-8", capture_output=True, check=False,
                )
                self.assertEqual(3, result.returncode, result.stderr + result.stdout)
                self.assertNotIn("TypeError", result.stderr + result.stdout)
                self.assertNotIn("No local Chromium", result.stderr + result.stdout)

    def test_node_v1_nested_null_is_structured_not_typeerror(self):
        canonical, analysis = sample()
        video = copy.deepcopy(build_video_ir(canonical, analysis)["videos"][0])
        next(scene for scene in video["scenes"] if scene["role"] == "conflict_risk")["content"]["conflicts"][0]["positions"] = None
        with tempfile.TemporaryDirectory() as tmp:
            props = Path(tmp) / "invalid.props.json"
            props.write_text(json.dumps({"schema": "xhs-video/v1", "video": video}, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(["node", str(SCRIPTS / "render_video.mjs"), "--props", str(props), "--output", str(Path(tmp) / "out.mp4")], text=True, encoding="utf-8", capture_output=True, check=False)
            self.assertEqual(3, result.returncode, result.stderr + result.stdout)
            self.assertNotIn("TypeError", result.stderr + result.stdout)

    @staticmethod
    def _silence_first_asset(payload, root):
        scene = payload["video"]["scenes"][0]
        write_wav(root / scene["audio"]["path"], scene["audio"]["sample_count"], amplitude=0)

    def test_real_three_frame_v2_render_has_h264_and_aac(self):
        output = self.root / "three-frames.mp4"
        result = subprocess.run(
            ["node", str(SCRIPTS / "render_video.mjs"), "--props", str(next(self.project_dir.glob("*.props.json"))),
             "--output", str(output), "--frame-range", "0:2", "--browser", "C:/Program Files/Google/Chrome/Application/chrome.exe"],
            text=True, encoding="utf-8", capture_output=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        summary = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual("aac", summary["audio"])
        self.assertEqual((1, "aac", 48_000, 1), (summary["probe"]["audio_streams"], summary["probe"]["audio_codec"], summary["probe"]["audio_sample_rate"], summary["probe"]["audio_channels"]))

    def test_invalid_v2_asset_keeps_existing_mp4(self):
        project = self.root / "transaction"
        shutil.copytree(self.project_dir, project)
        props = next(project.glob("*.props.json"))
        payload = json.loads(props.read_text(encoding="utf-8"))
        asset = project / payload["video"]["scenes"][0]["audio"]["path"]
        raw = bytearray(asset.read_bytes()); raw[-1] ^= 1; asset.write_bytes(raw)
        target = project / "existing.mp4"; target.write_bytes(b"old-complete-video")
        with self.assertRaisesRegex(RuntimeError, "previous MP4 is unchanged"):
            render_mp4s([(payload["video"], props, target)], node="node", browser="C:/Program Files/Google/Chrome/Application/chrome.exe", frame_range=(0, 2))
        self.assertEqual(b"old-complete-video", target.read_bytes())

    def test_render_mp4s_accepts_v2_audio_probe_atomically(self):
        target = self.root / "mock-v2.mp4"
        target.write_bytes(b"old-complete-video")
        video = self.project["videos"][0]
        props = next(self.project_dir.glob("*.props.json"))

        def runner(command, **_kwargs):
            output = Path(command[command.index("--output") + 1])
            payload = b"\x00\x00\x00\x18ftypisom" + b"voiced-video"
            output.write_bytes(payload)
            summary = {"codec": "h264", "width": 1080, "height": 1920, "fps": 30,
                       "duration_in_frames": video["duration_in_frames"], "rendered_frame_range": None,
                       "audio": "aac", "file_size": len(payload),
                       "probe": {"codec": "h264", "width": 1080, "height": 1920, "audio_streams": 1,
                                 "audio_codec": "aac", "audio_sample_rate": 48_000, "audio_channels": 1,
                                 "duration_seconds": video["duration_in_frames"] / 30,
                                 "video_duration_seconds": video["duration_in_frames"] / 30,
                                 "audio_duration_seconds": video["duration_in_frames"] / 30}}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(summary), stderr="")

        summaries = render_mp4s([(video, props, target)], node="node", runner=runner)
        self.assertEqual("aac", summaries[0]["audio"])
        self.assertIn(b"ftyp", target.read_bytes()[:32])

    def test_remotion_uses_per_scene_html5_audio_and_fixed_disclosure(self):
        component = (SKILL / "remotion/XhsQuestionVideo.jsx").read_text(encoding="utf-8")
        shell = (SKILL / "remotion/components/SceneShell.jsx").read_text(encoding="utf-8")
        self.assertIn("Html5Audio", component)
        self.assertIn("staticFile", component)
        self.assertIn("synthetic_audio", shell)
        self.assertIn("旁白由AI合成", shell)

    @staticmethod
    def _inject_hook_ai_caption(project, label):
        hook = next(scene for scene in project["videos"][0]["scenes"] if scene["role"] == "hook")
        hook["captions"][0]["text"] = label
        hook["narration"] = "".join(caption["text"] for caption in hook["captions"])
        hook["audio"]["narration_sha256"] = "sha256:" + hashlib.sha256(hook["narration"].encode("utf-8")).hexdigest()

    def test_synthetic_hook_has_one_dedicated_first_frame_label_without_footer_or_chip_duplication(self):
        shell = (SKILL / "remotion/components/SceneShell.jsx").read_text(encoding="utf-8")
        self.assertEqual(1, shell.count('"旁白由AI合成"'))
        self.assertIn("first-frame-ai-label", shell)
        self.assertIn("data-first-frame-ai-label", shell)
        self.assertIn('scene.role === "hook"', shell)
        self.assertIn('code === "synthetic_audio"', shell)
        self.assertIn('"有声版 · 已确认使用权"', shell)
        self.assertNotIn('"旁白由AI合成 · 已确认使用权"', shell)
        self.assertIn('"无配音版 · 静音也能看懂"', shell)
        self.assertIn('"真人旁白 · 已确认使用权"', shell)
        self.assertIn(
            'audio.kind === "none"\n    ? "无配音版 · 静音也能看懂"\n'
            '    : audio.origin === "synthetic_ai" ? "有声版 · 已确认使用权" : "真人旁白 · 已确认使用权"',
            shell,
        )

    def test_python_rejects_duplicate_negative_and_question_ai_labels_in_hook_captions(self):
        for label in self.AI_AUDIO_LABEL_MUTATIONS:
            with self.subTest(label=label):
                project = copy.deepcopy(self.project)
                self._inject_hook_ai_caption(project, label)
                errors = validate_video_ir(project)
                self.assertTrue(any("FIRST_FRAME_AI_LABEL" in error for error in errors), errors)

    def test_node_rejects_hook_caption_ai_label_before_browser(self):
        for index, label in enumerate(self.AI_AUDIO_LABEL_MUTATIONS):
            with self.subTest(label=label):
                project = self.root / f"caption-label-{index}"
                shutil.copytree(self.project_dir, project)
                props = next(project.glob("*.props.json"))
                payload = json.loads(props.read_text(encoding="utf-8"))
                wrapper = {"schema": payload["schema"], "videos": [payload["video"]]}
                self._inject_hook_ai_caption(wrapper, label)
                props.write_text(json.dumps({"schema": payload["schema"], "video": wrapper["videos"][0]}, ensure_ascii=False), encoding="utf-8")
                result = subprocess.run(
                    ["node", str(SCRIPTS / "render_video.mjs"), "--props", str(props), "--output", str(project / "out.mp4"), "--frame-range", "0:2"],
                    text=True, encoding="utf-8", capture_output=True, check=False,
                )
                self.assertEqual(3, result.returncode, result.stderr + result.stdout)
                self.assertIn("first-frame AI label", result.stderr + result.stdout)
                self.assertNotIn("No local Chromium", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
