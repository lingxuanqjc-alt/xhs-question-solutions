#!/usr/bin/env python3
"""Import reviewed per-scene WAV voiceover into a deterministic xhs-video/v2 project."""
import argparse
import copy
import hashlib
import io
import json
import math
import os
import re
import shutil
import struct
import sys
import uuid
import wave
import warnings
from pathlib import Path, PurePosixPath

from render_video import UNSAFE_NOTICE_CODE, UNSAFE_WARNING, display_units, validate_video_ir


MANIFEST_SCHEMA = "xhs-voiceover-manifest/v1"
SOURCE_SCHEMA = "xhs-video/v1"
OUTPUT_SCHEMA = "xhs-video/v2"
OUTPUT_PROFILE = "xhs-vertical-1080x1920-v2-voiced"
FPS = 30
SAMPLE_RATE = 48_000
SAMPLES_PER_FRAME = SAMPLE_RATE // FPS
ORIGINS = {"human_recorded", "synthetic_ai"}
RIGHTS_BY_ORIGIN = {
    "human_recorded": {"self_recorded", "licensed"},
    "synthetic_ai": {"synthetic_service_terms_confirmed", "licensed"},
}
SYNTHETIC_DISCLOSURE = "旁白由AI合成"
MAX_WAV_BYTES = 9_000_000  # 90s mono PCM is 8.64MB; this allows bounded RIFF metadata.
MIN_CUE_SAMPLES = 1_200 * SAMPLE_RATE // 1_000


class VoiceoverError(ValueError):
    def __init__(self, code, path="$", message=""):
        self.code, self.path, self.message = code, path, message or code
        super().__init__(f"{code} {path}: {self.message}")

    def as_dict(self):
        return {"code": self.code, "path": self.path, "message": self.message}


def _fail(code, path, message):
    raise VoiceoverError(code, path, message)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _nonempty(value):
    return isinstance(value, str) and bool(value)


def _exact_keys(value, expected, path, code):
    if not isinstance(value, dict):
        _fail(code, path, "must be an object")
    missing = sorted(set(expected) - set(value))
    unknown = sorted(set(value) - set(expected))
    if missing or unknown:
        _fail(code, path, f"fields missing={missing} unknown={unknown}")


def _canonical_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise VoiceoverError("VOICEOVER_JSON_INVALID", "$", "JSON must be finite and serializable") from error


def canonical_sha256(value):
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def narration_sha256(value):
    if not isinstance(value, str):
        _fail("VOICEOVER_NARRATION_MISMATCH", "$", "narration must be a string")
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_source_v1(source):
    try:
        complete_errors = validate_video_ir(source)
    except Exception as error:
        raise VoiceoverError("VOICEOVER_SOURCE_IR_INVALID", "$", "complete v1 validation failed") from error
    complete_errors = [error for error in complete_errors
                       if not (error.startswith("UNSAFE_NOTICE ") and error.endswith(" has warning without unsafe evidence"))]
    if complete_errors:
        _fail("VOICEOVER_SOURCE_IR_INVALID", "$", complete_errors[0])
    _exact_keys(source, {"schema", "videos"}, "$", "VOICEOVER_SOURCE_IR_INVALID")
    if source["schema"] != SOURCE_SCHEMA:
        _fail("VOICEOVER_SOURCE_IR_INVALID", "$.schema", f"expected {SOURCE_SCHEMA}")
    videos = source["videos"]
    if not isinstance(videos, list) or not videos:
        _fail("VOICEOVER_SOURCE_IR_INVALID", "$.videos", "must be a non-empty list")
    seen_videos = set()
    for vi, video in enumerate(videos):
        path = f"$.videos[{vi}]"
        if not isinstance(video, dict):
            _fail("VOICEOVER_SOURCE_IR_INVALID", path, "must be an object")
        for key in ("video_id", "note_id", "profile", "width", "height", "fps", "duration_ms", "duration_in_frames", "meta", "scenes"):
            if key not in video:
                _fail("VOICEOVER_SOURCE_IR_INVALID", f"{path}.{key}", "is required")
        if not _nonempty(video["video_id"]) or video["video_id"] in seen_videos or not _nonempty(video["note_id"]):
            _fail("VOICEOVER_SOURCE_IR_INVALID", path, "video_id and note_id must be non-empty and video_id unique")
        seen_videos.add(video["video_id"])
        if video["profile"] != "xhs-vertical-1080x1920-v1" or any(not _is_int(video[key]) for key in ("width", "height", "fps", "duration_ms", "duration_in_frames")):
            _fail("VOICEOVER_SOURCE_IR_INVALID", path, "invalid v1 profile scalars")
        if (video["width"], video["height"], video["fps"]) != (1080, 1920, FPS):
            _fail("VOICEOVER_SOURCE_IR_INVALID", path, "expected 1080x1920 at 30fps")
        if not isinstance(video["meta"], dict) or video["meta"].get("audio") != {"kind": "none"}:
            _fail("VOICEOVER_SOURCE_IR_INVALID", f"{path}.meta.audio", "source v1 must be silent")
        unsafe_ids = video.get("unsafe_evidence_comment_ids")
        if not isinstance(unsafe_ids, list) or any(not _nonempty(item) for item in unsafe_ids):
            _fail("VOICEOVER_SOURCE_IR_INVALID", f"{path}.unsafe_evidence_comment_ids", "must be a string list")
        scenes = video["scenes"]
        if not isinstance(scenes, list) or not scenes:
            _fail("VOICEOVER_SOURCE_IR_INVALID", f"{path}.scenes", "must be non-empty")
        cursor, seen_scenes = 0, set()
        for si, scene in enumerate(scenes):
            scene_path = f"{path}.scenes[{si}]"
            if not isinstance(scene, dict):
                _fail("VOICEOVER_SOURCE_IR_INVALID", scene_path, "must be an object")
            for key in ("scene_id", "index", "start_ms", "end_ms", "narration", "captions", "persistent_notices"):
                if key not in scene:
                    _fail("VOICEOVER_SOURCE_IR_INVALID", f"{scene_path}.{key}", "is required")
            if not _nonempty(scene["scene_id"]) or scene["scene_id"] in seen_scenes:
                _fail("VOICEOVER_SOURCE_IR_INVALID", f"{scene_path}.scene_id", "must be non-empty and unique")
            seen_scenes.add(scene["scene_id"])
            if not all(_is_int(scene[key]) for key in ("index", "start_ms", "end_ms")) or scene["index"] != si + 1 or scene["start_ms"] != cursor or scene["end_ms"] <= cursor:
                _fail("VOICEOVER_SOURCE_IR_INVALID", scene_path, "scene timing and index must be contiguous")
            cursor = scene["end_ms"]
            if not _nonempty(scene["narration"]) or not isinstance(scene["captions"], list) or not scene["captions"]:
                _fail("VOICEOVER_SOURCE_IR_INVALID", scene_path, "narration and captions must be non-empty")
            texts = []
            for ci, caption in enumerate(scene["captions"]):
                if not isinstance(caption, dict) or not _nonempty(caption.get("text")):
                    _fail("VOICEOVER_SOURCE_IR_INVALID", f"{scene_path}.captions[{ci}]", "caption text must be non-empty")
                texts.append(caption["text"])
            if "".join(texts) != scene["narration"]:
                _fail("VOICEOVER_SOURCE_IR_INVALID", scene_path, "captions must concatenate to narration")
            notices, evidence_ids = scene["persistent_notices"], scene.get("evidence_comment_ids")
            if not isinstance(notices, list) or not isinstance(evidence_ids, list):
                _fail("VOICEOVER_SOURCE_IR_INVALID", scene_path, "notices and evidence IDs must be lists")
            unsafe = bool(set(unsafe_ids).intersection(evidence_ids))
            if unsafe != (UNSAFE_NOTICE_CODE in notices):
                _fail("VOICEOVER_SOURCE_IR_INVALID", scene_path, "unsafe evidence and fixed notice must agree")
            if unsafe and not texts[0].startswith(UNSAFE_WARNING):
                _fail("VOICEOVER_SOURCE_IR_INVALID", scene_path, "unsafe scene must begin with the fixed warning")
        if cursor != video["duration_ms"]:
            _fail("VOICEOVER_SOURCE_IR_INVALID", path, "video duration must equal the scene timeline")
    return videos


def _resolved(path):
    return Path(path).resolve(strict=False)


def _paths_overlap(first, second):
    first, second = _resolved(first), _resolved(second)
    return first == second or first in second.parents or second in first.parents


def _reject_overlap(first, second, path):
    if _paths_overlap(first, second):
        _fail("VOICEOVER_PATH_OVERLAP", path, "input and output paths must not overlap")


def init_manifest(source, *, source_path=None, manifest_path=None):
    if source_path is not None and manifest_path is not None:
        _reject_overlap(source_path, manifest_path, "$paths.manifest")
    videos = _validate_source_v1(source)
    result = {"schema": MANIFEST_SCHEMA, "source_ir_sha256": canonical_sha256(source), "videos": []}
    for vi, video in enumerate(videos, 1):
        item = {"video_id": video["video_id"], "origin": None, "rights_basis": None, "scenes": []}
        for si, scene in enumerate(video["scenes"], 1):
            item["scenes"].append({
                "scene_id": scene["scene_id"],
                "narration": scene["narration"],
                "narration_sha256": narration_sha256(scene["narration"]),
                "file": f"audio/video-{vi:03d}/scene-{si:03d}.wav",
                "cues": [{"text": caption["text"], "start_sample": None, "end_sample": None} for caption in scene["captions"]],
            })
        result["videos"].append(item)
    return result


def _resolve_audio(base, raw_path, path):
    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path or re.match(r"^[A-Za-z]:", raw_path):
        _fail("VOICEOVER_PATH_INVALID", path, "use a non-empty relative POSIX path")
    pure = PurePosixPath(raw_path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        _fail("VOICEOVER_PATH_INVALID", path, "absolute and parent paths are forbidden")
    base = Path(base).resolve()
    candidate = base.joinpath(*pure.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(base)
    except (OSError, ValueError) as error:
        code = "VOICEOVER_FILE_MISSING" if not candidate.exists() else "VOICEOVER_PATH_INVALID"
        raise VoiceoverError(code, path, "audio must exist inside the manifest directory") from error
    if not resolved.is_file():
        _fail("VOICEOVER_FILE_MISSING", path, "audio file is missing")
    return resolved


def _pcm_wav(path, json_path):
    size_on_disk = path.stat().st_size
    if size_on_disk > MAX_WAV_BYTES:
        _fail("VOICEOVER_WAV_TOO_LARGE", json_path, f"clip exceeds {MAX_WAV_BYTES} bytes")
    raw = path.read_bytes()
    if len(raw) != size_on_disk:
        _fail("VOICEOVER_WAV_CHANGED", json_path, "audio changed while it was being imported")
    if len(raw) < 16 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        _fail("VOICEOVER_WAV_FORMAT", json_path, "expected RIFF/WAVE PCM")
    if struct.unpack_from("<I", raw, 4)[0] != len(raw) - 8:
        _fail("VOICEOVER_WAV_FORMAT", json_path, "RIFF size does not match the complete file")
    offset, format_tag, data_size, data_start = 12, None, None, None
    while offset < len(raw):
        if offset + 8 > len(raw):
            _fail("VOICEOVER_WAV_FORMAT", json_path, "truncated WAV chunk header")
        chunk, size = raw[offset:offset + 4], struct.unpack_from("<I", raw, offset + 4)[0]
        start = offset + 8
        end = start + size
        padded_end = end + (size % 2)
        if end > len(raw) or padded_end > len(raw):
            _fail("VOICEOVER_WAV_FORMAT", json_path, "truncated WAV chunk")
        if chunk == b"fmt " and size >= 16:
            format_tag = struct.unpack_from("<H", raw, start)[0]
        elif chunk == b"data":
            if data_size is not None:
                _fail("VOICEOVER_WAV_FORMAT", json_path, "multiple data chunks are not supported")
            data_size = size
            data_start = start
        offset = padded_end
    try:
        with wave.open(io.BytesIO(raw), "rb") as handle:
            values = (handle.getcomptype(), handle.getframerate(), handle.getnchannels(), handle.getsampwidth(), handle.getnframes())
    except (EOFError, wave.Error) as error:
        raise VoiceoverError("VOICEOVER_WAV_FORMAT", json_path, "invalid WAV structure") from error
    compression, rate, channels, width, samples = values
    if (
        format_tag != 1 or compression != "NONE" or (rate, channels, width) != (SAMPLE_RATE, 1, 2)
        or samples <= 0 or data_size != samples * channels * width
    ):
        _fail("VOICEOVER_WAV_FORMAT", json_path, "requires RIFF PCM s16le 48000Hz mono 16-bit")
    loud_samples = 0
    for (sample,) in struct.iter_unpack("<h", raw[data_start:data_start + data_size]):
        if abs(sample) >= 256:
            loud_samples += 1
            if loud_samples >= 480:
                break
    if loud_samples < 480:
        _fail("VOICEOVER_WAV_SIGNAL", json_path, "basic signal gate found less than 10ms above the near-silence threshold")
    return raw, samples


def _frame_ms(frame):
    return (frame * 1000 + FPS // 2) // FPS


def _validate_manifest_shape(manifest):
    _exact_keys(manifest, {"schema", "source_ir_sha256", "videos"}, "$manifest", "VOICEOVER_MANIFEST_INVALID")
    if manifest["schema"] != MANIFEST_SCHEMA or not isinstance(manifest["videos"], list):
        _fail("VOICEOVER_MANIFEST_INVALID", "$manifest", f"expected {MANIFEST_SCHEMA}")


def _build_in_staging(source, manifest, manifest_dir, staging):
    source_videos = _validate_source_v1(source)
    _validate_manifest_shape(manifest)
    if manifest["source_ir_sha256"] != canonical_sha256(source):
        _fail("VOICEOVER_SOURCE_IR_MISMATCH", "$manifest.source_ir_sha256", "manifest was created for different v1 IR")
    expected_video_ids = [video["video_id"] for video in source_videos]
    actual_video_ids = [item.get("video_id") for item in manifest["videos"] if isinstance(item, dict)]
    if actual_video_ids != expected_video_ids or len(manifest["videos"]) != len(source_videos):
        _fail("VOICEOVER_SCENE_SET_MISMATCH", "$manifest.videos", "video set and order must exactly match v1")
    source_digest, manifest_digest = canonical_sha256(source), canonical_sha256(manifest)
    output = {"schema": OUTPUT_SCHEMA, "source": {"schema": SOURCE_SCHEMA, "sha256": source_digest}, "videos": []}
    asset_dir = staging / "assets/voiceover"
    asset_dir.mkdir(parents=True)
    for vi, (source_video, manifest_video) in enumerate(zip(source_videos, manifest["videos"])):
        video_path = f"$manifest.videos[{vi}]"
        _exact_keys(manifest_video, {"video_id", "origin", "rights_basis", "scenes"}, video_path, "VOICEOVER_MANIFEST_INVALID")
        if not isinstance(manifest_video["origin"], str) or manifest_video["origin"] not in ORIGINS:
            _fail("VOICEOVER_ORIGIN_REQUIRED", f"{video_path}.origin", "choose human_recorded or synthetic_ai")
        if (
            not isinstance(manifest_video["rights_basis"], str)
            or manifest_video["rights_basis"] not in RIGHTS_BY_ORIGIN[manifest_video["origin"]]
        ):
            _fail(
                "VOICEOVER_RIGHTS_BASIS_INVALID",
                f"{video_path}.rights_basis",
                "rights_basis is not allowed for this origin",
            )
        if not isinstance(manifest_video["scenes"], list):
            _fail("VOICEOVER_SCENE_SET_MISMATCH", f"{video_path}.scenes", "must be a list")
        expected_scenes = source_video["scenes"]
        actual_scene_ids = [item.get("scene_id") for item in manifest_video["scenes"] if isinstance(item, dict)]
        if actual_scene_ids != [scene["scene_id"] for scene in expected_scenes] or len(manifest_video["scenes"]) != len(expected_scenes):
            _fail("VOICEOVER_SCENE_SET_MISMATCH", f"{video_path}.scenes", "scene set and order must exactly match v1")
        video = copy.deepcopy(source_video)
        video["profile"] = OUTPUT_PROFILE
        video["meta"]["audio"] = {
            "kind": "external_voiceover", "layout": "per_scene", "origin": manifest_video["origin"], "reviewed": True,
            "rights_basis": manifest_video["rights_basis"], "rights_confirmed": True,
            "disclosure_required": manifest_video["origin"] == "synthetic_ai",
            "disclosure_text": SYNTHETIC_DISCLOSURE if manifest_video["origin"] == "synthetic_ai" else None,
            "signal_check": {"kind": "basic_pcm_activity", "audibility_verified": False},
        }
        if manifest_video["origin"] == "synthetic_ai":
            notice_scenes = [scene for scene in video["scenes"] if scene.get("role") in {"hook", "disclosure"}]
            if {scene.get("role") for scene in notice_scenes} != {"hook", "disclosure"} or any(
                not isinstance(scene.get("persistent_notices"), list) for scene in notice_scenes
            ):
                _fail("VOICEOVER_DISCLOSURE_SCENE_INVALID", video_path, "synthetic audio requires hook and disclosure scenes")
            for notice_scene in notice_scenes:
                if "synthetic_audio" not in notice_scene["persistent_notices"]:
                    notice_scene["persistent_notices"].append("synthetic_audio")
        cursor_frame, audio_hashes = 0, []
        for si, (scene, item) in enumerate(zip(video["scenes"], manifest_video["scenes"])):
            item_path = f"{video_path}.scenes[{si}]"
            _exact_keys(item, {"scene_id", "narration", "narration_sha256", "file", "cues"}, item_path, "VOICEOVER_MANIFEST_INVALID")
            source_scene = expected_scenes[si]
            if item["narration"] != source_scene["narration"]:
                _fail("VOICEOVER_NARRATION_MISMATCH", f"{item_path}.narration", "must exactly equal v1 narration")
            expected_hash = narration_sha256(source_scene["narration"])
            if item["narration_sha256"] != expected_hash:
                _fail("VOICEOVER_NARRATION_HASH_MISMATCH", f"{item_path}.narration_sha256", "does not match exact UTF-8 narration")
            audio_path = _resolve_audio(manifest_dir, item["file"], f"{item_path}.file")
            raw, sample_count = _pcm_wav(audio_path, f"{item_path}.file")
            digest = hashlib.sha256(raw).hexdigest()
            audio_hashes.append(f"sha256:{digest}")
            asset_relative = f"assets/voiceover/{digest}.wav"
            asset = staging / asset_relative
            if not asset.exists():
                asset.write_bytes(raw)
            cues = item["cues"]
            expected_texts = [caption["text"] for caption in source_scene["captions"]]
            if not isinstance(cues, list) or not cues:
                _fail("VOICEOVER_CAPTION_TEXT_MISMATCH", f"{item_path}.cues", "cues must be non-empty")
            if UNSAFE_NOTICE_CODE in source_scene["persistent_notices"] and (not isinstance(cues[0], dict) or not str(cues[0].get("text", "")).startswith(UNSAFE_WARNING)):
                _fail("VOICEOVER_UNSAFE_FIRST_CUE", f"{item_path}.cues[0]", "unsafe voiceover must begin with the fixed warning")
            actual_texts = [cue.get("text") for cue in cues if isinstance(cue, dict)]
            if actual_texts != expected_texts or len(cues) != len(expected_texts) or "".join(actual_texts) != source_scene["narration"]:
                _fail("VOICEOVER_CAPTION_TEXT_MISMATCH", f"{item_path}.cues", "cue texts must exactly match v1 caption chunks")
            scene_frames = math.ceil(sample_count / SAMPLES_PER_FRAME)
            scene_start_frame, scene_end_frame = cursor_frame, cursor_frame + scene_frames
            scene_start_ms, scene_end_ms = _frame_ms(scene_start_frame), _frame_ms(scene_end_frame)
            captions, previous_sample, previous_ms = [], 0, scene_start_ms
            for ci, cue in enumerate(cues):
                cue_path = f"{item_path}.cues[{ci}]"
                _exact_keys(cue, {"text", "start_sample", "end_sample"}, cue_path, "VOICEOVER_MANIFEST_INVALID")
                start, end = cue["start_sample"], cue["end_sample"]
                if not _is_int(start) or not _is_int(end) or start < previous_sample or end <= start or end > sample_count:
                    _fail("VOICEOVER_CUE_TIMING_INVALID", cue_path, "cue samples must be ordered, non-overlapping, and inside the WAV")
                if end - start < MIN_CUE_SAMPLES:
                    _fail("VOICEOVER_CUE_TOO_SHORT", cue_path, "every caption cue must be at least 1200ms")
                seconds = (end - start) / SAMPLE_RATE
                if display_units(cue["text"]) / seconds > 10.0001:
                    _fail("VOICEOVER_CUE_READING_SPEED", cue_path, "caption exceeds 10 display units per second")
                timeline_sample = scene_start_frame * SAMPLES_PER_FRAME
                start_ms = max(previous_ms, (timeline_sample + start) * 1000 // SAMPLE_RATE)
                end_ms = ((timeline_sample + end) * 1000 + SAMPLE_RATE // 2) // SAMPLE_RATE
                captions.append({"text": cue["text"], "startMs": start_ms, "endMs": end_ms, "timestampMs": None, "confidence": None})
                previous_sample, previous_ms = end, end_ms
            if captions[-1]["endMs"] > scene_end_ms:
                _fail("VOICEOVER_CUE_TIMING_INVALID", f"{item_path}.cues", "caption exceeds the audio-derived scene")
            scene.update({
                "start_frame": scene_start_frame, "end_frame": scene_end_frame,
                "start_ms": scene_start_ms, "end_ms": scene_end_ms, "captions": captions,
                "audio": {
                    "kind": "external_voiceover_clip", "path": asset_relative,
                    "sha256": f"sha256:{digest}", "narration_sha256": expected_hash,
                    "codec": "pcm_s16le", "sample_rate_hz": SAMPLE_RATE, "channels": 1,
                    "bits_per_sample": 16, "sample_count": sample_count,
                },
            })
            cursor_frame = scene_end_frame
        if not 1800 <= cursor_frame <= 2700:
            _fail("VOICEOVER_DURATION_OUT_OF_RANGE", video_path, "audio-derived video must be 60-90 seconds")
        video["duration_in_frames"] = cursor_frame
        video["duration_ms"] = _frame_ms(cursor_frame)
        attestation_binding = {
            "source_ir_sha256": source_digest,
            "manifest_sha256": manifest_digest,
            "video_id": video["video_id"],
            "origin": manifest_video["origin"],
            "rights_basis": manifest_video["rights_basis"],
            "audio_sha256": audio_hashes,
            "audio_reviewed": True,
            "audio_rights_confirmed": True,
            "license_verified_by_tool": False,
        }
        video["meta"]["audio"]["attestation"] = {
            "kind": "user_declared_review_and_rights",
            **attestation_binding,
            "sha256": canonical_sha256(attestation_binding),
        }
        output["videos"].append(video)
        props = {"schema": OUTPUT_SCHEMA, "video": video}
        _write_json(staging / f"video-{vi + 1:03d}.props.json", props)
    _write_json(staging / "video-projects.json", output)
    return output


def _write_json(path, value):
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path):
    if os.name == "nt":
        return
    try:
        directory_fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        pass


def _replace_directory(staging, output, warnings_out):
    backup = output.with_name(f".{output.name}.backup-{uuid.uuid4().hex}")
    backed_up = False
    try:
        if output.exists():
            if not output.is_dir():
                _fail("VOICEOVER_OUTPUT_INVALID", "$output", "output must be a directory")
            output.rename(backup)
            backed_up = True
        staging.rename(output)
        _fsync_directory(output.parent)
    except Exception as error:
        if output.exists() and output != staging:
            shutil.rmtree(output, ignore_errors=True)
        if backed_up and backup.exists():
            backup.rename(output)
        if isinstance(error, VoiceoverError):
            raise
        raise VoiceoverError("VOICEOVER_OUTPUT_REPLACE_FAILED", "$output", "previous output was restored") from error
    if backup.exists():
        try:
            shutil.rmtree(backup)
        except OSError:
            warnings_out.append({"code": "VOICEOVER_BACKUP_CLEANUP_FAILED", "path": str(backup),
                                 "message": "new output is installed; old backup could not be removed"})
        _fsync_directory(output.parent)


def build_voiceover_project(
    source, manifest, manifest_dir, output_dir, *, confirmed_audio_reviewed=False, confirmed_audio_rights=False,
    source_path=None, manifest_path=None, warnings_out=None
):
    if confirmed_audio_reviewed is not True:
        _fail("VOICEOVER_REVIEW_REQUIRED", "$confirm", "pass --confirm-audio-reviewed only after listening to every clip")
    if confirmed_audio_rights is not True:
        _fail(
            "VOICEOVER_RIGHTS_CONFIRMATION_REQUIRED",
            "$confirm",
            "pass --confirm-audio-rights only after confirming the declared usage rights",
        )
    output, manifest_base = _resolved(output_dir), _resolved(manifest_dir)
    _reject_overlap(output, manifest_base, "$paths.output")
    if source_path is not None:
        _reject_overlap(output, source_path, "$paths.output")
    if manifest_path is not None:
        _reject_overlap(output, manifest_path, "$paths.output")
    emit_python_warnings = warnings_out is None
    warnings_out = warnings_out if warnings_out is not None else []
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.with_name(f".{output.name}.voiceover.lock")
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise VoiceoverError("VOICEOVER_OUTPUT_BUSY", "$output", "another build holds the output lock") from error
    os.close(lock_fd)
    staging = output.with_name(f".{output.name}.voiceover-{uuid.uuid4().hex}")
    try:
        staging.mkdir()
        result = _build_in_staging(source, manifest, manifest_base, staging)
        _replace_directory(staging, output, warnings_out)
        return result
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        try:
            lock.unlink()
        except OSError:
            warnings_out.append({"code": "VOICEOVER_LOCK_CLEANUP_FAILED", "path": str(lock),
                                 "message": "new output is installed; lock could not be removed and must be cleared before the next build"})
        if emit_python_warnings:
            for warning in warnings_out:
                warnings.warn(f"{warning['code']} {warning['path']}: {warning['message']}", RuntimeWarning, stacklevel=2)


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise VoiceoverError("VOICEOVER_JSON_INVALID", str(path), "must be valid UTF-8 JSON without non-finite constants") from error


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        _write_json(temporary, value)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise VoiceoverError("VOICEOVER_CLI_USAGE", "$argv", message)


def _parser():
    parser = _Parser(description="Initialize or build reviewed external voiceover for xhs-video/v1")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    init = commands.add_parser("init")
    init.add_argument("source_ir", type=Path)
    init.add_argument("manifest", type=Path)
    build = commands.add_parser("build")
    build.add_argument("source_ir", type=Path)
    build.add_argument("manifest", type=Path)
    build.add_argument("output_dir", type=Path)
    build.add_argument("--confirm-audio-reviewed", action="store_true")
    build.add_argument("--confirm-audio-rights", action="store_true")
    return parser


def _print_json(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def main(argv=None):
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="strict")
        args = _parser().parse_args(argv)
        source = _load_json(args.source_ir)
        if args.command == "init":
            manifest = init_manifest(source, source_path=args.source_ir, manifest_path=args.manifest)
            _atomic_json(args.manifest, manifest)
            _print_json({"status": "ok", "schema": MANIFEST_SCHEMA, "manifest": str(args.manifest)})
        else:
            manifest = _load_json(args.manifest)
            build_warnings = []
            project = build_voiceover_project(source, manifest, args.manifest.parent, args.output_dir,
                                               confirmed_audio_reviewed=args.confirm_audio_reviewed,
                                               confirmed_audio_rights=args.confirm_audio_rights,
                                               source_path=args.source_ir, manifest_path=args.manifest,
                                               warnings_out=build_warnings)
            _print_json({"status": "ok", "schema": project["schema"], "output": str(args.output_dir),
                         "videos": len(project["videos"]), "warnings": build_warnings})
        return 0
    except VoiceoverError as error:
        _print_json({"status": "error", "errors": [error.as_dict()]})
        return 2
    except Exception:
        error = VoiceoverError("VOICEOVER_INTERNAL_ERROR", "$", "unexpected failure; no output was installed")
        _print_json({"status": "error", "errors": [error.as_dict()]})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
