import copy
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents/skills/xhs-question-solutions/scripts"
sys.path.insert(0, str(SCRIPTS))

from import_voiceover import (  # noqa: E402
    VoiceoverError,
    build_voiceover_project,
    canonical_sha256,
    init_manifest,
)


SOURCE = ROOT / "examples/sample-video/video-projects.json"
UNSAFE_WARNING = "未核验高风险观点，不是操作建议"


def source_ir():
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def write_wav(path, frames, *, channels=1, sample_width=2, rate=48_000, amplitude=2048):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sample_width)
        handle.setframerate(rate)
        if sample_width == 2:
            positive = struct.pack("<h", amplitude) * channels
            negative = struct.pack("<h", -amplitude) * channels
            pattern = positive * 64 + negative * 64
            payload = (pattern * math.ceil(frames / 128))[:frames * channels * sample_width]
        else:
            payload = b"\1" * frames * channels * sample_width
        handle.writeframes(payload)


def ready_manifest(base, source=None, scale=1.0, origin="human_recorded"):
    source = source or source_ir()
    manifest = init_manifest(source)
    for manifest_video, video in zip(manifest["videos"], source["videos"]):
        manifest_video["origin"] = origin
        manifest_video["rights_basis"] = "self_recorded" if origin == "human_recorded" else "synthetic_service_terms_confirmed"
        for item, scene in zip(manifest_video["scenes"], video["scenes"]):
            duration_ms = round((scene["end_ms"] - scene["start_ms"]) * scale)
            frames = duration_ms * 48
            write_wav(base / item["file"], frames)
            for cue, caption in zip(item["cues"], scene["captions"]):
                cue["start_sample"] = min(frames - 1, round((caption["startMs"] - scene["start_ms"]) * 48))
                cue["end_sample"] = min(frames, round((caption["endMs"] - scene["start_ms"]) * 48))
    return manifest


def ready_manifest_for_total_frames(base, total_frames):
    source = source_ir()
    for scene in source["videos"][0]["scenes"]:
        text = UNSAFE_WARNING if scene["narration"].startswith(UNSAFE_WARNING) else "A"
        scene["narration"] = text
        scene["captions"] = [{"text": text, "startMs": scene["start_ms"], "endMs": scene["end_ms"],
                              "timestampMs": None, "confidence": None}]
    manifest = init_manifest(source)
    video = source["videos"][0]
    item_video = manifest["videos"][0]
    item_video["origin"] = "human_recorded"
    item_video["rights_basis"] = "self_recorded"
    minimums, cue_minimums = [], []
    for scene in video["scenes"]:
        cue_frames = [max(36, math.ceil(max(1, len(caption["text"]) * 2) / 10 * 30))
                      for caption in scene["captions"]]
        cue_minimums.append(cue_frames)
        minimums.append(sum(cue_frames))
    self_total = sum(minimums)
    if self_total > total_frames:
        raise AssertionError("fixture narration cannot fit target duration")
    allocations = list(minimums)
    for index in range(total_frames - self_total):
        allocations[index % len(allocations)] += 1
    for item, scene, frames, minimum_cues in zip(item_video["scenes"], video["scenes"], allocations, cue_minimums):
        samples = frames * 1600
        write_wav(base / item["file"], samples)
        cue_frames = list(minimum_cues)
        for index in range(frames - sum(cue_frames)):
            cue_frames[index % len(cue_frames)] += 1
        cursor = 0
        for cue, duration_frames in zip(item["cues"], cue_frames):
            duration = duration_frames * 1600
            cue["start_sample"] = cursor
            cue["end_sample"] = min(samples, cursor + duration)
            cursor = cue["end_sample"]
        item["cues"][-1]["end_sample"] = samples
    return source, manifest


class VoiceoverImportTests(unittest.TestCase):
    def test_init_is_deterministic_and_binds_exact_v1_narration(self):
        source = source_ir()
        first = init_manifest(source)
        second = init_manifest(copy.deepcopy(source))

        self.assertEqual(first, second)
        self.assertEqual("xhs-voiceover-manifest/v1", first["schema"])
        self.assertEqual(canonical_sha256(source), first["source_ir_sha256"])
        for video_item, video in zip(first["videos"], source["videos"]):
            self.assertIsNone(video_item["origin"])
            self.assertIsNone(video_item["rights_basis"])
            for item, scene in zip(video_item["scenes"], video["scenes"]):
                self.assertEqual(scene["narration"], item["narration"])
                self.assertEqual(
                    "sha256:" + hashlib.sha256(scene["narration"].encode("utf-8")).hexdigest(),
                    item["narration_sha256"],
                )
                self.assertEqual([caption["text"] for caption in scene["captions"]], [cue["text"] for cue in item["cues"]])
                self.assertTrue(item["file"].startswith("audio/"))
                self.assertNotIn("..", Path(item["file"]).parts)

    def test_build_writes_content_addressed_assets_v2_props_and_audio_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "voiceover"
            source = source_ir()
            manifest = ready_manifest(base, source, origin="synthetic_ai")
            output = Path(tmp) / "built"

            result = build_voiceover_project(source, manifest, base, output, confirmed_audio_reviewed=True, confirmed_audio_rights=True)

            self.assertEqual("xhs-video/v2", result["schema"])
            self.assertEqual({"schema": "xhs-video/v1", "sha256": canonical_sha256(source)}, result["source"])
            video = result["videos"][0]
            self.assertEqual("xhs-vertical-1080x1920-v2-voiced", video["profile"])
            self.assertEqual(2250, video["duration_in_frames"])
            self.assertEqual(75_000, video["duration_ms"])
            self.assertEqual(
                {"kind": "external_voiceover", "layout": "per_scene", "origin": "synthetic_ai", "reviewed": True,
                 "rights_basis": "synthetic_service_terms_confirmed", "rights_confirmed": True,
                 "disclosure_required": True, "disclosure_text": "旁白由AI合成",
                 "signal_check": {"kind": "basic_pcm_activity", "audibility_verified": False},
                 "attestation": video["meta"]["audio"]["attestation"]},
                video["meta"]["audio"],
            )
            self.assertEqual("user_declared_review_and_rights", video["meta"]["audio"]["attestation"]["kind"])
            attestation = video["meta"]["audio"]["attestation"]
            binding = {key: value for key, value in attestation.items() if key not in {"kind", "sha256"}}
            self.assertEqual(canonical_sha256(binding), attestation["sha256"])
            self.assertFalse(attestation["license_verified_by_tool"])
            self.assertEqual([scene["audio"]["sha256"] for scene in video["scenes"]], attestation["audio_sha256"])
            cursor = 0
            for scene in video["scenes"]:
                self.assertEqual(cursor, scene["start_frame"])
                cursor = scene["end_frame"]
                audio = scene["audio"]
                self.assertEqual("pcm_s16le", audio["codec"])
                self.assertEqual((48_000, 1, 16), (audio["sample_rate_hz"], audio["channels"], audio["bits_per_sample"]))
                self.assertTrue((output / audio["path"]).is_file())
                self.assertEqual(audio["sha256"].removeprefix("sha256:"), (output / audio["path"]).stem)
                self.assertEqual(scene["narration"], "".join(item["text"] for item in scene["captions"]))
                self.assertLessEqual(scene["captions"][-1]["endMs"], scene["end_ms"])
            props = list(output.glob("*.props.json"))
            self.assertEqual(1, len(props))
            self.assertEqual({"schema": "xhs-video/v2", "video": video}, json.loads(props[0].read_text(encoding="utf-8")))
            self.assertEqual(result, json.loads((output / "video-projects.json").read_text(encoding="utf-8")))

    def test_build_requires_review_and_known_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "voiceover"
            source = source_ir()
            manifest = ready_manifest(base, source)
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_REVIEW_REQUIRED"):
                build_voiceover_project(source, manifest, base, base.parent / "out", confirmed_audio_reviewed=False)
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_RIGHTS_CONFIRMATION_REQUIRED"):
                build_voiceover_project(source, manifest, base, base.parent / "out", confirmed_audio_reviewed=True)
            manifest["videos"][0]["origin"] = "unknown"
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_ORIGIN_REQUIRED"):
                build_voiceover_project(source, manifest, base, base.parent / "out", confirmed_audio_reviewed=True, confirmed_audio_rights=True)
            manifest["videos"][0]["origin"] = []
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_ORIGIN_REQUIRED"):
                build_voiceover_project(source, manifest, base, base.parent / "out", confirmed_audio_reviewed=True, confirmed_audio_rights=True)

    def test_rights_basis_is_controlled_by_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "voiceover"
            source = source_ir()
            for origin, basis in (("human_recorded", "synthetic_service_terms_confirmed"), ("synthetic_ai", "self_recorded"),
                                  ("human_recorded", "unknown"), ("human_recorded", [])):
                with self.subTest(origin=origin, basis=basis):
                    manifest = ready_manifest(base, source, origin=origin)
                    manifest["videos"][0]["rights_basis"] = basis
                    with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_RIGHTS_BASIS_INVALID"):
                        build_voiceover_project(source, manifest, base, base.parent / "out", confirmed_audio_reviewed=True,
                                                confirmed_audio_rights=True)

    def test_scene_narration_hash_and_cue_text_are_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "voiceover"
            source = source_ir()
            for mutate, code in (
                (lambda m: m.__setitem__("source_ir_sha256", "sha256:" + "0" * 64), "VOICEOVER_SOURCE_IR_MISMATCH"),
                (lambda m: m["videos"][0]["scenes"][0].__setitem__("narration", "改写"), "VOICEOVER_NARRATION_MISMATCH"),
                (lambda m: m["videos"][0]["scenes"][0].__setitem__("narration_sha256", "sha256:" + "0" * 64), "VOICEOVER_NARRATION_HASH_MISMATCH"),
                (lambda m: m["videos"][0]["scenes"][0]["cues"][0].__setitem__("text", "改写"), "VOICEOVER_CAPTION_TEXT_MISMATCH"),
            ):
                with self.subTest(code=code):
                    manifest = ready_manifest(base, source)
                    mutate(manifest)
                    with self.assertRaisesRegex(VoiceoverError, code):
                        build_voiceover_project(source, manifest, base, base.parent / "out", confirmed_audio_reviewed=True, confirmed_audio_rights=True)

    def test_scene_set_and_order_must_exactly_match_v1(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "voiceover"
            source = source_ir()
            for mutate in (
                lambda m: m["videos"][0]["scenes"].pop(),
                lambda m: m["videos"][0]["scenes"].reverse(),
            ):
                manifest = ready_manifest(base, source)
                mutate(manifest)
                with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_SCENE_SET_MISMATCH"):
                    build_voiceover_project(source, manifest, base, base.parent / "out", confirmed_audio_reviewed=True, confirmed_audio_rights=True)

    def test_multi_video_set_and_order_are_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "voiceover"
            source = source_ir()
            second = copy.deepcopy(source["videos"][0])
            second["video_id"] = "note:second"
            second["note_id"] = "second"
            second["scenes"] = [{**scene, "scene_id": f"second:{index:02d}"} for index, scene in enumerate(second["scenes"], 1)]
            source["videos"].append(second)
            manifest = ready_manifest(base, source)
            manifest["videos"].reverse()
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_SCENE_SET_MISMATCH"):
                build_voiceover_project(source, manifest, base, base.parent / "out", confirmed_audio_reviewed=True,
                                        confirmed_audio_rights=True)

    def test_paths_reject_absolute_drive_parent_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "voiceover"
            source = source_ir()
            for unsafe in ("/tmp/a.wav", "C:/audio/a.wav", "../a.wav", "audio\\a.wav"):
                with self.subTest(path=unsafe):
                    manifest = ready_manifest(base, source)
                    manifest["videos"][0]["scenes"][0]["file"] = unsafe
                    with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_PATH_INVALID"):
                        build_voiceover_project(source, manifest, base, Path(tmp) / "out", confirmed_audio_reviewed=True, confirmed_audio_rights=True)
            outside = Path(tmp) / "outside.wav"
            write_wav(outside, 144_000)
            link = base / "audio/escape.wav"
            try:
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(outside)
            except OSError:
                return
            manifest = ready_manifest(base, source)
            manifest["videos"][0]["scenes"][0]["file"] = "audio/escape.wav"
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_PATH_INVALID"):
                build_voiceover_project(source, manifest, base, Path(tmp) / "out", confirmed_audio_reviewed=True, confirmed_audio_rights=True)

    def test_wav_format_and_cue_timing_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "voiceover"
            source = source_ir()
            for kwargs in ({"channels": 2}, {"sample_width": 3}, {"rate": 44_100}):
                with self.subTest(format=kwargs):
                    manifest = ready_manifest(base, source)
                    first = manifest["videos"][0]["scenes"][0]
                    write_wav(base / first["file"], 144_000, **kwargs)
                    with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_WAV_FORMAT"):
                        build_voiceover_project(source, manifest, base, base.parent / "out", confirmed_audio_reviewed=True,
                                                confirmed_audio_rights=True)

            manifest = ready_manifest(base, source)
            first = manifest["videos"][0]["scenes"][0]
            wav = base / first["file"]
            wav.write_bytes(wav.read_bytes()[:-10])
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_WAV_FORMAT"):
                build_voiceover_project(source, manifest, base, base.parent / "out", confirmed_audio_reviewed=True,
                                        confirmed_audio_rights=True)

            manifest = ready_manifest(base, source)
            cues = manifest["videos"][0]["scenes"][0]["cues"]
            cues[0]["end_sample"] = cues[0]["start_sample"]
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_CUE_TIMING_INVALID"):
                build_voiceover_project(source, manifest, base, base.parent / "out", confirmed_audio_reviewed=True, confirmed_audio_rights=True)

            for mutate, code in (
                (lambda cues: cues[1].__setitem__("start_sample", cues[0]["start_sample"]), "VOICEOVER_CUE_TIMING_INVALID"),
                (lambda cues: cues[-1].__setitem__("end_sample", 10**9), "VOICEOVER_CUE_TIMING_INVALID"),
                (lambda cues: cues[1].__setitem__("end_sample", cues[1]["start_sample"] + 57_600), "VOICEOVER_CUE_READING_SPEED"),
            ):
                manifest = ready_manifest(base, source)
                cues = manifest["videos"][0]["scenes"][0]["cues"]
                mutate(cues)
                with self.assertRaisesRegex(VoiceoverError, code):
                    build_voiceover_project(source, manifest, base, base.parent / "out", confirmed_audio_reviewed=True,
                                            confirmed_audio_rights=True)

    def test_missing_audio_is_a_stable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "voiceover"
            source = source_ir()
            manifest = ready_manifest(base, source)
            (base / manifest["videos"][0]["scenes"][0]["file"]).unlink()
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_FILE_MISSING"):
                build_voiceover_project(source, manifest, base, base.parent / "out", confirmed_audio_reviewed=True,
                                        confirmed_audio_rights=True)

    def test_sixty_and_ninety_second_frame_boundaries_are_exact(self):
        for frames, allowed in ((1799, False), (1800, True), (2700, True), (2701, False)):
            with self.subTest(frames=frames), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp) / "voiceover"
                source, manifest = ready_manifest_for_total_frames(base, frames)
                if allowed:
                    result = build_voiceover_project(source, manifest, base, Path(tmp) / "out",
                                                     confirmed_audio_reviewed=True, confirmed_audio_rights=True)
                    self.assertEqual(frames, result["videos"][0]["duration_in_frames"])
                else:
                    with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_DURATION_OUT_OF_RANGE"):
                        build_voiceover_project(source, manifest, base, Path(tmp) / "out",
                                                confirmed_audio_reviewed=True, confirmed_audio_rights=True)

    def test_unsafe_scene_requires_the_fixed_warning_as_first_cue(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "voiceover"
            source = source_ir()
            manifest = ready_manifest(base, source)
            unsafe = next(item for item in manifest["videos"][0]["scenes"] if item["narration"].startswith(UNSAFE_WARNING))
            unsafe["cues"].reverse()
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_UNSAFE_FIRST_CUE"):
                build_voiceover_project(source, manifest, base, base.parent / "out", confirmed_audio_reviewed=True, confirmed_audio_rights=True)

    def test_audio_over_ninety_seconds_is_rejected_and_old_output_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "voiceover"
            source = source_ir()
            output = Path(tmp) / "out"
            output.mkdir()
            (output / "old.txt").write_text("complete", encoding="utf-8")
            manifest = ready_manifest(base, source, scale=1.22)
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_DURATION_OUT_OF_RANGE"):
                build_voiceover_project(source, manifest, base, output, confirmed_audio_reviewed=True, confirmed_audio_rights=True)
            self.assertEqual("complete", (output / "old.txt").read_text(encoding="utf-8"))
            self.assertEqual(["old.txt"], [item.name for item in output.iterdir()])
            self.assertFalse(list(output.parent.glob(f".{output.name}.voiceover-*")))

    def test_success_replaces_the_complete_old_directory_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "voiceover"
            source = source_ir()
            manifest = ready_manifest(base, source)
            output = Path(tmp) / "out"
            output.mkdir()
            (output / "old.txt").write_text("old", encoding="utf-8")
            build_voiceover_project(source, manifest, base, output, confirmed_audio_reviewed=True,
                                    confirmed_audio_rights=True)
            self.assertFalse((output / "old.txt").exists())
            self.assertTrue((output / "video-projects.json").is_file())
            self.assertFalse(list(output.parent.glob(f".{output.name}.voiceover-*")))
            self.assertFalse(list(output.parent.glob(f".{output.name}.backup-*")))

    def test_synthetic_origin_exposes_a_downstream_disclosure_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "voiceover"
            source = source_ir()
            manifest = ready_manifest(base, source, origin="synthetic_ai")
            result = build_voiceover_project(source, manifest, base, Path(tmp) / "out",
                                             confirmed_audio_reviewed=True, confirmed_audio_rights=True)
            video = result["videos"][0]
            disclosure = next(scene for scene in video["scenes"] if scene["role"] == "disclosure")
            hook = next(scene for scene in video["scenes"] if scene["role"] == "hook")
            self.assertEqual("旁白由AI合成", video["meta"]["audio"]["disclosure_text"])
            self.assertIn("synthetic_audio", hook["persistent_notices"])
            self.assertIn("synthetic_audio", disclosure["persistent_notices"])

    def test_init_and_build_reject_path_overlap_for_api_cli_aliases_and_unicode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "语音清单"
            base.mkdir()
            source = source_ir()
            source_path = base / "source.json"
            manifest_path = base / "manifest.json"
            source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_PATH_OVERLAP"):
                init_manifest(source, source_path=source_path, manifest_path=source_path)
            original = source_path.read_bytes()
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "import_voiceover.py"), "init", str(source_path), str(source_path)],
                text=True, encoding="utf-8", capture_output=True, check=False,
            )
            self.assertEqual(2, result.returncode)
            self.assertEqual(original, source_path.read_bytes())
            self.assertEqual("VOICEOVER_PATH_OVERLAP", json.loads(result.stdout)["errors"][0]["code"])

            manifest = ready_manifest(base, source)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            for output in (base, base / "child", base / "child/..", source_path.parent, manifest_path):
                with self.subTest(output=output), self.assertRaisesRegex(VoiceoverError, "VOICEOVER_PATH_OVERLAP"):
                    build_voiceover_project(
                        source, manifest, base, output, confirmed_audio_reviewed=True, confirmed_audio_rights=True,
                        source_path=source_path, manifest_path=manifest_path,
                    )
            cli = subprocess.run(
                [sys.executable, str(SCRIPTS / "import_voiceover.py"), "build", str(source_path), str(manifest_path),
                 str(base / "cli-out"), "--confirm-audio-reviewed", "--confirm-audio-rights"],
                text=True, encoding="utf-8", capture_output=True, check=False,
            )
            self.assertEqual("VOICEOVER_PATH_OVERLAP", json.loads(cli.stdout)["errors"][0]["code"])
            link = root / "别名"
            try:
                link.symlink_to(base, target_is_directory=True)
            except OSError:
                return
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_PATH_OVERLAP"):
                build_voiceover_project(source, manifest, base, link / "out", confirmed_audio_reviewed=True,
                                        confirmed_audio_rights=True)

    def test_complete_v1_validation_signal_gate_and_minimum_cue_duration_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "voiceover"
            invalid = source_ir()
            invalid["videos"][0]["scenes"][0]["persistent_notices"] = None
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_SOURCE_IR_INVALID"):
                init_manifest(invalid)
            invalid = source_ir()
            invalid["videos"][0]["scenes"][0]["role"] = "unknown"
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_SOURCE_IR_INVALID"):
                init_manifest(invalid)

            source = source_ir()
            for amplitude in (0, 1):
                manifest = ready_manifest(base, source)
                first = manifest["videos"][0]["scenes"][0]
                write_wav(base / first["file"], 144_000, amplitude=amplitude)
                with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_WAV_SIGNAL"):
                    build_voiceover_project(source, manifest, base, Path(tmp) / "out",
                                            confirmed_audio_reviewed=True, confirmed_audio_rights=True)

            shortened = source_ir()
            scene = next(item for item in shortened["videos"][0]["scenes"] if item["narration"].startswith("第2步："))
            scene["narration"] = "A" + scene["narration"][4:]
            scene["captions"][0]["text"] = "A"
            manifest = ready_manifest(base, shortened)
            item = next(value for value in manifest["videos"][0]["scenes"] if value["scene_id"] == scene["scene_id"])
            item["cues"][0]["start_sample"], item["cues"][0]["end_sample"] = 0, 16_800
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_CUE_TOO_SHORT"):
                build_voiceover_project(shortened, manifest, base, Path(tmp) / "out",
                                        confirmed_audio_reviewed=True, confirmed_audio_rights=True)

    def test_attestation_binds_final_audio_hashes_and_output_tree_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "voiceover"
            source = source_ir()
            manifest = ready_manifest(base, source)
            first = build_voiceover_project(source, manifest, base, root / "out-a", confirmed_audio_reviewed=True,
                                            confirmed_audio_rights=True)
            second = build_voiceover_project(source, manifest, base, root / "out-b", confirmed_audio_reviewed=True,
                                             confirmed_audio_rights=True)
            tree = lambda directory: {p.relative_to(directory).as_posix(): p.read_bytes()
                                      for p in directory.rglob("*") if p.is_file()}
            self.assertEqual(tree(root / "out-a"), tree(root / "out-b"))
            before = first["videos"][0]["meta"]["audio"]["attestation"]["sha256"]
            old_asset = root / "out-a" / first["videos"][0]["scenes"][0]["audio"]["path"]
            old_asset.write_bytes(b"tampered")
            write_wav(base / manifest["videos"][0]["scenes"][0]["file"], 144_000, amplitude=4096)
            rebuilt = build_voiceover_project(source, manifest, base, root / "out-a", confirmed_audio_reviewed=True,
                                              confirmed_audio_rights=True)
            self.assertNotEqual(before, rebuilt["videos"][0]["meta"]["audio"]["attestation"]["sha256"])
            new_asset = root / "out-a" / rebuilt["videos"][0]["scenes"][0]["audio"]["path"]
            self.assertEqual(hashlib.sha256(new_asset.read_bytes()).hexdigest(), new_asset.stem)
            self.assertFalse(old_asset.exists())

    def test_clip_size_lock_utf8_and_backup_cleanup_warning_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "voiceover"
            source = source_ir()
            manifest = ready_manifest(base, source)
            first = base / manifest["videos"][0]["scenes"][0]["file"]
            with first.open("wb") as handle:
                handle.truncate(9_000_001)
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_WAV_TOO_LARGE"):
                build_voiceover_project(source, manifest, base, root / "out", confirmed_audio_reviewed=True,
                                        confirmed_audio_rights=True)

            manifest = ready_manifest(base, source)
            lock = root / ".out.voiceover.lock"
            lock.write_text("busy", encoding="ascii")
            with self.assertRaisesRegex(VoiceoverError, "VOICEOVER_OUTPUT_BUSY"):
                build_voiceover_project(source, manifest, base, root / "out", confirmed_audio_reviewed=True,
                                        confirmed_audio_rights=True)
            lock.unlink()

            output = root / "out"
            output.mkdir()
            warnings_out = []
            real_rmtree = shutil.rmtree
            def fail_backup_only(path, *args, **kwargs):
                if ".backup-" in Path(path).name:
                    raise OSError("locked backup")
                return real_rmtree(path, *args, **kwargs)
            with mock.patch("import_voiceover.shutil.rmtree", side_effect=fail_backup_only):
                built = build_voiceover_project(source, manifest, base, output, confirmed_audio_reviewed=True,
                                                confirmed_audio_rights=True, warnings_out=warnings_out)
            self.assertEqual("xhs-video/v2", built["schema"])
            self.assertEqual("VOICEOVER_BACKUP_CLEANUP_FAILED", warnings_out[0]["code"])
            self.assertTrue((output / "video-projects.json").is_file())

            lock_warnings = []
            with mock.patch("import_voiceover.Path.unlink", side_effect=OSError("locked lock")):
                built = build_voiceover_project(source, manifest, base, output, confirmed_audio_reviewed=True,
                                                confirmed_audio_rights=True, warnings_out=lock_warnings)
            self.assertEqual("xhs-video/v2", built["schema"])
            self.assertEqual(
                "new output is installed; lock could not be removed and must be cleared before the next build",
                lock_warnings[0]["message"],
            )
            (root / ".out.voiceover.lock").unlink()

            unicode_source = root / "坏数据.json"
            unicode_source.write_text('{"videos":[NaN]}', encoding="utf-8")
            env = {**os.environ, "PYTHONIOENCODING": "ascii"}
            cli = subprocess.run(
                [sys.executable, str(SCRIPTS / "import_voiceover.py"), "init", str(unicode_source), str(root / "m.json")],
                capture_output=True, check=False, env=env,
            )
            self.assertEqual(2, cli.returncode)
            self.assertEqual("error", json.loads(cli.stdout.decode("utf-8"))["status"])
            self.assertNotIn(b"Traceback", cli.stderr + cli.stdout)

    def test_cli_reports_json_exit_two_without_traceback_or_nan(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.json"
            source.write_text('{"schema":"xhs-video/v1","videos":[NaN]}', encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "import_voiceover.py"), "init", str(source), str(Path(tmp) / "manifest.json")],
                text=True, encoding="utf-8", capture_output=True, check=False,
            )
            self.assertEqual(2, result.returncode)
            report = json.loads(result.stdout)
            self.assertEqual("error", report["status"])
            self.assertTrue(report["errors"][0]["code"])
            self.assertNotIn("Traceback", result.stderr + result.stdout)
            self.assertNotIn("NaN", result.stdout)


if __name__ == "__main__":
    unittest.main()
