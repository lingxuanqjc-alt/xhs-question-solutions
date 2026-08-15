#!/usr/bin/env node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import {bundle} from "@remotion/bundler";
import {renderMedia, renderStill, selectComposition} from "@remotion/renderer";

const here = path.dirname(fileURLToPath(import.meta.url));
const skillRoot = path.resolve(here, "..");

const fail = (message, code = 1) => {
  process.stderr.write(`${message}\n`);
  process.exit(code);
};

const parseArgs = () => {
  const result = {};
  const argv = process.argv.slice(2);
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    if (!key.startsWith("--") || argv[index + 1] === undefined) fail(`Invalid argument: ${key}`, 2);
    result[key.slice(2)] = argv[index + 1];
  }
  if (!result.props || !result.output) fail("Usage: render_video.mjs --props <props.json> --output <video.mp4|stills-dir> [--browser <path>] [--frame-range 0:2 | --still-frames 45,165]", 2);
  return result;
};

const browserCandidates = (explicit) => {
  const values = [
    explicit,
    process.env.REMOTION_BROWSER_EXECUTABLE,
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
    process.platform === "win32" && "C:/Program Files/Google/Chrome/Application/chrome.exe",
    process.platform === "win32" && "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    process.platform === "win32" && "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    process.platform === "darwin" && "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    process.platform === "darwin" && "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    process.platform === "linux" && "/usr/bin/google-chrome",
    process.platform === "linux" && "/usr/bin/chromium",
    process.platform === "linux" && "/usr/bin/chromium-browser",
  ];
  return [...new Set(values.filter(Boolean).map((value) => path.resolve(value)))];
};

const resolveBrowser = (explicit) => {
  const browser = browserCandidates(explicit).find((candidate) => fs.existsSync(candidate));
  if (!browser) fail("No local Chromium, Edge, or Chrome was found. Set REMOTION_BROWSER_EXECUTABLE; automatic browser downloads are disabled.", 4);
  return browser;
};

const exactKeys = (value, expected, location, optional = []) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(`Invalid xhs-video/v1 props: ${location} must be an object`, 3);
  const keys = Object.keys(value);
  const missing = expected.filter((key) => !keys.includes(key));
  const unknown = keys.filter((key) => !expected.includes(key) && !optional.includes(key));
  if (missing.length || unknown.length) fail(`Invalid xhs-video/v1 props: ${location} fields missing=[${missing}] unknown=[${unknown}]`, 3);
};

const sameList = (left, right) => Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((value, index) => value === right[index]);
const requiredString = (value, location) => {
  if (typeof value !== "string" || value.length === 0) fail(`Invalid xhs-video/v1 props: ${location} must be a non-empty string`, 3);
};

const validateProps = (inputProps) => {
  exactKeys(inputProps, ["schema", "video"], "$props");
  if (inputProps.schema !== "xhs-video/v1") fail("Invalid xhs-video/v1 props: schema mismatch", 3);
  const video = inputProps?.video;
  exactKeys(video, ["video_id", "note_id", "profile", "width", "height", "fps", "duration_ms", "duration_in_frames", "meta", "scenes", "appendix", "unsafe_evidence_comment_ids"], "$props.video");
  if (video.profile !== "xhs-vertical-1080x1920-v1") fail("Props must contain one validated xhs-vertical-1080x1920-v1 video", 3);
  if (video.width !== 1080 || video.height !== 1920 || video.fps !== 30) fail("Video profile must be 1080x1920 at 30fps", 3);
  if (!Number.isInteger(video.duration_in_frames) || video.duration_in_frames < 1800 || video.duration_in_frames > 2700) fail("Video duration must be 1800-2700 frames", 3);
  if (!Number.isInteger(video.duration_ms) || video.duration_ms * video.fps / 1000 !== video.duration_in_frames) fail("Invalid xhs-video/v1 props: duration mismatch", 3);
  exactKeys(video.meta, ["candidate_count", "question_count", "excluded_count", "source", "captured_at", "comments_total", "comments_collected", "is_truncated", "failure_reason", "risk_level", "publish_status", "ai_assisted", "interest_disclosure", "audio"], "$props.video.meta");
  if (JSON.stringify(video.meta.audio) !== '{"kind":"none"}') fail("Invalid xhs-video/v1 props: only audio.kind=none is supported", 3);
  exactKeys(video.appendix, ["evidence"], "$props.video.appendix");
  if (!Array.isArray(video.appendix.evidence)) fail("Invalid xhs-video/v1 props: appendix.evidence must be a list", 3);
  const evidenceIds = new Set();
  if (!Array.isArray(video.unsafe_evidence_comment_ids) || new Set(video.unsafe_evidence_comment_ids).size !== video.unsafe_evidence_comment_ids.length || video.unsafe_evidence_comment_ids.some((id) => typeof id !== "string" || id.length === 0)) fail("Invalid xhs-video/v1 props: unsafe_evidence_comment_ids must be a unique string list", 3);
  const unsafeIds = new Set(video.unsafe_evidence_comment_ids);
  for (const [index, item] of video.appendix.evidence.entries()) {
    exactKeys(item, ["comment_id", "category", "category_label", "author", "likes", "likes_label", "thread_id", "excerpt"], `appendix.evidence[${index}]`, ["safety_warning"]);
    requiredString(item.comment_id, `appendix.evidence[${index}].comment_id`);
    if (evidenceIds.has(item.comment_id)) fail(`Invalid xhs-video/v1 props: duplicate evidence ${item.comment_id}`, 3);
    evidenceIds.add(item.comment_id);
    if (unsafeIds.has(item.comment_id) && item.safety_warning !== "未核验高风险观点，不是操作建议") fail(`Invalid xhs-video/v1 props: unsafe evidence ${item.comment_id} lost its appendix warning`, 3);
    if (!unsafeIds.has(item.comment_id) && item.safety_warning !== undefined) fail(`Invalid xhs-video/v1 props: evidence ${item.comment_id} has an unexpected safety warning`, 3);
  }
  if ([...unsafeIds].some((id) => !evidenceIds.has(id))) fail("Invalid xhs-video/v1 props: unsafe evidence manifest contains an unknown comment", 3);
  if (!Array.isArray(video.scenes) || video.scenes.length === 0) fail("Video must contain scenes", 3);
  const sceneFields = ["scene_id", "index", "role", "start_ms", "end_ms", "content", "narration", "captions", "evidence_comment_ids", "persistent_notices"];
  const allowedNotices = new Set(["unsafe_unverified_not_advice", "synthetic_demo", "truncated_sample", "high_risk_needs_review", "experience_not_fact", "ai_assisted"]);
  const contentFields = {
    hook: ["social_title", "question", "summary"], scope: ["candidate_count", "question_count", "excluded_count", "source_label", "captured_at_label", "coverage", "is_truncated", "failure_reason_label"],
    action: ["step_number", "text", "applies_when", "verification", "stop_conditions"], evidence: ["experience", "counterexample", "boundary"],
    conflict_risk: ["conflicts", "risk_level", "publish_status"], risk_unknowns: ["risk_level", "publish_status", "unknowns", "stop_conditions"],
    disclosure: ["coverage", "source_label", "is_truncated", "failure_reason_label", "ai_assisted", "experience_is_not_fact", "interest_disclosure", "publish_status", "evidence_index"],
    cta: ["question", "stop_message"],
  };
  let cursor = 0;
  const roles = [];
  const sceneIds = new Set();
  for (const [index, scene] of video.scenes.entries()) {
    exactKeys(scene, sceneFields, `scenes[${index}]`);
    requiredString(scene.scene_id, `scenes[${index}].scene_id`);
    if (sceneIds.has(scene.scene_id)) fail(`Invalid xhs-video/v1 props: duplicate scene ${scene.scene_id}`, 3);
    sceneIds.add(scene.scene_id);
    if (!contentFields[scene.role]) fail(`Invalid xhs-video/v1 props: unsupported scene role ${scene.role}`, 3);
    roles.push(scene.role);
    exactKeys(scene.content, contentFields[scene.role], `scenes[${index}].content`);
    if (scene.index !== index + 1 || scene.start_ms !== cursor || !Number.isInteger(scene.end_ms) || scene.end_ms <= cursor) fail(`Invalid xhs-video/v1 props: scene timing/index mismatch at ${index}`, 3);
    cursor = scene.end_ms;
    requiredString(scene.narration, `scenes[${index}].narration`);
    if (!Array.isArray(scene.evidence_comment_ids) || new Set(scene.evidence_comment_ids).size !== scene.evidence_comment_ids.length || scene.evidence_comment_ids.some((id) => !evidenceIds.has(id))) fail(`Invalid xhs-video/v1 props: scene evidence mismatch at ${index}`, 3);
    if (!Array.isArray(scene.persistent_notices) || scene.persistent_notices.some((notice) => !allowedNotices.has(notice))) fail(`Invalid xhs-video/v1 props: scene notices must be a known list at ${index}`, 3);
    if (!Array.isArray(scene.captions) || scene.captions.length === 0) fail(`Invalid xhs-video/v1 props: captions missing at ${index}`, 3);
    let combined = "";
    let captionCursor = scene.start_ms;
    for (const [captionIndex, caption] of scene.captions.entries()) {
      exactKeys(caption, ["text", "startMs", "endMs", "timestampMs", "confidence"], `scenes[${index}].captions[${captionIndex}]`);
      requiredString(caption.text, `scenes[${index}].captions[${captionIndex}].text`);
      if (/[\r\n\t]/.test(caption.text) || !Number.isInteger(caption.startMs) || !Number.isInteger(caption.endMs) || caption.startMs < captionCursor || caption.endMs - caption.startMs < 1200 || caption.endMs > scene.end_ms) fail(`Invalid xhs-video/v1 props: caption timing/text mismatch at ${index}:${captionIndex}`, 3);
      if (caption.timestampMs !== null || caption.confidence !== null) fail(`Invalid xhs-video/v1 props: caption metadata mismatch at ${index}:${captionIndex}`, 3);
      captionCursor = caption.endMs;
      combined += caption.text;
    }
    if (combined !== scene.narration) fail(`Invalid xhs-video/v1 props: caption narration mismatch at ${index}`, 3);
    let derivedEvidence = null;
    if (scene.role === "conflict_risk") derivedEvidence = [...new Set(scene.content.conflicts.flatMap((conflict) => conflict.positions.flatMap((position) => position.evidence_comment_ids)))];
    if (scene.role === "evidence") derivedEvidence = [scene.content.experience, scene.content.counterexample].filter(Boolean).map((item) => item.comment_id);
    if (derivedEvidence && !sameList(scene.evidence_comment_ids, derivedEvidence)) fail(`Invalid xhs-video/v1 props: content evidence mismatch at ${index}`, 3);
    const hasUnsafe = scene.evidence_comment_ids.some((id) => unsafeIds.has(id));
    const hasNotice = scene.persistent_notices.includes("unsafe_unverified_not_advice");
    if (hasUnsafe && (!hasNotice || !scene.narration.startsWith("未核验高风险观点，不是操作建议。") || !scene.captions[0].text.startsWith("未核验高风险观点，不是操作建议。"))) fail(`Invalid xhs-video/v1 props: unsafe evidence lost its same-scene warning at ${index}`, 3);
    if (!hasUnsafe && hasNotice) fail(`Invalid xhs-video/v1 props: unsafe notice has no unsafe evidence at ${index}`, 3);
  }
  const actionCount = roles.filter((role) => role === "action").length;
  const expectedRoles = ["hook", "scope", ...Array(actionCount).fill("action"), "evidence", "conflict_risk", "risk_unknowns", "disclosure", "cta"];
  if (actionCount < 1 || actionCount > 5 || !sameList(roles, expectedRoles) || cursor !== video.duration_ms) fail("Invalid xhs-video/v1 props: scene order or final duration mismatch", 3);
  return video;
};

const parseFrameRange = (raw, duration) => {
  if (!raw) return undefined;
  const match = /^(\d+):(\d+)$/.exec(raw);
  if (!match) fail("--frame-range must look like 0:2", 2);
  const range = [Number(match[1]), Number(match[2])];
  if (range[0] > range[1] || range[1] >= duration) fail("--frame-range is outside the composition", 2);
  return range;
};

const parseStillFrames = (raw, duration) => {
  if (!raw) return undefined;
  const frames = raw.split(",").map((value) => Number(value));
  if (!frames.length || frames.some((value) => !Number.isInteger(value) || value < 0 || value >= duration)) fail("--still-frames contains an invalid frame", 2);
  return [...new Set(frames)];
};

const hasFtyp = (file) => fs.readFileSync(file).subarray(0, 64).includes(Buffer.from("ftyp"));

const probeMedia = (file, expectedDuration) => {
  const packageRoot = path.join(skillRoot, "node_modules", "@remotion");
  const executable = process.platform === "win32" ? "ffprobe.exe" : "ffprobe";
  const candidate = fs.readdirSync(packageRoot)
    .filter((name) => name.startsWith("compositor-"))
    .map((name) => path.join(packageRoot, name, executable))
    .find((value) => fs.existsSync(value));
  if (!candidate) fail("Pinned Remotion ffprobe binary was not found", 6);
  const result = spawnSync(candidate, ["-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height:format=duration", "-of", "json", file], {encoding: "utf8"});
  if (result.status !== 0) fail(`ffprobe failed: ${result.stderr || result.stdout}`, 6);
  const data = JSON.parse(result.stdout);
  const videos = (data.streams || []).filter((stream) => stream.codec_type === "video");
  const audios = (data.streams || []).filter((stream) => stream.codec_type === "audio");
  const duration = Number(data.format?.duration);
  if (videos.length !== 1 || videos[0].codec_name !== "h264" || videos[0].width !== 1080 || videos[0].height !== 1920 || audios.length !== 0 || !Number.isFinite(duration) || Math.abs(duration - expectedDuration) > 0.2) {
    fail("ffprobe metadata does not match silent 1080x1920 H.264 output", 6);
  }
  return {codec: videos[0].codec_name, width: videos[0].width, height: videos[0].height, audio_streams: audios.length, duration_seconds: duration};
};

const main = async () => {
  const args = parseArgs();
  const propsPath = path.resolve(args.props);
  const output = path.resolve(args.output);
  if (!fs.existsSync(propsPath)) fail(`Props file does not exist: ${propsPath}`, 2);
  const inputProps = JSON.parse(fs.readFileSync(propsPath, "utf8"));
  const video = validateProps(inputProps);
  const browserExecutable = resolveBrowser(args.browser);
  const frameRange = parseFrameRange(args["frame-range"], video.duration_in_frames);
  const stillFrames = parseStillFrames(args["still-frames"], video.duration_in_frames);
  if (frameRange && stillFrames) fail("Use either --frame-range or --still-frames, not both", 2);
  if (stillFrames) fs.mkdirSync(output, {recursive: true});
  else if (fs.existsSync(output)) fail(`Refusing to overwrite staging output: ${output}`, 2);
  const bundleDir = fs.mkdtempSync(path.join(os.tmpdir(), "xhs-video-bundle-"));
  try {
    const serveUrl = await bundle({
      entryPoint: path.join(skillRoot, "remotion", "index.jsx"),
      rootDir: skillRoot,
      outDir: bundleDir,
      onProgress: () => undefined,
    });
    const browserOptions = {browserExecutable, chromeMode: "chrome-for-testing"};
    const composition = await selectComposition({
      serveUrl,
      id: "XhsQuestionVideo",
      inputProps,
      ...browserOptions,
    });
    if (composition.width !== video.width || composition.height !== video.height || composition.fps !== video.fps || composition.durationInFrames !== video.duration_in_frames) {
      fail("Composition metadata does not match the validated video IR", 5);
    }
    if (stillFrames) {
      const files = [];
      for (const frame of stillFrames) {
        const filename = `frame-${String(frame).padStart(4, "0")}.png`;
        const target = path.join(output, filename);
        await renderStill({composition, serveUrl, output: target, frame, inputProps, imageFormat: "png", logLevel: "warn", ...browserOptions});
        files.push(filename);
      }
      process.stdout.write(`${JSON.stringify({width: composition.width, height: composition.height, fps: composition.fps, stills: files})}\n`);
      return;
    }
    await renderMedia({composition, serveUrl, codec: "h264", pixelFormat: "yuv420p", outputLocation: output, inputProps,
      muted: true, overwrite: false, concurrency: 1, x264Preset: frameRange ? "superfast" : "medium", frameRange,
      logLevel: "warn", ...browserOptions});
    if (!fs.existsSync(output) || fs.statSync(output).size < 16 || !hasFtyp(output)) fail("Renderer did not produce a valid MP4", 6);
    const renderedFrames = frameRange ? frameRange[1] - frameRange[0] + 1 : composition.durationInFrames;
    const probe = probeMedia(output, renderedFrames / composition.fps);
    process.stdout.write(`${JSON.stringify({
      codec: "h264",
      width: composition.width,
      height: composition.height,
      fps: composition.fps,
      duration_in_frames: composition.durationInFrames,
      rendered_frame_range: frameRange ?? null,
      audio: "none",
      file_size: fs.statSync(output).size,
      browser: path.basename(browserExecutable),
      probe,
    })}\n`);
  } finally {
    fs.rmSync(bundleDir, {recursive: true, force: true});
  }
};

main().catch((error) => fail(error?.stack || error?.message || String(error)));
