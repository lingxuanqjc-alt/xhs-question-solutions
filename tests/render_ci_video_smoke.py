"""Render real three-frame v1/v2 media in CI without downloading a browser."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents" / "skills" / "xhs-question-solutions" / "scripts"


def find_browser():
    candidates = [shutil.which(name) for name in ("google-chrome", "chromium", "chromium-browser", "msedge")]
    for variable, suffixes in (
        ("PROGRAMFILES", ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe")),
        ("PROGRAMFILES(X86)", ("Microsoft/Edge/Application/msedge.exe",)),
        ("LOCALAPPDATA", ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe")),
    ):
        base = os.environ.get(variable)
        if base:
            candidates.extend(Path(base) / suffix for suffix in suffixes)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise RuntimeError("CI requires a preinstalled Chrome, Chromium, or Edge executable; no browser is downloaded")


def render_project(project_dir, browser, voiced):
    command = [
        sys.executable, "-X", "utf8", str(SCRIPTS / "render_video.py"),
        "--project-dir", str(project_dir), "--mp4", "--browser", str(browser),
        "--frame-range", "0:2",
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    summary_path = project_dir / "mp4-render-summary.frames-0-2.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))["videos"]
    if len(summary) != 1:
        raise AssertionError(summary)
    item = summary[0]
    output = project_dir / item["output"]
    payload = output.read_bytes()
    probe = item["probe"]
    assert len(payload) > 16 and b"ftyp" in payload[:64]
    assert item["codec"] == "h264" and item["rendered_frame_range"] == [0, 2]
    assert item["file_size"] == len(payload) and (probe["width"], probe["height"]) == (1080, 1920)
    if voiced:
        assert item["audio"] == "aac"
        assert (probe["audio_streams"], probe["audio_codec"], probe["audio_sample_rate"], probe["audio_channels"]) == (1, "aac", 48_000, 1)
    else:
        assert item["audio"] == "none" and probe["audio_streams"] == 0


def verify_overflow_fails(v1_dir, browser):
    props = list(v1_dir.glob("*.props.json"))
    if len(props) != 1:
        raise AssertionError(props)
    data = json.loads(props[0].read_text(encoding="utf-8"))
    scene = data["video"]["scenes"][0]
    scene["narration"] = "W" * 40
    scene["captions"] = [{"text": "W" * 40, "startMs": 200, "endMs": 2700, "timestampMs": None, "confidence": None}]
    overflow_props = v1_dir / "overflow.props.json"
    overflow_output = v1_dir / "overflow.mp4"
    overflow_props.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    try:
        result = subprocess.run(
            [shutil.which("node") or "node", str(SCRIPTS / "render_video.mjs"), "--props", str(overflow_props),
             "--output", str(overflow_output), "--browser", str(browser), "--frame-range", "0:2"],
            cwd=ROOT, text=True, encoding="utf-8", capture_output=True, check=False,
        )
        assert result.returncode != 0 and "CAPTION_OVERFLOW" in (result.stderr + result.stdout)
        assert not overflow_output.exists()
    finally:
        overflow_props.unlink(missing_ok=True)
        overflow_output.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("v1_dir", type=Path)
    parser.add_argument("v2_dir", type=Path)
    args = parser.parse_args()
    browser = find_browser()
    render_project(args.v1_dir, browser, voiced=False)
    render_project(args.v2_dir, browser, voiced=True)
    verify_overflow_fails(args.v1_dir, browser)
    print(json.dumps({"status": "ok", "browser": browser.name, "v1": "h264/no-audio", "v2": "h264/aac-48k-mono"}))


if __name__ == "__main__":
    main()
