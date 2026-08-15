#!/usr/bin/env python3
"""Build and serialize the deterministic xhs-video/v1 contract."""
import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

from validate_result import load_jsonl, validate

SCHEMA = "xhs-video/v1"
PROFILE = "xhs-vertical-1080x1920-v1"
WIDTH, HEIGHT, FPS = 1080, 1920, 30
UNSAFE_NOTICE_CODE = "unsafe_unverified_not_advice"
UNSAFE_WARNING = "未核验高风险观点，不是操作建议"
ALLOWED_NOTICES = {UNSAFE_NOTICE_CODE, "synthetic_demo", "truncated_sample", "high_risk_needs_review", "experience_not_fact", "ai_assisted"}
ROLES = ("hook", "scope", "action", "evidence", "conflict_risk", "risk_unknowns", "disclosure", "cta")
TARGET_MS = {1: 60_000, 2: 68_000, 3: 75_000, 4: 84_000, 5: 90_000}
FIXED_DURATIONS = {"hook": 3_000, "scope": 5_000, "evidence": 10_000, "conflict_risk": 11_000, "risk_unknowns": 8_000, "disclosure": 7_000, "cta": 4_000}
CTA_STOP_MESSAGE_MAX_UNITS = 60
VIDEO_FIELDS = {"video_id", "note_id", "profile", "width", "height", "fps", "duration_ms", "duration_in_frames", "meta", "scenes", "appendix", "unsafe_evidence_comment_ids"}
META_FIELDS = {"candidate_count", "question_count", "excluded_count", "source", "captured_at", "comments_total", "comments_collected", "is_truncated", "failure_reason", "risk_level", "publish_status", "ai_assisted", "interest_disclosure", "audio"}
SCENE_FIELDS = {"scene_id", "index", "role", "start_ms", "end_ms", "content", "narration", "captions", "evidence_comment_ids", "persistent_notices"}
CAPTION_FIELDS = {"text", "startMs", "endMs", "timestampMs", "confidence"}
CONTENT_FIELDS = {
    "hook": {"social_title", "question", "summary"},
    "scope": {"candidate_count", "question_count", "excluded_count", "source_label", "captured_at_label", "coverage", "is_truncated", "failure_reason_label"},
    "action": {"step_number", "text", "applies_when", "verification", "stop_conditions"},
    "evidence": {"experience", "counterexample", "boundary"},
    "conflict_risk": {"conflicts", "risk_level", "publish_status"},
    "risk_unknowns": {"risk_level", "publish_status", "unknowns", "stop_conditions"},
    "disclosure": {"coverage", "source_label", "is_truncated", "failure_reason_label", "ai_assisted", "experience_is_not_fact", "interest_disclosure", "publish_status", "evidence_index"},
    "cta": {"question", "stop_message"},
}
SOURCE_LABELS = {"synthetic_fixture": "合成演示数据", "browser": "公开页面采集", "export": "用户提供的导出数据", "unknown": "来源未知"}
FAILURE_LABELS = {"reached_limit": "达到采集上限", "rate_limited": "采集频率受限", "login_required": "需要登录后继续", "timeout": "采集超时"}
RISK_LABELS = {"low": "低", "medium": "中", "high": "高"}
PUBLISH_LABELS = {"ready": "可发布", "needs_review": "需要人工复核"}


def display_units(value):
    """Approximate mobile caption width without platform-dependent font metrics."""
    total = 0.0
    for char in str(value):
        if unicodedata.combining(char) or char in "\ufe0e\ufe0f\u200d":
            continue
        total += 1.0 if unicodedata.east_asian_width(char) in {"W", "F", "A"} else 0.5
    return total


def _coverage(capture):
    total, got = capture.get("comments_total", 0), capture.get("comments_collected", 0)
    return f"页面显示 {total} 条 · 实际采集 {got} 条" if total else f"实际采集 {got} 条 · 页面总量未知"


def _stable_unique(values):
    return list(dict.fromkeys(value for value in values if value))


_CAPTION_TOKEN = re.compile(
    r"\d+(?:\.\d+)?(?:\s*(?:%|％|毫秒|分钟|小时|平方米|毫米|厘米|千克|公斤|毫升|个月|秒|天|周|月|年|米|㎡|克|升|元|块|个|次|条|步|项|种|倍|℃|°C|[A-Za-z]+))?"
    r"|[A-Za-z][A-Za-z0-9]*(?:[._/+:-][A-Za-z0-9]+)*|\s+|.",
    re.DOTALL,
)
_CAPTION_STRONG_BOUNDARIES = set("。！？!?；;")
_CAPTION_WEAK_BOUNDARIES = set("，,：:")


def _caption_atoms(text):
    return _CAPTION_TOKEN.findall(text)


def _orphan_han_chunk(text):
    visible = text.strip("。！？!?；;，,：: ")
    return bool(re.fullmatch(r"[\u3400-\u9fff]{1,2}", visible))


def _semantic_caption_chunks(text, max_units):
    """Prefer punctuation boundaries while keeping protected tokens and Han tails whole."""
    atoms, best = _caption_atoms(text), {}
    best[len(atoms)] = (0.0, [])
    for start in range(len(atoms) - 1, -1, -1):
        chunk = ""
        for end in range(start + 1, len(atoms) + 1):
            chunk += atoms[end - 1]
            units = display_units(chunk)
            if units > max_units:
                break
            if end not in best or (_orphan_han_chunk(chunk) and not (start == 0 and end == len(atoms))):
                continue
            if end == len(atoms):
                boundary_cost = 0
            elif chunk[-1] in _CAPTION_STRONG_BOUNDARIES:
                boundary_cost = 0
            elif chunk[-1] in _CAPTION_WEAK_BOUNDARIES:
                boundary_cost = 3
            elif chunk[-1].isspace():
                boundary_cost = 6
            else:
                boundary_cost = 24
            cost = 10 + boundary_cost + best[end][0] + (max_units - units) ** 2 / 100
            candidate = (cost, [chunk] + best[end][1])
            if start not in best or candidate[0] < best[start][0]:
                best[start] = candidate
    if 0 not in best:
        raise ValueError("CAPTION_TOKEN_OVERFLOW: cannot split narration without breaking a word, unit, or short Han tail")
    return best[0][1]


def _caption_chunks(narration, max_units=20):
    if not narration or any(char in narration for char in "\r\n\t"):
        raise ValueError("narration must be a non-empty single line")
    chunks = []
    safety_prefix = f"{UNSAFE_WARNING}。"
    if narration.startswith(safety_prefix):
        chunks.append(safety_prefix)
        narration = narration[len(safety_prefix):]
    if narration:
        chunks.extend(_semantic_caption_chunks(narration, max_units))
    return chunks


def _sentence(value):
    text = str(value).strip()
    return text if text.endswith(("。", "！", "？", "!", "?")) else f"{text}。"


def _captions(narration, start_ms, end_ms, edge_padding_ms=200):
    chunks = _caption_chunks(narration)
    cursor, latest = start_ms + edge_padding_ms, end_ms - edge_padding_ms
    durations = [max(1_200, math.ceil(display_units(text) / 8 * 1000)) for text in chunks]
    if sum(durations) > latest - cursor:
        durations = [max(1_200, math.ceil(display_units(text) / 10 * 1000)) for text in chunks]
    if sum(durations) > latest - cursor:
        raise ValueError(f"CAPTION_OVERFLOW: narration needs {sum(durations)}ms in {end_ms - start_ms}ms scene")
    result = []
    for text, duration in zip(chunks, durations):
        result.append({"text": text, "startMs": cursor, "endMs": cursor + duration, "timestampMs": None, "confidence": None})
        cursor += duration
    return result


def _unsafe_ids(post):
    return {item["comment_id"] for item in post["comments"] if "unsafe_advice" in item.get("risk_flags", [])}


def _scene(scene_id, index, role, start_ms, duration_ms, content, narration, evidence_ids=(), notices=()):
    return {"scene_id": scene_id, "index": index, "role": role, "start_ms": start_ms, "end_ms": start_ms + duration_ms,
            "content": content, "narration": narration, "captions": _captions(narration, start_ms, start_ms + duration_ms, 100 if role == "hook" else 200),
            "evidence_comment_ids": list(evidence_ids), "persistent_notices": list(notices)}


def _selected(items, category):
    """Pick by evidence quality, fewer risk flags, confidence, then stable comment ID."""
    quality = {"strong": 3, "moderate": 2, "weak": 1}
    candidates = [item for item in items if item.get("category") == category]
    if not candidates:
        return None
    selected = min(candidates, key=lambda item: (
        -quality.get(item.get("evidence_quality"), 0),
        len(item.get("risk_flags", [])),
        -float(item.get("confidence", 0)),
        item["comment_id"],
    ))
    return {"comment_id": selected["comment_id"], "claim": selected["claim"]}


_HOOK_BOUNDARY_MARKERS = ("不要", "不得", "不能", "不应", "不建议", "切勿", "禁止", "避免", "并非", "未", "无", "风险", "危险", "停止", "不适", "火源", "不")
_HOOK_QUALIFIERS = ("如果", "若", "时", "先", "后", "再", "需", "需要", "仅", "只", "前提", "条件")


def _hook_summary_excerpt(summary, max_units=12):
    """Select a complete continuous clause without inventing or clipping safety meaning."""
    text = str(summary).strip()
    if not text or any(char in text for char in "\r\n\t"):
        raise ValueError("hook summary must be a non-empty single line")
    if max_units <= 0:
        return None
    clauses = [part.strip() for part in re.split(r"[。！？!?；;，,：:]", text) if part.strip()]
    candidates = list(clauses)
    for clause in clauses:
        candidates.extend(part.strip() for part in re.split(r"并且|并|且|和|及", clause) if part.strip())
    candidates = _stable_unique(candidate for candidate in candidates if display_units(candidate) <= max_units)
    protected = tuple(marker for marker in _HOOK_BOUNDARY_MARKERS if marker in text)
    if protected:
        candidates = [candidate for candidate in candidates if any(marker in candidate for marker in protected)]
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: (
        -sum(marker in candidate for marker in _HOOK_QUALIFIERS),
        -sum(marker in candidate for marker in _HOOK_BOUNDARY_MARKERS),
        -display_units(candidate),
        text.index(candidate),
        candidate,
    ))


def _stop_message(steps, primary_stop_condition=None):
    """Copy the semantic primary boundary, or preserve every boundary when it is absent."""
    for step in steps:
        if not isinstance(step, dict):
            continue
        for condition in step.get("stop_conditions", []):
            if isinstance(condition, str) and condition != condition.strip():
                raise ValueError("stop_conditions must not have leading or trailing whitespace")
    raw_conditions = _stable_unique(
        condition
        for step in steps if isinstance(step, dict)
        for condition in step.get("stop_conditions", [])
        if isinstance(condition, str) and condition.strip()
    )
    if primary_stop_condition is not None:
        if not isinstance(primary_stop_condition, str) or primary_stop_condition != primary_stop_condition.strip():
            raise ValueError("primary_stop_condition must not have leading or trailing whitespace")
        if primary_stop_condition not in raw_conditions:
            raise ValueError("primary_stop_condition must exactly match one steps.stop_conditions item")
        condition = primary_stop_condition.strip("。；， ")
        prefix = "" if condition.startswith(("若", "如果", "当")) else "若"
        message = f"{prefix}{condition}，请停止自行处理并寻求合适的专业帮助。"
        if display_units(message) > CTA_STOP_MESSAGE_MAX_UNITS:
            raise ValueError(f"CTA_PRIMARY_STOP_TOO_LONG: stop message exceeds {CTA_STOP_MESSAGE_MAX_UNITS} display units")
        return message
    if not raw_conditions:
        return "出现无法安全判断或情况恶化时，请停止自行处理并寻求合适的专业帮助。"
    conditions = [condition.strip("。；， ") for condition in raw_conditions]
    message = f"停止条件：{'；'.join(conditions)}。任一出现时，请停止自行处理并寻求合适的专业帮助。"
    if display_units(message) > CTA_STOP_MESSAGE_MAX_UNITS:
        raise ValueError(f"CTA_STOP_CONDITIONS_TOO_LONG: full stop-condition list exceeds {CTA_STOP_MESSAGE_MAX_UNITS} display units")
    return message


def _cta(unknowns):
    if unknowns:
        text = str(unknowns[0]).replace("未提供", "").replace("未知", "").strip("。；， ")
        subject = text[:36].strip("。；， ") or "关键现场条件"
    else: subject = "关键现场条件"
    return f"{subject}，你目前能确认哪一项？"


def _reader_time(value):
    if not value: return "时间未知"
    try: return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M")
    except ValueError: return str(value)[:24]


def _video_for_post(post, note, deck, candidate_count, question_count, excluded_count):
    solution, steps = post["solution"], post["solution"]["steps"]
    if not 1 <= len(steps) <= 5:
        raise ValueError(f"{post['note_id']}: xhs-video/v1 requires 1-5 steps; refusing to cram or truncate")
    capture, unsafe = note.get("capture", {}), _unsafe_ids(post)
    action_total = TARGET_MS[len(steps)] - sum(FIXED_DURATIONS.values())
    base, remainder = divmod(action_total, len(steps))
    action_durations = [base + (1 if index < remainder else 0) for index in range(len(steps))]
    scenes, cursor = [], 0

    def add(role, duration, content, narration, evidence_ids=(), notices=()):
        nonlocal cursor
        index, ids, local_notices = len(scenes) + 1, _stable_unique(evidence_ids), list(notices)
        if unsafe.intersection(ids):
            if UNSAFE_NOTICE_CODE not in local_notices: local_notices.append(UNSAFE_NOTICE_CODE)
            narration = f"{UNSAFE_WARNING}。{narration}"
        scenes.append(_scene(f"{post['note_id']}:{index:02d}", index, role, cursor, duration, content, narration, ids, local_notices))
        cursor += duration

    hook_notices = []
    if capture.get("source") == "synthetic_fixture": hook_notices.append("synthetic_demo")
    if solution["risk_level"] == "high" and solution["publish_status"] == "needs_review": hook_notices.append("high_risk_needs_review")
    social_title = post.get("social_title") or post["question"]
    title_voice, hook_path = _sentence(social_title), f"，继续看{len(steps)}步。"
    excerpt_budget = min(12, 28 - display_units(title_voice) - display_units(hook_path))
    hook_excerpt = _hook_summary_excerpt(solution["summary"], excerpt_budget)
    hook_voice = f"{title_voice}{hook_excerpt}{hook_path}" if hook_excerpt else f"问题、证据、行动，继续看{len(steps)}步。"
    add("hook", FIXED_DURATIONS["hook"], {"social_title": social_title, "question": post["question"], "summary": solution["summary"]}, hook_voice, notices=hook_notices)

    coverage = _coverage(capture)
    scope_voice = f"{coverage}，{'评论未完整采集；' if capture.get('is_truncated') else ''}热度不等于事实。"
    add("scope", FIXED_DURATIONS["scope"],
        {"candidate_count": candidate_count, "question_count": question_count, "excluded_count": excluded_count,
         "source_label": SOURCE_LABELS.get(capture.get("source"), "其他已授权来源"), "captured_at_label": _reader_time(capture.get("captured_at")),
         "coverage": coverage, "is_truncated": bool(capture.get("is_truncated")), "failure_reason_label": FAILURE_LABELS.get(capture.get("failure_reason"), "")},
        scope_voice, notices=["truncated_sample"] if capture.get("is_truncated") else [])

    for number, (step, duration) in enumerate(zip(steps, action_durations), 1):
        evidence_ids = step["evidence_comment_ids"]
        if unsafe.intersection(evidence_ids):
            voice = f"第{number}步先确认安全边界，完整适用、验证与停止条件见画面。"
        else:
            voice = _sentence(f"第{number}步：{step['text']}")
        add("action", duration,
            {"step_number": number, "text": step["text"], "applies_when": step["applies_when"], "verification": step["verification"], "stop_conditions": step["stop_conditions"]},
            voice, evidence_ids)

    experience, counterexample = _selected(post["comments"], "firsthand_experience"), _selected(post["comments"], "counterexample")
    evidence_ids = [item["comment_id"] for item in (experience, counterexample) if item]
    if experience and counterexample:
        evidence_voice = "左边是一条亲历个案，右边是一条失败反例。它们只提示方向，不证明普遍结果。"
    elif experience: evidence_voice = "当前只有一条亲历个案，不能外推为普遍结果。"
    elif counterexample: evidence_voice = "当前只有一条失败反例，它只提示方案边界。"
    else: evidence_voice = "当前样本没有可用的亲历与失败反例，不能据此判断普遍效果。"
    add("evidence", FIXED_DURATIONS["evidence"], {"experience": experience, "counterexample": counterexample, "boundary": "评论个案只提示方向，不证明普遍结果"}, evidence_voice, evidence_ids, ["experience_not_fact"])

    conflict_ids = _stable_unique(cid for conflict in solution.get("conflicts", []) for position in conflict.get("positions", []) for cid in position.get("evidence_comment_ids", []))
    conflict_voice = "评论中存在相互冲突的处理观点，不能按点赞裁决。"
    if solution["risk_level"] == "high": conflict_voice += "高风险内容仍需权威复核。"
    high_notice = ["high_risk_needs_review"] if solution["risk_level"] == "high" and solution["publish_status"] == "needs_review" else []
    add("conflict_risk", FIXED_DURATIONS["conflict_risk"], {"conflicts": solution.get("conflicts", []), "risk_level": solution["risk_level"], "publish_status": solution["publish_status"]}, conflict_voice, conflict_ids, high_notice)

    stops = _stable_unique(stop for step in steps for stop in step["stop_conditions"])
    risk_voice = f"风险等级是{RISK_LABELS[solution['risk_level']]}，发布状态为{PUBLISH_LABELS[solution['publish_status']]}。还需确认现场条件、停止边界以及遗漏的反例。"
    add("risk_unknowns", FIXED_DURATIONS["risk_unknowns"], {"risk_level": solution["risk_level"], "publish_status": solution["publish_status"], "unknowns": solution.get("unknowns", []), "stop_conditions": stops}, risk_voice, notices=high_notice)
    source_label = SOURCE_LABELS.get(capture.get("source"), "其他已授权来源")
    sample_label = "评论未完整采集" if capture.get("is_truncated") else "按采集范围整理"
    publish_voice = "发布前人工复核" if solution["publish_status"] == "needs_review" else "当前可发布"
    add("disclosure", FIXED_DURATIONS["disclosure"],
        {"coverage": coverage, "source_label": source_label, "is_truncated": bool(capture.get("is_truncated")),
         "failure_reason_label": FAILURE_LABELS.get(capture.get("failure_reason"), ""), "ai_assisted": True,
         "experience_is_not_fact": True, "interest_disclosure": "unknown_requires_manual_confirmation",
         "publish_status": solution["publish_status"], "evidence_index": "appendix.evidence"},
        f"AI辅助整理、{source_label}，{sample_label}且经验不等于事实，利益关系未知，{publish_voice}。",
        notices=["ai_assisted"] + (["truncated_sample"] if capture.get("is_truncated") else []))
    cta = _cta(solution.get("unknowns", []))
    add("cta", FIXED_DURATIONS["cta"], {"question": cta, "stop_message": _stop_message(steps, solution.get("primary_stop_condition"))}, cta)

    meta = {"candidate_count": candidate_count, "question_count": question_count, "excluded_count": excluded_count,
            "source": capture.get("source", "unknown"), "captured_at": capture.get("captured_at", ""), "comments_total": capture.get("comments_total", 0),
            "comments_collected": capture.get("comments_collected", 0), "is_truncated": bool(capture.get("is_truncated")), "failure_reason": capture.get("failure_reason", ""),
            "risk_level": solution["risk_level"], "publish_status": solution["publish_status"], "ai_assisted": True,
            "interest_disclosure": "unknown_requires_manual_confirmation", "audio": {"kind": "none"}}
    unsafe_evidence_comment_ids = _stable_unique(item["comment_id"] for item in post["comments"] if "unsafe_advice" in item.get("risk_flags", []))
    return {"video_id": f"note:{post['note_id']}", "note_id": post["note_id"], "profile": PROFILE, "width": WIDTH, "height": HEIGHT, "fps": FPS,
            "duration_ms": cursor, "duration_in_frames": cursor * FPS // 1000, "meta": meta, "scenes": scenes, "appendix": deck["appendix"],
            "unsafe_evidence_comment_ids": unsafe_evidence_comment_ids}


def build_video_ir(canonical, analysis):
    errors = validate(canonical, analysis)
    if errors: raise ValueError("analysis validation failed:\n" + "\n".join(errors))
    from render_result import build_card_decks
    notes = {row["note_id"]: row for row in canonical if row.get("kind") == "note"}
    question_posts = [post for post in analysis["posts"] if post["is_question"]]
    decks = {deck["note_id"]: deck for deck in build_card_decks(canonical, analysis)["decks"]}
    excluded = sum(not post["is_question"] for post in analysis["posts"])
    ir = {"schema": SCHEMA, "videos": [_video_for_post(post, notes[post["note_id"]], decks[post["note_id"]], len(analysis["posts"]), len(question_posts), excluded) for post in question_posts]}
    ir_errors = validate_video_ir(ir, canonical, analysis)
    if ir_errors: raise ValueError("video IR validation failed:\n" + "\n".join(ir_errors))
    return ir


def _unknown_fields(value, allowed, path, errors):
    if isinstance(value, dict):
        missing = sorted(allowed - set(value))
        if missing: errors.append(f"MISSING_FIELD {path}: {', '.join(missing)}")
        unknown = sorted(set(value) - allowed)
        if unknown: errors.append(f"UNKNOWN_FIELD {path}: {', '.join(unknown)}")


def _strict_int(value):
    return type(value) is int


def _nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def validate_video_ir(ir, canonical=None, analysis=None):
    errors = []
    if not isinstance(ir, dict): return ["SHAPE $ must be an object"]
    _unknown_fields(ir, {"schema", "videos"}, "$", errors)
    if ir.get("schema") != SCHEMA: errors.append(f"SCHEMA expected {SCHEMA}")
    videos = ir.get("videos")
    if not isinstance(videos, list) or not videos: return errors + ["SHAPE $.videos must be a non-empty list"]
    canonical_comments = {(row["note_id"], row["comment_id"]): row for row in (canonical or []) if row.get("kind") == "comment"}
    posts = {post["note_id"]: post for post in analysis.get("posts", []) if post.get("is_question")} if isinstance(analysis, dict) else {}
    expected_videos = {}
    if canonical is not None and isinstance(analysis, dict):
        try:
            from render_result import build_card_decks
            notes = {row["note_id"]: row for row in canonical if row.get("kind") == "note"}
            question_posts = [post for post in analysis.get("posts", []) if post.get("is_question")]
            decks = {deck["note_id"]: deck for deck in build_card_decks(canonical, analysis)["decks"]}
            excluded = sum(not post.get("is_question") for post in analysis.get("posts", []))
            expected_videos = {
                post["note_id"]: _video_for_post(post, notes[post["note_id"]], decks[post["note_id"]],
                                                  len(analysis.get("posts", [])), len(question_posts), excluded)
                for post in question_posts
            }
        except (KeyError, TypeError, ValueError):
            expected_videos = {}
    seen_videos = set()
    for vi, video in enumerate(videos):
        path = f"$.videos[{vi}]"
        if not isinstance(video, dict): errors.append(f"SHAPE {path} must be an object"); continue
        _unknown_fields(video, VIDEO_FIELDS, path, errors)
        note_id, video_id = video.get("note_id"), video.get("video_id")
        if not _nonempty_string(video_id): errors.append(f"TYPE {path}.video_id must be a non-empty string")
        elif video_id in seen_videos: errors.append(f"DUPLICATE_ID {path}.video_id")
        else: seen_videos.add(video_id)
        if not _nonempty_string(note_id): errors.append(f"TYPE {path}.note_id must be a non-empty string")
        for field in ("width", "height", "fps", "duration_ms", "duration_in_frames"):
            if not _strict_int(video.get(field)): errors.append(f"TYPE {path}.{field} must be an integer")
        if video.get("profile") != PROFILE or (video.get("width"), video.get("height"), video.get("fps")) != (WIDTH, HEIGHT, FPS): errors.append(f"PROFILE {path} must use {PROFILE}")
        unsafe_manifest = video.get("unsafe_evidence_comment_ids")
        valid_unsafe_ids = isinstance(unsafe_manifest, list) and all(_nonempty_string(cid) for cid in unsafe_manifest)
        if not valid_unsafe_ids:
            errors.append(f"TYPE {path}.unsafe_evidence_comment_ids must contain non-empty string IDs")
        if not valid_unsafe_ids or len(unsafe_manifest) != len(set(unsafe_manifest)):
            errors.append(f"UNSAFE_MANIFEST {path}.unsafe_evidence_comment_ids must be a unique string list"); unsafe_manifest = []
        meta = video.get("meta")
        if not isinstance(meta, dict): errors.append(f"SHAPE {path}.meta must be an object")
        else:
            _unknown_fields(meta, META_FIELDS, f"{path}.meta", errors)
            if meta.get("audio") != {"kind": "none"}: errors.append(f"AUDIO {path}.meta.audio only supports silent v1")
        appendix = video.get("appendix")
        appendix_ids = set()
        if not isinstance(appendix, dict): errors.append(f"SHAPE {path}.appendix must be an object")
        else:
            _unknown_fields(appendix, {"evidence"}, f"{path}.appendix", errors)
            evidence = appendix.get("evidence")
            if not isinstance(evidence, list): errors.append(f"SHAPE {path}.appendix.evidence must be a list")
            else:
                required = {"comment_id", "category", "category_label", "author", "likes", "likes_label", "thread_id", "excerpt"}
                allowed = required | {"safety_warning"}
                for ei, item in enumerate(evidence):
                    item_path = f"{path}.appendix.evidence[{ei}]"
                    if not isinstance(item, dict): errors.append(f"SHAPE {item_path} must be an object"); continue
                    missing = sorted(required - set(item)); unknown = sorted(set(item) - allowed)
                    if missing: errors.append(f"MISSING_FIELD {item_path}: {', '.join(missing)}")
                    if unknown: errors.append(f"UNKNOWN_FIELD {item_path}: {', '.join(unknown)}")
                    if _nonempty_string(item.get("comment_id")): appendix_ids.add(item["comment_id"])
                    else: errors.append(f"TYPE {item_path}.comment_id must be a non-empty string")
                    if item.get("comment_id") in unsafe_manifest:
                        if item.get("safety_warning") != UNSAFE_WARNING: errors.append(f"UNSAFE_MANIFEST {item_path} must carry the fixed safety warning")
                    elif item.get("safety_warning") is not None:
                        errors.append(f"UNSAFE_MANIFEST {item_path} has a warning but is not in unsafe_evidence_comment_ids")
        if any(cid not in appendix_ids for cid in unsafe_manifest): errors.append(f"UNSAFE_MANIFEST {path} contains an unknown appendix comment")
        scenes = video.get("scenes")
        if not isinstance(scenes, list) or not scenes: errors.append(f"SHAPE {path}.scenes must be non-empty"); continue
        post, cursor, scene_ids = posts.get(note_id), 0, set()
        for si, scene in enumerate(scenes):
            scene_path = f"{path}.scenes[{si}]"
            if not isinstance(scene, dict): errors.append(f"SHAPE {scene_path} must be an object"); continue
            _unknown_fields(scene, SCENE_FIELDS, scene_path, errors)
            role = scene.get("role")
            if role not in ROLES: errors.append(f"ROLE {scene_path}.role")
            if not _strict_int(scene.get("index")): errors.append(f"TYPE {scene_path}.index must be an integer")
            if scene.get("index") != si + 1: errors.append(f"SCENE_INDEX {scene_path}")
            scene_id = scene.get("scene_id")
            if not _nonempty_string(scene_id): errors.append(f"TYPE {scene_path}.scene_id must be a non-empty string")
            elif scene_id in scene_ids: errors.append(f"DUPLICATE_ID {scene_path}.scene_id")
            else: scene_ids.add(scene_id)
            start_ms, end_ms = scene.get("start_ms"), scene.get("end_ms")
            if not _strict_int(start_ms): errors.append(f"TYPE {scene_path}.start_ms must be an integer")
            if not _strict_int(end_ms): errors.append(f"TYPE {scene_path}.end_ms must be an integer")
            if not _strict_int(start_ms) or not _strict_int(end_ms) or start_ms != cursor or end_ms <= cursor: errors.append(f"SCENE_TIMING {scene_path} expected start {cursor}")
            if _strict_int(end_ms): cursor = end_ms
            content = scene.get("content")
            if not isinstance(content, dict): errors.append(f"SHAPE {scene_path}.content must be an object")
            elif role in CONTENT_FIELDS: _unknown_fields(content, CONTENT_FIELDS[role], f"{scene_path}.content", errors)
            evidence_ids = scene.get("evidence_comment_ids")
            if not isinstance(evidence_ids, list) or any(not _nonempty_string(cid) for cid in evidence_ids):
                errors.append(f"TYPE {scene_path}.evidence_comment_ids must contain non-empty string IDs")
                errors.append(f"EVIDENCE {scene_path}.evidence_comment_ids must be a string list"); evidence_ids = []
            elif len(evidence_ids) != len(set(evidence_ids)): errors.append(f"EVIDENCE {scene_path} contains duplicate IDs")
            for cid in evidence_ids:
                if canonical is not None and (note_id, cid) not in canonical_comments: errors.append(f"EVIDENCE {scene_path} invalid comment {cid}")
            notices = scene.get("persistent_notices")
            if not isinstance(notices, list) or any(item not in ALLOWED_NOTICES for item in notices): errors.append(f"NOTICE {scene_path}.persistent_notices"); notices = []
            unsafe = _unsafe_ids(post).intersection(evidence_ids) if post else set()
            narration, captions = scene.get("narration"), scene.get("captions")
            if not isinstance(narration, str) or not narration: errors.append(f"NARRATION {scene_path}")
            if not isinstance(captions, list) or not captions: errors.append(f"CAPTION {scene_path}.captions must be non-empty"); captions = []
            combined, previous = "", scene.get("start_ms", 0)
            for ci, caption in enumerate(captions):
                caption_path = f"{scene_path}.captions[{ci}]"
                if not isinstance(caption, dict): errors.append(f"CAPTION {caption_path} must be an object"); continue
                _unknown_fields(caption, CAPTION_FIELDS, caption_path, errors)
                if set(caption) != CAPTION_FIELDS: errors.append(f"CAPTION_SHAPE {caption_path}")
                text, start, end = caption.get("text"), caption.get("startMs"), caption.get("endMs")
                if not isinstance(text, str) or not text or any(char in text for char in "\r\n\t"): errors.append(f"CAPTION_TEXT {caption_path}"); text = ""
                if not _strict_int(start): errors.append(f"TYPE {caption_path}.startMs must be an integer")
                if not _strict_int(end): errors.append(f"TYPE {caption_path}.endMs must be an integer")
                if not _strict_int(start) or not _strict_int(end) or start < previous or end <= start or not _strict_int(scene.get("end_ms")) or end > scene["end_ms"]: errors.append(f"CAPTION_TIMING {caption_path}")
                elif end - start < 1_200 or display_units(text) / ((end - start) / 1000) > 10.0001: errors.append(f"CAPTION_DENSITY {caption_path}")
                if display_units(text) > 20.0001: errors.append(f"CAPTION_WIDTH {caption_path}")
                if caption.get("timestampMs") is not None or caption.get("confidence") is not None: errors.append(f"CAPTION_METADATA {caption_path}")
                if _strict_int(end): previous = end
                combined += text
            if isinstance(narration, str) and combined != narration: errors.append(f"CAPTION_NARRATION_MISMATCH {scene_path}")
            if unsafe:
                if UNSAFE_NOTICE_CODE not in notices: errors.append(f"UNSAFE_NOTICE {scene_path}")
                if not narration.startswith(UNSAFE_WARNING): errors.append(f"UNSAFE_NARRATION {scene_path}")
                if not captions or not captions[0].get("text", "").startswith(UNSAFE_WARNING): errors.append(f"UNSAFE_CAPTION {scene_path}")
            elif UNSAFE_NOTICE_CODE in notices: errors.append(f"UNSAFE_NOTICE {scene_path} has warning without unsafe evidence")
        if post:
            expected_roles = ["hook", "scope"] + ["action"] * len(post["solution"]["steps"]) + ["evidence", "conflict_risk", "risk_unknowns", "disclosure", "cta"]
            if [scene.get("role") for scene in scenes if isinstance(scene, dict)] != expected_roles: errors.append(f"SCENE_ORDER {path}")
        duration, duration_frames = video.get("duration_ms"), video.get("duration_in_frames")
        if not _strict_int(duration) or duration != cursor or not 60_000 <= duration <= 90_000: errors.append(f"VIDEO_DURATION {path}")
        if not _strict_int(duration_frames) or (_strict_int(duration) and duration_frames != duration * FPS // 1000): errors.append(f"VIDEO_FRAMES {path}")
        if post:
            actions = [scene for scene in scenes if isinstance(scene, dict) and scene.get("role") == "action"]
            for step_number, (scene, step) in enumerate(zip(actions, post["solution"]["steps"]), 1):
                expected = {"step_number": step_number, "text": step["text"], "applies_when": step["applies_when"], "verification": step["verification"], "stop_conditions": step["stop_conditions"]}
                if scene.get("content") != expected: errors.append(f"ACTION_CONTENT {path}.scenes[{step_number + 1}]")
                if scene.get("evidence_comment_ids") != step["evidence_comment_ids"]: errors.append(f"ACTION_EVIDENCE {path}")
            conflict = next((scene for scene in scenes if scene.get("role") == "conflict_risk"), None)
            if conflict and conflict.get("content", {}).get("conflicts") != post["solution"].get("conflicts", []): errors.append(f"CONFLICT_CONTENT {path}")
            risk = next((scene for scene in scenes if scene.get("role") == "risk_unknowns"), None)
            if risk and risk.get("content", {}).get("unknowns") != post["solution"].get("unknowns", []): errors.append(f"UNKNOWN_CONTENT {path}")
        if note_id in expected_videos and video != expected_videos[note_id]:
            errors.append(f"VIDEO_CONTENT_MISMATCH {path} differs from deterministic canonical builder")
    if analysis is not None:
        expected = {post["note_id"] for post in analysis.get("posts", []) if post.get("is_question")}
        actual = {video.get("note_id") for video in videos if isinstance(video, dict)}
        if actual != expected: errors.append("VIDEO_COVERAGE question posts and videos differ")
    return errors


def serialize_video_ir(ir):
    if not isinstance(ir, dict) or ir.get("schema") != SCHEMA: raise ValueError(f"unsupported video schema; expected {SCHEMA}")
    return json.dumps(ir, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _cell(value): return " ".join(str(value or "").split()).replace("|", "｜").replace("。；", "；").replace("。。", "。")


def _time_label(start_ms, end_ms):
    def value(ms):
        seconds = ms / 1000
        return str(int(seconds)) if seconds.is_integer() else f"{seconds:.1f}".rstrip("0").rstrip(".")
    return f"{value(start_ms)}–{value(end_ms)} 秒"


def _visual(scene):
    labels = {"hook": "问题—误区—替代方向", "scope": "覆盖率、截断与热度边界", "evidence": "亲历个案与失败反例并列", "conflict_risk": "冲突双方 + 常驻风险警示", "risk_unknowns": "风险、未知项与停止边界", "disclosure": "AI、样本、利益与证据披露", "cta": "安全互动问题"}
    if scene["role"] != "action": return labels[scene["role"]]
    content = scene["content"]
    return (f"第 {content['step_number']} 步：{content['text']}｜适用：{'；'.join(content['applies_when'])}｜"
            f"验证：{content['verification']}｜停止：{'；'.join(content['stop_conditions'])}")


def render_video_markdown(ir):
    if not isinstance(ir, dict) or ir.get("schema") != SCHEMA: raise ValueError(f"unsupported video schema; expected {SCHEMA}")
    lines = ["# 短视频口播脚本"]
    for video in ir["videos"]:
        hook = video["scenes"][0]["content"]
        lines += [f"\n## 选题：{hook['question']}", "\n| 时段 | 画面 | 口播 | 字幕 | 证据 |", "|---|---|---|---|---|"]
        for scene in video["scenes"]:
            row = (_time_label(scene["start_ms"], scene["end_ms"]), _visual(scene), scene["narration"], scene["narration"], ", ".join(scene["evidence_comment_ids"]) or "—")
            lines.append("| " + " | ".join(_cell(cell) for cell in row) + " |")
        meta, coverage = video["meta"], _coverage(video["meta"])
        disclosure = f"{coverage}。{'评论未完整采集。' if meta['is_truncated'] else ''}高风险内容须经权威来源复核；内容由 AI 辅助整理；利益关系未知；发布状态：{PUBLISH_LABELS[meta['publish_status']]}。"
        lines += [f"\n### 描述区披露\n{disclosure}", "\n### 证据索引"]
        for item in video["appendix"]["evidence"]:
            warning = f"｜警示：{item['safety_warning']}" if item.get("safety_warning") else ""
            lines.append(f"- `{item['comment_id']}`｜{item['category_label']}｜{item['author']}｜{item.get('likes_label', '赞数未知')}｜thread `{item['thread_id']}`{warning}｜{item['excerpt']}")
    return "\n".join(lines).replace("。；", "；").replace("。。", "。").rstrip() + "\n"


def _safe_name(value):
    raw = str(value or "video")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")[:32] or "video"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def write_video_projects(canonical, analysis, output_dir):
    ir = build_video_ir(canonical, analysis)
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "video-projects.json").write_text(serialize_video_ir(ir), encoding="utf-8")
    (output_dir / "short-video.md").write_text(render_video_markdown(ir), encoding="utf-8")
    written = []
    for video in ir["videos"]:
        name = _safe_name(video["note_id"])
        props = output_dir / f"{name}.props.json"
        props.write_text(json.dumps({"schema": SCHEMA, "video": video}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        written.append((video, props, output_dir / f"{name}.mp4"))
    return ir, written


def _replace_output_files(entries):
    states = []
    try:
        for staging, target in entries:
            backup = target.with_name(f".{target.stem}.backup-{uuid.uuid4().hex}{target.suffix}")
            state = {"staging": staging, "target": target, "backup": backup, "backed_up": False, "installed": False}
            states.append(state)
            if target.exists(): target.rename(backup); state["backed_up"] = True
        for state in states:
            state["staging"].rename(state["target"]); state["installed"] = True
    except Exception as error:
        restore_errors = []
        for state in reversed(states):
            try:
                if state["installed"] and state["target"].exists(): state["target"].unlink()
                if state["backed_up"] and state["backup"].exists(): state["backup"].rename(state["target"])
            except Exception as restore_error: restore_errors.append(str(restore_error))
        if restore_errors: raise RuntimeError(f"MP4 batch replacement failed and rollback failed: {'; '.join(restore_errors)}") from error
        raise RuntimeError("MP4 batch replacement failed; previous MP4 set was restored") from error
    for state in states:
        if state["backed_up"]: state["backup"].unlink()


def _has_ftyp(path):
    with Path(path).open("rb") as handle:
        return b"ftyp" in handle.read(64)


def render_mp4s(written, node=None, browser=None, frame_range=None, runner=subprocess.run):
    node_path = str(node) if node else shutil.which("node")
    if not node_path:
        raise RuntimeError("MP4 rendering needs Node.js; xhs-video/v1 and Markdown were generated successfully")
    renderer = Path(__file__).with_name("render_video.mjs")
    if not renderer.is_file(): raise RuntimeError(f"MP4 renderer is missing: {renderer}")
    targets = [Path(item[2]).resolve() for item in written]
    if len(targets) != len(set(targets)): raise RuntimeError("MP4 batch contains duplicate output targets")
    prepared, staging_paths = [], []
    try:
        for video, props_path, target in written:
            target = Path(target); target.parent.mkdir(parents=True, exist_ok=True)
            staging = target.with_name(f".{target.stem}.rendering-{uuid.uuid4().hex}{target.suffix}")
            staging_paths.append(staging)
            command = [node_path, str(renderer), "--props", str(props_path), "--output", str(staging)]
            if browser: command += ["--browser", str(browser)]
            if frame_range is not None: command += ["--frame-range", f"{frame_range[0]}:{frame_range[1]}" ]
            result = runner(command, check=False, text=True, encoding="utf-8", capture_output=True)
            if result.returncode:
                detail = (result.stderr or result.stdout).strip()
                raise RuntimeError(f"MP4 rendering failed for {video['video_id']}: {detail}; previous MP4 is unchanged")
            if not staging.is_file() or staging.stat().st_size < 16 or not _has_ftyp(staging):
                raise RuntimeError(f"MP4 rendering failed for {video['video_id']}: output is not a valid MP4; previous MP4 is unchanged")
            try: summary = json.loads(result.stdout.strip().splitlines()[-1])
            except (IndexError, json.JSONDecodeError) as error:
                raise RuntimeError(f"MP4 rendering failed for {video['video_id']}: renderer returned invalid JSON; previous MP4 is unchanged") from error
            expected = {
                "codec": "h264", "width": video["width"], "height": video["height"], "fps": video["fps"],
                "duration_in_frames": video["duration_in_frames"],
                "rendered_frame_range": list(frame_range) if frame_range is not None else None,
                "audio": "none", "file_size": staging.stat().st_size,
            }
            probe = summary.get("probe", {})
            rendered_frames = frame_range[1] - frame_range[0] + 1 if frame_range is not None else video["duration_in_frames"]
            probe_ok = ((probe.get("codec"), probe.get("width"), probe.get("height"), probe.get("audio_streams")) ==
                        ("h264", video["width"], video["height"], 0) and
                        isinstance(probe.get("duration_seconds"), (int, float)) and
                        abs(probe["duration_seconds"] - rendered_frames / video["fps"]) <= 0.2)
            if any(summary.get(key) != value for key, value in expected.items()) or not probe_ok:
                raise RuntimeError(f"MP4 rendering failed for {video['video_id']}: renderer metadata mismatch; previous MP4 is unchanged")
            prepared.append((staging, target, summary))
        _replace_output_files([(staging, target) for staging, target, _summary in prepared])
        summaries = []
        for _staging, target, summary in prepared:
            summary["output"] = target.name
            summaries.append(summary)
        return summaries
    finally:
        for staging in staging_paths:
            if staging.exists(): staging.unlink()


def main():
    parser = argparse.ArgumentParser(description="Build xhs-video/v1, Markdown, Remotion props, and optional MP4")
    parser.add_argument("canonical", type=Path); parser.add_argument("analysis", type=Path); parser.add_argument("output_dir", type=Path)
    parser.add_argument("--mp4", action="store_true", help="also render silent H.264 MP4 using the pinned optional Remotion runtime")
    parser.add_argument("--node", type=Path); parser.add_argument("--browser", type=Path)
    args = parser.parse_args(); canonical = load_jsonl(args.canonical); analysis = json.loads(args.analysis.read_text(encoding="utf-8-sig"))
    ir, written = write_video_projects(canonical, analysis, args.output_dir)
    print(f"rendered {len(ir['videos'])} video project(s): {args.output_dir}")
    if args.mp4:
        summaries = render_mp4s(written, args.node, args.browser)
        (args.output_dir / "mp4-render-summary.json").write_text(json.dumps({"backend": "remotion", "videos": summaries}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(f"rendered {len(summaries)} silent 1080x1920 H.264 MP4 video(s)")


if __name__ == "__main__": main()
