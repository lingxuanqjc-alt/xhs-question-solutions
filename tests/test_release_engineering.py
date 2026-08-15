import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents" / "skills" / "xhs-question-solutions"
sys.path.insert(0, str(ROOT / "tests"))

import prepare_ci_voiceover  # noqa: E402


class ReleaseEngineeringTests(unittest.TestCase):
    def test_package_and_lock_publish_the_same_v050_version(self):
        package = json.loads((SKILL / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((SKILL / "package-lock.json").read_text(encoding="utf-8"))

        self.assertEqual("0.5.0", package["version"])
        self.assertEqual(package["version"], lock["version"])
        self.assertEqual(package["version"], lock["packages"][""]["version"])

    def test_ci_exercises_the_reviewed_v2_cli_and_both_media_contracts(self):
        workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")

        required_fragments = (
            "import_voiceover.py init",
            "prepare_ci_voiceover.py",
            "import_voiceover.py build",
            "--project-dir",
            "--confirm-audio-reviewed",
            "--confirm-audio-rights",
            "render_ci_video_smoke.py build/ci/v1 build/ci/v2",
        )
        for fragment in required_fragments:
            self.assertIn(fragment, workflow)
        self.assertNotIn("playwright install", workflow)
        self.assertNotIn("remotion browser", workflow)
        helper = (ROOT / "tests" / "render_ci_video_smoke.py").read_text(encoding="utf-8")
        for fragment in ("--project-dir", "--frame-range", "audio_streams", "audio_codec", "audio_sample_rate", "audio_channels", "CAPTION_OVERFLOW"):
            self.assertIn(fragment, helper)

    def test_ci_voiceover_fixture_is_explicit_non_speech_pcm_with_activity(self):
        manifest = {
            "schema": "xhs-voiceover-manifest/v1",
            "source_ir_sha256": "sha256:" + "0" * 64,
            "videos": [{
                "video_id": "test:video",
                "origin": None,
                "rights_basis": None,
                "scenes": [{
                    "scene_id": "test:scene",
                    "narration": "测试",
                    "narration_sha256": "sha256:" + "0" * 64,
                    "file": "audio/test.wav",
                    "cues": [{"text": "测试", "start_sample": None, "end_sample": None}],
                }],
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(prepare_ci_voiceover, "TARGET_FRAMES", 90):
                prepare_ci_voiceover.prepare(path)

            prepared = json.loads(path.read_text(encoding="utf-8"))
            video = prepared["videos"][0]
            cue = video["scenes"][0]["cues"][0]
            self.assertEqual(("synthetic_ai", "licensed"),
                             (video["origin"], video["rights_basis"]))
            self.assertEqual((0, 90 * 1_600), (cue["start_sample"], cue["end_sample"]))
            with wave.open(str(Path(directory) / "audio/test.wav"), "rb") as handle:
                self.assertEqual((1, 2, 48_000, 90 * 1_600),
                                 (handle.getnchannels(), handle.getsampwidth(), handle.getframerate(), handle.getnframes()))
                self.assertNotEqual({0}, set(handle.readframes(128)))


if __name__ == "__main__":
    unittest.main()
