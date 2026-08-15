#!/usr/bin/env node
import fs from "node:fs";
import crypto from "node:crypto";
import os from "node:os";
import path from "node:path";
import {spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import {bundle} from "@remotion/bundler";
import {renderMedia, renderStill, selectComposition} from "@remotion/renderer";

const here = path.dirname(fileURLToPath(import.meta.url));
const skillRoot = path.resolve(here, "..");
const V1_SCHEMA = "xhs-video/v1";
const V2_SCHEMA = "xhs-video/v2";
let validationSchema = V1_SCHEMA;

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
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(`Invalid ${validationSchema} props: ${location} must be an object`, 3);
  const keys = Object.keys(value);
  const missing = expected.filter((key) => !keys.includes(key));
  const unknown = keys.filter((key) => !expected.includes(key) && !optional.includes(key));
  if (missing.length || unknown.length) fail(`Invalid ${validationSchema} props: ${location} fields missing=[${missing}] unknown=[${unknown}]`, 3);
};

const sameList = (left, right) => Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((value, index) => value === right[index]);
const requiredString = (value, location) => {
  if (typeof value !== "string" || value.trim().length === 0) fail(`Invalid ${validationSchema} props: ${location} must be a non-empty string`, 3);
};
const requiredInteger = (value, location) => {
  if (!Number.isInteger(value)) fail(`Invalid ${validationSchema} props: ${location} must be an integer`, 3);
};

const isStringList = (value) => Array.isArray(value) && value.every((item) => typeof item === "string" && item.trim().length > 0);
const mobileWideRanges = [
  [0x1100, 0x11ff], [0x2329, 0x232a], [0x2600, 0x27bf],
  [0x2e80, 0xa4cf], [0xac00, 0xd7ff], [0xf900, 0xfaff],
  [0xfe10, 0xfe19], [0xfe30, 0xfe6f], [0xff01, 0xff60],
  [0xffe0, 0xffe6], [0x1f000, 0x1faff], [0x20000, 0x3fffd],
];
const mobileZeroWidthRanges = [
  [0x0300, 0x036f], [0x1ab0, 0x1aff], [0x1dc0, 0x1dff],
  [0x20d0, 0x20ff], [0xfe00, 0xfe0f], [0xfe20, 0xfe2f],
  [0xe0100, 0xe01ef],
];
const displayUnits = (value) => [...String(value)].reduce((total, character) => {
  const codepoint = character.codePointAt(0);
  if (codepoint === 0x200d || mobileZeroWidthRanges.some(([start, end]) => codepoint >= start && codepoint <= end)) return total;
  const wide = mobileWideRanges.some(([start, end]) => codepoint >= start && codepoint <= end);
  return total + (wide ? 1 : 0.5);
}, 0);
const containsAiAudioLabel = (value) => {
  const compact = String(value ?? "").normalize("NFKC").replace(/[\s，。！？!?；;：:、·]+/gu, "").toUpperCase();
  const hasAi = compact.includes("AI") || compact.includes("人工智能");
  return hasAi && ["旁白", "配音", "声音", "音频", "语音"].some((term) => compact.includes(term));
};
const validateCommonTypes = (video, schema = V1_SCHEMA) => {
  const invalid = (location, expected) => fail(`Invalid ${schema} props: ${location} must be ${expected}`, 3);
  const meta = video?.meta;
  if (!meta || typeof meta !== "object" || Array.isArray(meta)) invalid("$props.video.meta", "an object");
  for (const field of ["candidate_count", "question_count", "excluded_count", "comments_total", "comments_collected"]) if (!Number.isInteger(meta[field])) invalid(`$props.video.meta.${field}`, "an integer");
  for (const field of ["source", "captured_at", "failure_reason", "risk_level", "publish_status", "interest_disclosure"]) if (typeof meta[field] !== "string") invalid(`$props.video.meta.${field}`, "a string");
  for (const field of ["is_truncated", "ai_assisted"]) if (typeof meta[field] !== "boolean") invalid(`$props.video.meta.${field}`, "a boolean");
  if (!Array.isArray(video?.appendix?.evidence)) invalid("$props.video.appendix.evidence", "a list");
  for (const [index, item] of video.appendix.evidence.entries()) {
    for (const field of ["category", "category_label", "author", "likes_label", "thread_id", "excerpt"]) if (typeof item?.[field] !== "string") invalid(`appendix.evidence[${index}].${field}`, "a string");
    if (!Number.isInteger(item?.likes)) invalid(`appendix.evidence[${index}].likes`, "an integer");
  }
  if (!Array.isArray(video?.scenes)) invalid("$props.video.scenes", "a list");
  for (const [index, scene] of video.scenes.entries()) {
    const item = scene?.content;
    if (!item || typeof item !== "object" || Array.isArray(item)) invalid(`scenes[${index}].content`, "an object");
    const strings = {hook: ["social_title", "question", "summary"], scope: ["source_label", "captured_at_label", "coverage", "failure_reason_label"], action: ["text", "verification"], evidence: ["boundary"], conflict_risk: ["risk_level", "publish_status"], risk_unknowns: ["risk_level", "publish_status"], disclosure: ["coverage", "source_label", "failure_reason_label", "interest_disclosure", "publish_status", "evidence_index"], cta: ["question", "stop_message"]};
    for (const field of strings[scene.role] || []) if (typeof item[field] !== "string") invalid(`scenes[${index}].content.${field}`, "a string");
    if (scene.role === "scope") {
      for (const field of ["candidate_count", "question_count", "excluded_count"]) if (!Number.isInteger(item[field])) invalid(`scenes[${index}].content.${field}`, "an integer");
      if (typeof item.is_truncated !== "boolean") invalid(`scenes[${index}].content.is_truncated`, "a boolean");
    }
    if (scene.role === "action") {
      if (!Number.isInteger(item.step_number)) invalid(`scenes[${index}].content.step_number`, "an integer");
      for (const field of ["applies_when", "stop_conditions"]) if (!isStringList(item[field])) invalid(`scenes[${index}].content.${field}`, "a non-empty string list");
    }
    if (scene.role === "evidence") for (const field of ["experience", "counterexample"]) {
      const value = item[field];
      if (value !== null && (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).sort().join(",") !== "claim,comment_id" || typeof value.claim !== "string" || typeof value.comment_id !== "string")) invalid(`scenes[${index}].content.${field}`, "null or a comment binding");
    }
    if (scene.role === "conflict_risk") {
      if (!Array.isArray(item.conflicts)) invalid(`scenes[${index}].content.conflicts`, "a list");
      for (const [conflictIndex, conflict] of item.conflicts.entries()) {
        if (!conflict || typeof conflict !== "object" || Array.isArray(conflict) || Object.keys(conflict).sort().join(",") !== "positions,topic" || typeof conflict.topic !== "string" || !Array.isArray(conflict.positions)) invalid(`scenes[${index}].content.conflicts[${conflictIndex}]`, "a topic/positions object");
        for (const [positionIndex, position] of conflict.positions.entries()) if (!position || typeof position !== "object" || Array.isArray(position) || Object.keys(position).sort().join(",") !== "claim,evidence_comment_ids" || typeof position.claim !== "string" || !isStringList(position.evidence_comment_ids)) invalid(`scenes[${index}].content.conflicts[${conflictIndex}].positions[${positionIndex}]`, "a claim/evidence binding");
      }
    }
    if (scene.role === "risk_unknowns") for (const field of ["unknowns", "stop_conditions"]) if (!isStringList(item[field])) invalid(`scenes[${index}].content.${field}`, "a non-empty string list");
    if (scene.role === "disclosure") for (const field of ["is_truncated", "ai_assisted", "experience_is_not_fact"]) if (typeof item[field] !== "boolean") invalid(`scenes[${index}].content.${field}`, "a boolean");
  }
};

const validateV1Props = (inputProps, voiceover = false) => {
  exactKeys(inputProps, ["schema", "video"], "$props");
  if (inputProps.schema !== "xhs-video/v1") fail("Invalid xhs-video/v1 props: schema mismatch", 3);
  const video = inputProps?.video;
  exactKeys(video, ["video_id", "note_id", "profile", "width", "height", "fps", "duration_ms", "duration_in_frames", "meta", "scenes", "appendix", "unsafe_evidence_comment_ids"], "$props.video");
  requiredString(video.video_id, "$props.video.video_id");
  requiredString(video.note_id, "$props.video.note_id");
  for (const field of ["width", "height", "fps", "duration_ms", "duration_in_frames"]) requiredInteger(video[field], `$props.video.${field}`);
  if (video.profile !== "xhs-vertical-1080x1920-v1") fail("Invalid xhs-video/v1 props: profile must be xhs-vertical-1080x1920-v1", 3);
  if (video.width !== 1080 || video.height !== 1920 || video.fps !== 30) fail("Invalid xhs-video/v1 props: profile must be 1080x1920 at 30fps", 3);
  if (video.duration_in_frames < 1800 || video.duration_in_frames > 2700) fail("Invalid xhs-video/v1 props: duration must be 1800-2700 frames", 3);
  if (!voiceover && video.duration_ms * video.fps / 1000 !== video.duration_in_frames) fail("Invalid xhs-video/v1 props: duration mismatch", 3);
  validateCommonTypes(video);
  exactKeys(video.meta, ["candidate_count", "question_count", "excluded_count", "source", "captured_at", "comments_total", "comments_collected", "is_truncated", "failure_reason", "risk_level", "publish_status", "ai_assisted", "interest_disclosure", "audio"], "$props.video.meta");
  if (JSON.stringify(video.meta.audio) !== '{"kind":"none"}') fail("Invalid xhs-video/v1 props: only audio.kind=none is supported", 3);
  exactKeys(video.appendix, ["evidence"], "$props.video.appendix");
  if (!Array.isArray(video.appendix.evidence)) fail("Invalid xhs-video/v1 props: appendix.evidence must be a list", 3);
  const evidenceIds = new Set();
  if (!Array.isArray(video.unsafe_evidence_comment_ids) || new Set(video.unsafe_evidence_comment_ids).size !== video.unsafe_evidence_comment_ids.length || video.unsafe_evidence_comment_ids.some((id) => typeof id !== "string" || id.trim().length === 0)) fail("Invalid xhs-video/v1 props: unsafe_evidence_comment_ids must be a unique non-empty string list", 3);
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
    requiredInteger(scene.index, `scenes[${index}].index`);
    requiredInteger(scene.start_ms, `scenes[${index}].start_ms`);
    requiredInteger(scene.end_ms, `scenes[${index}].end_ms`);
    if (sceneIds.has(scene.scene_id)) fail(`Invalid xhs-video/v1 props: duplicate scene ${scene.scene_id}`, 3);
    sceneIds.add(scene.scene_id);
    if (!contentFields[scene.role]) fail(`Invalid xhs-video/v1 props: unsupported scene role ${scene.role}`, 3);
    roles.push(scene.role);
    exactKeys(scene.content, contentFields[scene.role], `scenes[${index}].content`);
    if (scene.index !== index + 1 || scene.start_ms !== cursor || scene.end_ms <= cursor) fail(`Invalid xhs-video/v1 props: scene timing/index mismatch at ${index}`, 3);
    cursor = scene.end_ms;
    requiredString(scene.narration, `scenes[${index}].narration`);
    if (!Array.isArray(scene.evidence_comment_ids) || new Set(scene.evidence_comment_ids).size !== scene.evidence_comment_ids.length || scene.evidence_comment_ids.some((id) => typeof id !== "string" || id.trim().length === 0 || !evidenceIds.has(id))) fail(`Invalid xhs-video/v1 props: scene evidence mismatch at ${index}`, 3);
    if (!Array.isArray(scene.persistent_notices) || scene.persistent_notices.some((notice) => !allowedNotices.has(notice))) fail(`Invalid xhs-video/v1 props: scene notices must be a known list at ${index}`, 3);
    if (!Array.isArray(scene.captions) || scene.captions.length === 0) fail(`Invalid xhs-video/v1 props: captions missing at ${index}`, 3);
    let combined = "";
    let captionCursor = scene.start_ms;
    for (const [captionIndex, caption] of scene.captions.entries()) {
      exactKeys(caption, ["text", "startMs", "endMs", "timestampMs", "confidence"], `scenes[${index}].captions[${captionIndex}]`);
      requiredString(caption.text, `scenes[${index}].captions[${captionIndex}].text`);
      if (/[\r\n\t]/.test(caption.text) || !Number.isInteger(caption.startMs) || !Number.isInteger(caption.endMs) || caption.startMs < captionCursor || caption.endMs - caption.startMs < 1200 || caption.endMs > scene.end_ms) fail(`Invalid xhs-video/v1 props: caption timing/text mismatch at ${index}:${captionIndex}`, 3);
      const captionDurationSeconds = (caption.endMs - caption.startMs) / 1000;
      if (displayUnits(caption.text) / captionDurationSeconds > 10.0001) fail(`Invalid xhs-video/v1 props: CAPTION_DENSITY at ${index}:${captionIndex}`, 3);
      if (displayUnits(caption.text) > 20.0001) fail(`Invalid xhs-video/v1 props: CAPTION_WIDTH at ${index}:${captionIndex}`, 3);
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

const canonicalJson = (value) => {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
};
const sha256 = (value) => `sha256:${crypto.createHash("sha256").update(value).digest("hex")}`;

const readPcmWav = (file, location) => {
  const bad = (message) => fail(`Invalid xhs-video/v2 props: ${location} ${message}`, 3);
  let raw;
  try { raw = fs.readFileSync(file); } catch { bad("could not be read"); }
  if (raw.length > 9_000_000) bad("WAV is too large");
  if (raw.length < 44 || raw.toString("ascii", 0, 4) !== "RIFF" || raw.toString("ascii", 8, 12) !== "WAVE" || raw.readUInt32LE(4) !== raw.length - 8) bad("has invalid RIFF structure");
  let offset = 12; let format = null; let data = null;
  while (offset < raw.length) {
    if (offset + 8 > raw.length) bad("has a truncated chunk header");
    const kind = raw.toString("ascii", offset, offset + 4); const size = raw.readUInt32LE(offset + 4); const start = offset + 8; const end = start + size; const padded = end + (size % 2);
    if (end > raw.length || padded > raw.length) bad("has a truncated chunk");
    if (kind === "fmt " && size >= 16) format = {tag: raw.readUInt16LE(start), channels: raw.readUInt16LE(start + 2), rate: raw.readUInt32LE(start + 4), bits: raw.readUInt16LE(start + 14)};
    if (kind === "data") { if (data) bad("has multiple data chunks"); data = {start, size}; }
    offset = padded;
  }
  if (!format || !data || format.tag !== 1 || format.channels !== 1 || format.rate !== 48_000 || format.bits !== 16 || data.size % 2) bad("must be PCM s16le 48000Hz mono");
  let activity = 0;
  for (let index = data.start; index < data.start + data.size; index += 2) if (Math.abs(raw.readInt16LE(index)) >= 256 && ++activity >= 480) break;
  if (activity < 480) bad("failed the near-silence gate");
  return {raw, samples: data.size / 2};
};

const resolveAudio = (propsDir, rawPath, location) => {
  if (typeof rawPath !== "string" || !rawPath || rawPath.includes("\\") || path.posix.isAbsolute(rawPath) || rawPath.split("/").some((part) => !part || part === "." || part === "..")) fail(`Invalid xhs-video/v2 props: ${location} must be a safe relative POSIX path`, 3);
  const base = fs.realpathSync(propsDir); const candidate = path.resolve(base, ...rawPath.split("/"));
  let real;
  try { real = fs.realpathSync(candidate); } catch { fail(`Invalid xhs-video/v2 props: ${location} audio file is missing`, 3); }
  if (real !== base && !real.startsWith(`${base}${path.sep}`)) fail(`Invalid xhs-video/v2 props: ${location} escapes the props directory`, 3);
  return real;
};

const validateV2Props = (inputProps, propsDir) => {
  exactKeys(inputProps, ["schema", "video"], "$props");
  const video = inputProps?.video;
  exactKeys(video, ["video_id", "note_id", "profile", "width", "height", "fps", "duration_ms", "duration_in_frames", "meta", "scenes", "appendix", "unsafe_evidence_comment_ids"], "$props.video");
  validateCommonTypes(video, V2_SCHEMA);
  for (const [index, scene] of video.scenes.entries()) {
    exactKeys(scene, ["scene_id", "index", "role", "start_ms", "end_ms", "content", "narration", "captions", "evidence_comment_ids", "persistent_notices", "start_frame", "end_frame", "audio"], `scenes[${index}]`);
    if (!Array.isArray(scene.persistent_notices)) fail(`Invalid xhs-video/v2 props: scenes[${index}].persistent_notices must be a list`, 3);
  }
  const normalized = JSON.parse(JSON.stringify(inputProps)); normalized.schema = V1_SCHEMA; normalized.video.profile = "xhs-vertical-1080x1920-v1"; normalized.video.meta.audio = {kind: "none"};
  normalized.video.scenes = normalized.video.scenes.map((scene) => { const result = {}; for (const key of ["scene_id", "index", "role", "start_ms", "end_ms", "content", "narration", "captions", "evidence_comment_ids", "persistent_notices"]) result[key] = scene[key]; result.persistent_notices = result.persistent_notices.filter((notice) => notice !== "synthetic_audio"); return result; });
  validateV1Props(normalized, true);
  if (video.profile !== "xhs-vertical-1080x1920-v2-voiced") fail("Invalid xhs-video/v2 props: profile mismatch", 3);
  const audio = video.meta.audio;
  exactKeys(audio, ["kind", "layout", "origin", "reviewed", "rights_basis", "rights_confirmed", "disclosure_required", "disclosure_text", "signal_check", "attestation"], "$props.video.meta.audio");
  const rights = {human_recorded: new Set(["self_recorded", "licensed"]), synthetic_ai: new Set(["synthetic_service_terms_confirmed", "licensed"])};
  if (audio.kind !== "external_voiceover" || audio.layout !== "per_scene" || typeof audio.origin !== "string" || typeof audio.rights_basis !== "string" || !rights[audio.origin]?.has(audio.rights_basis) || audio.reviewed !== true || audio.rights_confirmed !== true) fail("Invalid xhs-video/v2 props: audio origin/rights mismatch", 3);
  const synthetic = audio.origin === "synthetic_ai";
  if (audio.disclosure_required !== synthetic || audio.disclosure_text !== (synthetic ? "旁白由AI合成" : null) || !audio.signal_check || Object.keys(audio.signal_check).sort().join(",") !== "audibility_verified,kind" || audio.signal_check.kind !== "basic_pcm_activity" || audio.signal_check.audibility_verified !== false) fail("Invalid xhs-video/v2 props: audio disclosure/signal mismatch", 3);
  let cursor = 0; const hashes = [];
  for (const [index, scene] of video.scenes.entries()) {
    exactKeys(scene, ["scene_id", "index", "role", "start_ms", "end_ms", "content", "narration", "captions", "evidence_comment_ids", "persistent_notices", "start_frame", "end_frame", "audio"], `scenes[${index}]`);
    if (synthetic && scene.role === "hook" && scene.captions.some((caption) => containsAiAudioLabel(caption.text))) fail("Invalid xhs-video/v2 props: hook captions must not duplicate or conflict with the structured first-frame AI label", 3);
    if (!Number.isInteger(scene.start_frame) || !Number.isInteger(scene.end_frame) || scene.start_frame !== cursor || scene.end_frame <= cursor || scene.start_ms !== Math.round(scene.start_frame * 1000 / 30) || scene.end_ms !== Math.round(scene.end_frame * 1000 / 30)) fail(`Invalid xhs-video/v2 props: audio timeline mismatch at ${index}`, 3);
    if (scene.persistent_notices.includes("synthetic_audio") !== (synthetic && ["hook", "disclosure"].includes(scene.role))) fail(`Invalid xhs-video/v2 props: synthetic_audio notice mismatch at ${index}`, 3);
    cursor = scene.end_frame;
    const clip = scene.audio;
    exactKeys(clip, ["kind", "path", "sha256", "narration_sha256", "codec", "sample_rate_hz", "channels", "bits_per_sample", "sample_count"], `scenes[${index}].audio`);
    const file = resolveAudio(propsDir, clip.path, `scenes[${index}].audio.path`); const parsed = readPcmWav(file, `scenes[${index}].audio.path`); const digest = sha256(parsed.raw);
    if (clip.kind !== "external_voiceover_clip" || clip.codec !== "pcm_s16le" || clip.sample_rate_hz !== 48_000 || clip.channels !== 1 || clip.bits_per_sample !== 16 || clip.sample_count !== parsed.samples) fail(`Invalid xhs-video/v2 props: audio metadata mismatch at ${index}`, 3);
    if (clip.sha256 !== digest) fail(`Invalid xhs-video/v2 props: audio hash mismatch at ${index}`, 3);
    if (clip.path !== `assets/voiceover/${digest.slice(7)}.wav` || scene.end_frame - scene.start_frame !== Math.ceil(parsed.samples / 1600)) fail(`Invalid xhs-video/v2 props: audio path/timeline binding mismatch at ${index}`, 3);
    if (clip.narration_sha256 !== sha256(Buffer.from(scene.narration, "utf8"))) fail(`Invalid xhs-video/v2 props: narration hash mismatch at ${index}`, 3);
    hashes.push(digest);
  }
  if (video.duration_in_frames !== cursor || video.duration_ms !== Math.round(cursor * 1000 / 30)) fail("Invalid xhs-video/v2 props: final audio timeline mismatch", 3);
  if (synthetic) for (const role of ["hook", "disclosure"]) if (!video.scenes.find((scene) => scene.role === role)?.persistent_notices.includes("synthetic_audio")) fail(`Invalid xhs-video/v2 props: missing ${role} synthetic_audio notice`, 3);
  const att = audio.attestation;
  exactKeys(att, ["kind", "source_ir_sha256", "manifest_sha256", "video_id", "origin", "rights_basis", "audio_sha256", "audio_reviewed", "audio_rights_confirmed", "license_verified_by_tool", "sha256"], "$props.video.meta.audio.attestation");
  const binding = Object.fromEntries(Object.entries(att).filter(([key]) => !["kind", "sha256"].includes(key)));
  const digestFields = [att.source_ir_sha256, att.manifest_sha256, att.sha256].every((value) => /^sha256:[0-9a-f]{64}$/.test(value));
  if (!digestFields || att.kind !== "user_declared_review_and_rights" || !sameList(att.audio_sha256, hashes) || att.video_id !== video.video_id || att.origin !== audio.origin || att.rights_basis !== audio.rights_basis || att.audio_reviewed !== true || att.audio_rights_confirmed !== true || att.license_verified_by_tool !== false || att.sha256 !== sha256(Buffer.from(canonicalJson(binding), "utf8"))) fail("Invalid xhs-video/v2 props: attestation mismatch", 3);
  return video;
};

const validateProps = (inputProps, propsDir) => {
  validationSchema = inputProps?.schema === V2_SCHEMA ? V2_SCHEMA : V1_SCHEMA;
  if (inputProps?.schema === V2_SCHEMA) return validateV2Props(inputProps, propsDir);
  return validateV1Props(inputProps);
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

const pinnedMediaBinary = (name) => {
  const packageRoot = path.join(skillRoot, "node_modules", "@remotion");
  const executable = process.platform === "win32" ? `${name}.exe` : name;
  return fs.readdirSync(packageRoot)
    .filter((name) => name.startsWith("compositor-"))
    .map((name) => path.join(packageRoot, name, executable))
    .find((value) => fs.existsSync(value));
};

const forceMonoAac = (file) => {
  const executable = pinnedMediaBinary("ffmpeg");
  if (!executable) fail("Pinned Remotion ffmpeg binary was not found", 6);
  const temporary = `${file}.mono-${process.pid}.mp4`;
  try {
    const result = spawnSync(executable, ["-v", "error", "-i", file, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "1", "-movflags", "+faststart", temporary], {encoding: "utf8"});
    if (result.status !== 0 || !fs.existsSync(temporary) || !hasFtyp(temporary)) fail(`mono AAC normalization failed: ${result.stderr || result.stdout}`, 6);
    fs.rmSync(file); fs.renameSync(temporary, file);
  } finally {
    fs.rmSync(temporary, {force: true});
  }
};

const probeMedia = (file, expectedDuration, voiced) => {
  const candidate = pinnedMediaBinary("ffprobe");
  if (!candidate) fail("Pinned Remotion ffprobe binary was not found", 6);
  const result = spawnSync(candidate, ["-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,sample_rate,channels,duration:format=duration", "-of", "json", file], {encoding: "utf8"});
  if (result.status !== 0) fail(`ffprobe failed: ${result.stderr || result.stdout}`, 6);
  const data = JSON.parse(result.stdout);
  const videos = (data.streams || []).filter((stream) => stream.codec_type === "video");
  const audios = (data.streams || []).filter((stream) => stream.codec_type === "audio");
  const duration = Number(data.format?.duration);
  const videoDuration = Number(videos[0]?.duration);
  const audioDuration = Number(audios[0]?.duration);
  const audioOk = voiced ? audios.length === 1 && audios[0].codec_name === "aac" && Number(audios[0].sample_rate) === 48_000 && audios[0].channels === 1 : audios.length === 0;
  const durationsOk = Number.isFinite(duration) && Number.isFinite(videoDuration) && Math.abs(duration - expectedDuration) <= 0.2 && Math.abs(videoDuration - expectedDuration) <= 0.2 &&
    (!voiced || (Number.isFinite(audioDuration) && Math.abs(audioDuration - expectedDuration) <= 0.2 && Math.abs(audioDuration - videoDuration) <= 0.2));
  if (videos.length !== 1 || videos[0].codec_name !== "h264" || videos[0].width !== 1080 || videos[0].height !== 1920 || !audioOk || !durationsOk) {
    fail(`ffprobe metadata does not match ${voiced ? "voiced" : "silent"} 1080x1920 H.264 output: ${JSON.stringify(data)}`, 6);
  }
  return {codec: videos[0].codec_name, width: videos[0].width, height: videos[0].height, audio_streams: audios.length,
    audio_codec: audios[0]?.codec_name ?? null, audio_sample_rate: audios[0] ? Number(audios[0].sample_rate) : null,
    audio_channels: audios[0]?.channels ?? null, duration_seconds: duration, video_duration_seconds: videoDuration,
    audio_duration_seconds: voiced ? audioDuration : null};
};

const main = async () => {
  const args = parseArgs();
  const propsPath = path.resolve(args.props);
  const output = path.resolve(args.output);
  if (!fs.existsSync(propsPath)) fail(`Props file does not exist: ${propsPath}`, 2);
  const inputProps = JSON.parse(fs.readFileSync(propsPath, "utf8"));
  const video = validateProps(inputProps, path.dirname(propsPath));
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
      publicDir: path.dirname(propsPath),
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
    const voiced = inputProps.schema === V2_SCHEMA;
    await renderMedia({composition, serveUrl, codec: "h264", pixelFormat: "yuv420p", outputLocation: output, inputProps,
      muted: !voiced, audioCodec: voiced ? "aac" : undefined, sampleRate: voiced ? 48_000 : undefined,
      overwrite: false, concurrency: 1, x264Preset: frameRange ? "superfast" : "medium", frameRange,
      logLevel: "warn", ...browserOptions});
    if (!fs.existsSync(output) || fs.statSync(output).size < 16 || !hasFtyp(output)) fail("Renderer did not produce a valid MP4", 6);
    if (voiced) forceMonoAac(output);
    const renderedFrames = frameRange ? frameRange[1] - frameRange[0] + 1 : composition.durationInFrames;
    const probe = probeMedia(output, renderedFrames / composition.fps, voiced);
    process.stdout.write(`${JSON.stringify({
      codec: "h264",
      width: composition.width,
      height: composition.height,
      fps: composition.fps,
      duration_in_frames: composition.durationInFrames,
      rendered_frame_range: frameRange ?? null,
      audio: voiced ? "aac" : "none",
      file_size: fs.statSync(output).size,
      browser: path.basename(browserExecutable),
      probe,
    })}\n`);
  } finally {
    fs.rmSync(bundleDir, {recursive: true, force: true});
  }
};

main().catch((error) => fail(error?.stack || error?.message || String(error)));
