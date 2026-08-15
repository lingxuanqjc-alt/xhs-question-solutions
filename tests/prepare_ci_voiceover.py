"""Create reviewed non-speech WAV fixtures for the CI-only voiceover smoke test."""

import argparse
import json
import math
import struct
import sys
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents" / "skills" / "xhs-question-solutions" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from import_voiceover import display_units  # noqa: E402


SAMPLE_RATE = 48_000
SAMPLES_PER_FRAME = 1_600
TARGET_FRAMES = 2_250
MIN_CUE_SAMPLES = 57_600


def write_test_wave(path, sample_count):
    path.parent.mkdir(parents=True, exist_ok=True)
    cycle = struct.pack("<128h", *([2048] * 64 + [-2048] * 64))
    payload = (cycle * math.ceil(sample_count / 128))[: sample_count * 2]
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(payload)


def prepare(manifest_path):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scenes = [scene for video in manifest["videos"] for scene in video["scenes"]]
    if not scenes:
        raise ValueError("CI voiceover manifest has no scenes")
    frame_counts = [TARGET_FRAMES // len(scenes)] * len(scenes)
    for index in range(TARGET_FRAMES % len(scenes)):
        frame_counts[index] += 1

    cursor = 0
    for video in manifest["videos"]:
        video["origin"] = "synthetic_ai"
        video["rights_basis"] = "licensed"
        for scene in video["scenes"]:
            sample_count = frame_counts[cursor] * SAMPLES_PER_FRAME
            cursor += 1
            write_test_wave(manifest_path.parent / scene["file"], sample_count)

            minimums = [
                max(MIN_CUE_SAMPLES, math.ceil(display_units(cue["text"]) * SAMPLE_RATE / 10))
                for cue in scene["cues"]
            ]
            if sum(minimums) > sample_count:
                raise ValueError(f"CI test waveform is too short for {scene['scene_id']}")
            allocations = list(minimums)
            remaining = sample_count - sum(allocations)
            per_cue, remainder = divmod(remaining, len(allocations))
            allocations = [
                length + per_cue + (1 if index < remainder else 0)
                for index, length in enumerate(allocations)
            ]
            sample_cursor = 0
            for cue, length in zip(scene["cues"], allocations):
                cue["start_sample"] = sample_cursor
                sample_cursor += length
                cue["end_sample"] = sample_cursor

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    prepare(parser.parse_args().manifest)
