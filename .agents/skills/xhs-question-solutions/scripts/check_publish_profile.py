#!/usr/bin/env python3
"""Check xhs-video/v1 against dated, source-traceable platform profiles."""
import argparse
import json
import math
import re
import sys
import unicodedata
from datetime import date
from fractions import Fraction
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from render_video import UNSAFE_NOTICE_CODE, UNSAFE_WARNING, validate_video_ir


REPORT_SCHEMA = "xhs-publish-check/v1"
CATALOG_SCHEMA = "xhs-platform-profiles/v1"
AI_KINDS = ("none", "assistive_text_only", "synthetic_visual", "synthetic_audio", "realistic_altered")
SYNTHETIC_AI_KINDS = {"synthetic_visual", "synthetic_audio", "realistic_altered"}
CHECK_IDS = ("resolution", "aspect_ratio", "fps", "duration", "audio", "ai_disclosure", "platform_preview")
REQUIRED_PROFILES = {
    "xhs_cn", "douyin_cn", "tiktok_organic", "tiktok_ads",
    "youtube_shorts", "instagram_reels", "instagram_boost", "cross_platform_master_60",
}
PROFILE_FIELDS = {"profile_id", "label", "applicability", "publication_mode", "sources", "rules"}
SOURCE_FIELDS = {"source_id", "url", "checked_at", "authority", "evidence_status", "applies_to"}
RULE_FIELDS = {
    "resolution": {"knowledge", "enforcement", "exact_width", "exact_height", "min_width", "min_height", "min_short_edge", "source_ids", "manual_check"},
    "aspect_ratio": {"knowledge", "enforcement", "allowed_exact", "allowed_orientations", "min_ratio", "max_ratio", "source_ids", "manual_check"},
    "fps": {"knowledge", "enforcement", "exact", "min", "max", "source_ids", "manual_check"},
    "duration": {"knowledge", "enforcement", "min_ms", "max_ms", "min_inclusive", "max_inclusive", "source_ids", "manual_check"},
    "audio": {"knowledge", "enforcement", "allowed_kinds", "required", "source_ids", "manual_check"},
    "ai_disclosure": {"knowledge", "source_ids", "manual_check", "kinds"},
    "platform_preview": {"required", "items", "source_ids", "manual_check"},
}
AI_RULE_FIELDS = {"required", "verification", "action", "basis"}
KNOWLEDGE = {"known", "mixed", "unknown"}
ENFORCEMENT = {"hard", "advisory", "project_gate", "manual"}
AUTHORITIES = {"official", "official_recommendation", "regulator", "project_policy"}
EVIDENCE_STATUSES = {"supports", "no_public_value", "conflicting", "project_policy"}
OFFICIAL_DOMAIN_SUFFIXES = (
    "xiaohongshu.com", "douyin.com", "tiktok.com", "google.com",
    "youtube.com", "facebook.com", "fb.com",
)
CANONICAL_FIRST_FRAME_LABELS = {
    "synthetic_visual": "画面由AI生成",
    "synthetic_audio": "旁白由AI合成",
    "realistic_altered": "画面经AI修改",
}
FIRST_FRAME_LABEL_VARIANTS = {
    frozenset({"synthetic_visual"}): ("画面由AI生成", "非真人实拍，画面由AI生成"),
    frozenset({"synthetic_audio"}): ("旁白由AI合成",),
    frozenset({"realistic_altered"}): ("画面经AI修改",),
    frozenset({"synthetic_visual", "synthetic_audio"}): ("画面由AI生成，旁白由AI合成",),
    frozenset({"synthetic_visual", "realistic_altered"}): ("画面由AI生成并经AI修改",),
    frozenset({"synthetic_audio", "realistic_altered"}): ("画面经AI修改，旁白由AI合成",),
    frozenset({"synthetic_visual", "synthetic_audio", "realistic_altered"}): ("画面由AI生成并经AI修改，旁白由AI合成",),
}
SOURCE_URL_POLICIES = {
    ("xhs_cn", "xhs_creator_portal"): ("creator.xiaohongshu.com", "/"),
    ("xhs_cn", "xhs_share_docs"): ("agora.xiaohongshu.com", "/doc"),
    ("xhs_cn", "cac_ai_labels"): ("www.cac.gov.cn", "/2025-03/14/c_1743654684782215.htm"),
    ("douyin_cn", "douyin_publish_solution"): ("open.douyin.com", "/platform/resource/docs/ability/content-management/douyin-publish-solution"),
    ("douyin_cn", "douyin_publish_duration"): ("open.douyin.com", "/platform/resource/docs/ability/content-management/douyin-publish-solution"),
    ("douyin_cn", "douyin_user_agreement"): ("www.douyin.com", "/agreements/"),
    ("douyin_cn", "cac_ai_labels"): ("www.cac.gov.cn", "/2025-03/14/c_1743654684782215.htm"),
    ("tiktok_organic", "tiktok_studio_upload"): ("support.tiktok.com", "/en/using-tiktok/creating-videos/creator-tools-on-tiktok"),
    ("tiktok_organic", "tiktok_studio_no_public_values"): ("support.tiktok.com", "/en/using-tiktok/creating-videos/creator-tools-on-tiktok"),
    ("tiktok_organic", "tiktok_aigc"): ("support.tiktok.com", "/en/using-tiktok/creating-videos/ai-generated-content"),
    ("tiktok_ads", "tiktok_reservation_infeed"): ("ads.tiktok.com", "/help/article/tiktok-reservation-in-feed-ads-reach-frequency"),
    ("tiktok_ads", "tiktok_reservation_no_public_fps"): ("ads.tiktok.com", "/help/article/tiktok-reservation-in-feed-ads-reach-frequency"),
    ("tiktok_ads", "tiktok_aigc"): ("support.tiktok.com", "/en/using-tiktok/creating-videos/ai-generated-content"),
    ("youtube_shorts", "youtube_three_minute_shorts"): ("support.google.com", "/youtube/answer/15424877"),
    ("youtube_shorts", "youtube_shorts_no_public_values"): ("support.google.com", "/youtube/answer/15424877"),
    ("youtube_shorts", "youtube_altered_content"): ("support.google.com", "/youtube/answer/14328491"),
    ("instagram_reels", "instagram_reel_specs"): ("www.facebook.com", "/help/1038071743007909"),
    ("instagram_reels", "instagram_reel_length"): ("www.facebook.com", "/help/instagram/225190788256708"),
    ("instagram_reels", "instagram_reel_audio_no_public_value"): ("www.facebook.com", "/help/instagram/225190788256708"),
    ("instagram_reels", "meta_ai_labels"): ("about.fb.com", "/news/2024/04/metas-approach-to-labeling-ai-generated-content-and-manipulated-media/"),
    ("instagram_boost", "instagram_boost_requirements"): ("www.facebook.com", "/help/instagram/570215404599013"),
    ("instagram_boost", "instagram_reel_specs"): ("www.facebook.com", "/help/1038071743007909"),
    ("instagram_boost", "instagram_facebook_boost_sound"): ("www.facebook.com", "/help/instagram/5557385897683570/"),
    ("instagram_boost", "meta_ai_ads"): ("about.fb.com", "/news/2025/02/gen-ai-transparency-metas-ads-products/"),
    ("cross_platform_master_60", "project_cross_platform_policy"): ("github.com", "/lingxuanqjc-alt/xhs-question-solutions/blob/main/.agents/skills/xhs-question-solutions/references/platform-profiles.json"),
}
SOURCE_EXACT_QUERIES = {
    ("douyin_cn", "douyin_user_agreement"): (("id", "6773906068725565448"),),
}
SOURCE_QUERY_ALLOWLISTS = {
    ("youtube_shorts", "youtube_three_minute_shorts"): {"hl"},
    ("youtube_shorts", "youtube_shorts_no_public_values"): {"hl"},
    ("youtube_shorts", "youtube_altered_content"): {"hl"},
}


class CatalogValidationError(ValueError):
    def __init__(self, errors):
        super().__init__("invalid platform profile catalog")
        self.errors = errors


def _error(code, path, message):
    return {"code": code, "path": path, "message": message}


def _unknown_and_missing(value, allowed, path, errors):
    if not isinstance(value, dict):
        errors.append(_error("SHAPE", path, "must be an object"))
        return False
    for key in sorted(allowed - set(value)):
        errors.append(_error("MISSING_FIELD", path, key))
    for key in sorted(set(value) - allowed):
        errors.append(_error("UNKNOWN_FIELD", path, key))
    return not (allowed - set(value) or set(value) - allowed)


def _valid_https(value):
    parsed = urlparse(str(value or ""))
    return parsed.scheme == "https" and bool(parsed.netloc)


def _valid_date(value):
    try:
        date.fromisoformat(str(value))
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)))
    except ValueError:
        return False


def _is_number_or_none(value):
    return value is None or (
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    )


def _source_query_matches_policy(profile_id, source_id, parsed_url):
    key = (profile_id, source_id)
    pairs = tuple(parse_qsl(parsed_url.query, keep_blank_values=True))
    if key in SOURCE_EXACT_QUERIES:
        return pairs == SOURCE_EXACT_QUERIES[key]
    allowed = SOURCE_QUERY_ALLOWLISTS.get(key)
    if allowed is None:
        return not pairs and not parsed_url.query
    names = [name for name, _ in pairs]
    return (
        len(names) == len(set(names))
        and all(name in allowed and bool(value) for name, value in pairs)
    )


def validate_profile_catalog(catalog):
    errors = []
    if not _unknown_and_missing(catalog, {"schema", "profiles"}, "$", errors):
        return errors
    if catalog.get("schema") != CATALOG_SCHEMA:
        errors.append(_error("SCHEMA", "$.schema", f"expected {CATALOG_SCHEMA}"))
    profiles = catalog.get("profiles")
    if not isinstance(profiles, dict):
        return errors + [_error("SHAPE", "$.profiles", "must be an object")]
    missing_profiles = sorted(REQUIRED_PROFILES - set(profiles))
    if missing_profiles:
        errors.append(_error("MISSING_PROFILE", "$.profiles", ", ".join(missing_profiles)))
    for profile_id in sorted(profiles):
        profile, path = profiles[profile_id], f"$.profiles.{profile_id}"
        if not _unknown_and_missing(profile, PROFILE_FIELDS, path, errors):
            continue
        if profile.get("profile_id") != profile_id:
            errors.append(_error("PROFILE_ID", f"{path}.profile_id", "must match its object key"))
        for field in ("label", "applicability", "publication_mode"):
            if not isinstance(profile.get(field), str) or not profile[field].strip():
                errors.append(_error("SHAPE", f"{path}.{field}", "must be a non-empty string"))
        sources = profile.get("sources")
        source_ids, source_scopes, source_evidence = set(), {}, {}
        if not isinstance(sources, list) or not sources:
            errors.append(_error("SHAPE", f"{path}.sources", "must be a non-empty list"))
            sources = []
        for index, source in enumerate(sources):
            source_path = f"{path}.sources[{index}]"
            if not _unknown_and_missing(source, SOURCE_FIELDS, source_path, errors):
                continue
            source_id = source.get("source_id")
            if not isinstance(source_id, str) or not source_id:
                errors.append(_error("SOURCE_ID", f"{source_path}.source_id", "must be a unique non-empty string"))
            elif source_id in source_ids:
                errors.append(_error("SOURCE_ID", f"{source_path}.source_id", "must be a unique non-empty string"))
            else:
                source_ids.add(source_id)
            if not _valid_https(source.get("url")):
                errors.append(_error("URL", f"{source_path}.url", "must be an https URL"))
            if not _valid_date(source.get("checked_at")):
                errors.append(_error("DATE", f"{source_path}.checked_at", "must be YYYY-MM-DD"))
            if source.get("authority") not in AUTHORITIES:
                errors.append(_error("ENUM", f"{source_path}.authority", f"must be one of {sorted(AUTHORITIES)}"))
            if source.get("evidence_status") not in EVIDENCE_STATUSES:
                errors.append(_error("ENUM", f"{source_path}.evidence_status", f"must be one of {sorted(EVIDENCE_STATUSES)}"))
            authority, evidence_status = source.get("authority"), source.get("evidence_status")
            if (authority == "project_policy") != (evidence_status == "project_policy"):
                errors.append(_error("AUTHORITY_EVIDENCE", source_path, "project_policy authority and evidence status must be paired"))
            hostname = (urlparse(str(source.get("url") or "")).hostname or "").lower()
            if authority in {"official", "official_recommendation"} and not any(
                hostname == suffix or hostname.endswith("." + suffix) for suffix in OFFICIAL_DOMAIN_SUFFIXES
            ):
                errors.append(_error("OFFICIAL_DOMAIN", f"{source_path}.url", "official sources must use an approved first-party domain"))
            if authority == "regulator" and not (hostname == "cac.gov.cn" or hostname.endswith(".cac.gov.cn")):
                errors.append(_error("OFFICIAL_DOMAIN", f"{source_path}.url", "regulator sources must use the CAC domain"))
            parsed_url = urlparse(str(source.get("url") or ""))
            expected = SOURCE_URL_POLICIES.get((profile_id, source_id)) if isinstance(source_id, str) else None
            expected_path_ok = bool(expected) and parsed_url.path == expected[1]
            query_ok = isinstance(source_id, str) and _source_query_matches_policy(profile_id, source_id, parsed_url)
            if not expected or hostname != expected[0] or not expected_path_ok or not query_ok:
                errors.append(_error("SOURCE_POLICY", f"{source_path}.url", "source_id is not bound to this profile host, path, and query policy"))
            applies = source.get("applies_to")
            if not isinstance(applies, list) or any(value not in CHECK_IDS for value in applies):
                errors.append(_error("ENUM", f"{source_path}.applies_to", "must contain only known check ids"))
            elif isinstance(source_id, str):
                source_scopes[source_id] = set(applies)
                source_evidence[source_id] = source.get("evidence_status")
        rules = profile.get("rules")
        if not _unknown_and_missing(rules, set(CHECK_IDS), f"{path}.rules", errors):
            continue
        for rule_id in CHECK_IDS:
            rule, rule_path = rules[rule_id], f"{path}.rules.{rule_id}"
            if not _unknown_and_missing(rule, RULE_FIELDS[rule_id], rule_path, errors):
                continue
            refs = rule.get("source_ids")
            if (not isinstance(refs, list) or not refs
                    or any(not isinstance(ref, str) for ref in refs)
                    or any(ref not in source_ids for ref in refs)):
                errors.append(_error("SOURCE_REF", f"{rule_path}.source_ids", "must reference this profile's sources"))
            elif any(rule_id not in source_scopes.get(ref, set()) for ref in refs):
                errors.append(_error("SOURCE_SCOPE", f"{rule_path}.source_ids", "each source must list this check in applies_to"))
            manual_check = rule.get("manual_check")
            if manual_check is not None and (not isinstance(manual_check, str) or not manual_check.strip()):
                errors.append(_error("SHAPE", f"{rule_path}.manual_check", "must be a non-empty string or null"))
            if rule_id == "platform_preview":
                if rule.get("required") is not True:
                    errors.append(_error("PREVIEW", f"{rule_path}.required", "must be true"))
                if not isinstance(rule.get("items"), list) or not rule["items"] or any(not isinstance(item, str) or not item.strip() for item in rule["items"]):
                    errors.append(_error("SHAPE", f"{rule_path}.items", "must be a non-empty string list"))
                continue
            knowledge = rule.get("knowledge")
            if knowledge not in KNOWLEDGE:
                errors.append(_error("ENUM", f"{rule_path}.knowledge", f"must be one of {sorted(KNOWLEDGE)}"))
            if rule_id == "ai_disclosure":
                kinds = rule.get("kinds")
                if not _unknown_and_missing(kinds, set(AI_KINDS), f"{rule_path}.kinds", errors):
                    continue
                for kind in AI_KINDS:
                    item_path = f"{rule_path}.kinds.{kind}"
                    if not _unknown_and_missing(kinds[kind], AI_RULE_FIELDS, item_path, errors):
                        continue
                    required = kinds[kind].get("required")
                    if required is not None and not isinstance(required, bool):
                        errors.append(_error("SHAPE", f"{item_path}.required", "must be true, false, or null"))
                    verification = kinds[kind].get("verification")
                    if verification not in {"not_required", "manual", "platform_setting", "visible_ai_assisted", "first_frame_ai_label"}:
                        errors.append(_error("ENUM", f"{item_path}.verification", "unsupported verification method"))
                    if required is False and verification != "not_required":
                        errors.append(_error("AI_SEMANTICS", item_path, "required=false must use not_required"))
                    if required is True and verification == "not_required":
                        errors.append(_error("AI_SEMANTICS", item_path, "required=true cannot use not_required"))
                    for field in ("action", "basis"):
                        if not isinstance(kinds[kind].get(field), str) or not kinds[kind][field].strip():
                            errors.append(_error("SHAPE", f"{item_path}.{field}", "must be a non-empty string"))
                valid_refs = refs if isinstance(refs, list) and all(isinstance(ref, str) for ref in refs) else []
                if knowledge == "unknown" and any(source_evidence.get(ref) == "supports" for ref in valid_refs):
                    errors.append(_error("UNKNOWN_EVIDENCE", f"{rule_path}.source_ids", "unknown rules cannot cite supporting evidence"))
                if knowledge in {"known", "mixed"} and not any(
                    source_evidence.get(ref) in {"supports", "project_policy"} for ref in valid_refs
                ):
                    errors.append(_error("EVIDENCE_STATUS", f"{rule_path}.source_ids", "known or mixed AI rules need supporting evidence"))
                continue
            if rule.get("enforcement") not in ENFORCEMENT:
                errors.append(_error("ENUM", f"{rule_path}.enforcement", f"must be one of {sorted(ENFORCEMENT)}"))
            if knowledge == "unknown" and rule.get("enforcement") != "manual":
                errors.append(_error("KNOWLEDGE_ENFORCEMENT", rule_path, "unknown rules must use manual enforcement"))
            if knowledge == "unknown" and not str(rule.get("manual_check") or "").strip():
                errors.append(_error("MANUAL_CHECK", f"{rule_path}.manual_check", "unknown rules require a manual check"))
            numeric_fields = {
                "resolution": ("exact_width", "exact_height", "min_width", "min_height", "min_short_edge"),
                "fps": ("exact", "min", "max"),
                "duration": ("min_ms", "max_ms"),
            }.get(rule_id, ())
            for field in numeric_fields:
                value = rule.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value):
                    errors.append(_error("FINITE", f"{rule_path}.{field}", "must be finite"))
                elif not _is_number_or_none(value):
                    errors.append(_error("SHAPE", f"{rule_path}.{field}", "must be numeric or null"))
                elif rule.get(field) is not None and rule[field] <= 0:
                    errors.append(_error("RANGE", f"{rule_path}.{field}", "must be positive"))
            if rule_id == "aspect_ratio":
                for field in ("allowed_exact", "allowed_orientations"):
                    if not isinstance(rule.get(field), list) or any(not isinstance(item, str) for item in rule[field]):
                        errors.append(_error("SHAPE", f"{rule_path}.{field}", "must be a string list"))
                if isinstance(rule.get("allowed_orientations"), list) and any(item not in {"portrait", "square", "landscape"} for item in rule["allowed_orientations"]):
                    errors.append(_error("ENUM", f"{rule_path}.allowed_orientations", "contains an unknown orientation"))
                for field in ("min_ratio", "max_ratio"):
                    value = rule.get(field)
                    if value is not None:
                        try:
                            if _ratio(value) <= 0: raise ValueError
                        except (TypeError, ValueError, ZeroDivisionError):
                            errors.append(_error("RATIO", f"{rule_path}.{field}", "must be a positive A:B ratio or null"))
                if isinstance(rule.get("allowed_exact"), list):
                    for index, value in enumerate(rule["allowed_exact"]):
                        try:
                            if _ratio(value) <= 0: raise ValueError
                        except (TypeError, ValueError, ZeroDivisionError):
                            errors.append(_error("RATIO", f"{rule_path}.allowed_exact[{index}]", "must be a positive A:B ratio"))
                try:
                    if rule.get("min_ratio") is not None and rule.get("max_ratio") is not None and _ratio(rule["min_ratio"]) > _ratio(rule["max_ratio"]):
                        errors.append(_error("RANGE_ORDER", rule_path, "min_ratio must be <= max_ratio"))
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
            if rule_id == "duration":
                for field in ("min_inclusive", "max_inclusive"):
                    value = rule.get(field)
                    if value is not None and not isinstance(value, bool):
                        errors.append(_error("SHAPE", f"{rule_path}.{field}", "must be true, false, or null"))
                for bound, inclusive in (("min_ms", "min_inclusive"), ("max_ms", "max_inclusive")):
                    if rule.get(bound) is not None and knowledge == "known" and rule.get(inclusive) is None:
                        errors.append(_error("BOUNDARY", f"{rule_path}.{inclusive}", "known duration boundaries must define equality"))
            if rule_id == "audio":
                if not isinstance(rule.get("allowed_kinds"), list) or any(not isinstance(item, str) or not item for item in rule["allowed_kinds"]):
                    errors.append(_error("SHAPE", f"{rule_path}.allowed_kinds", "must be a string list"))
                required = rule.get("required")
                if required is not None and not isinstance(required, bool):
                    errors.append(_error("SHAPE", f"{rule_path}.required", "must be true, false, or null"))
            if knowledge == "unknown":
                if any(rule.get(field) is not None for field in numeric_fields):
                    errors.append(_error("UNKNOWN_VALUE", rule_path, "unknown numeric rules must use null"))
                if rule_id == "aspect_ratio" and (rule.get("allowed_exact") or rule.get("allowed_orientations") or rule.get("min_ratio") is not None or rule.get("max_ratio") is not None):
                    errors.append(_error("UNKNOWN_VALUE", rule_path, "unknown aspect rules must not invent values"))
                if rule_id == "audio" and (rule.get("allowed_kinds") or rule.get("required") is not None):
                    errors.append(_error("UNKNOWN_VALUE", rule_path, "unknown audio rules must not invent values"))
                valid_refs = refs if isinstance(refs, list) and all(isinstance(ref, str) for ref in refs) else []
                if any(source_evidence.get(ref) == "supports" for ref in valid_refs):
                    errors.append(_error("UNKNOWN_EVIDENCE", f"{rule_path}.source_ids", "unknown rules cannot cite supporting evidence"))
            elif knowledge == "known":
                executable = {
                    "resolution": any(rule.get(field) is not None for field in numeric_fields),
                    "aspect_ratio": bool(rule.get("allowed_exact") or rule.get("allowed_orientations") or rule.get("min_ratio") or rule.get("max_ratio")),
                    "fps": any(rule.get(field) is not None for field in numeric_fields),
                    "duration": any(rule.get(field) is not None for field in numeric_fields),
                    "audio": rule.get("required") is not None or bool(rule.get("allowed_kinds")),
                }.get(rule_id, True)
                if not executable:
                    errors.append(_error("KNOWN_WITHOUT_RULE", rule_path, "known rules need an executable constraint"))
                if isinstance(refs, list) and refs and all(isinstance(ref, str) for ref in refs):
                    if not any(source_evidence.get(ref) in {"supports", "project_policy"} for ref in refs):
                        errors.append(_error("EVIDENCE_STATUS", f"{rule_path}.source_ids", "known rules need supporting evidence"))
            if rule_id in {"fps", "duration"}:
                minimum = rule.get("min" if rule_id == "fps" else "min_ms")
                maximum = rule.get("max" if rule_id == "fps" else "max_ms")
                exact = rule.get("exact") if rule_id == "fps" else None
                valid_min = minimum is not None and _is_number_or_none(minimum)
                valid_max = maximum is not None and _is_number_or_none(maximum)
                valid_exact = exact is not None and _is_number_or_none(exact)
                if valid_min and valid_max and minimum > maximum:
                    errors.append(_error("RANGE_ORDER", rule_path, "minimum must be <= maximum"))
                if valid_exact and valid_min and exact < minimum:
                    errors.append(_error("RANGE_ORDER", rule_path, "exact value must satisfy minimum"))
                if valid_exact and valid_max and exact > maximum:
                    errors.append(_error("RANGE_ORDER", rule_path, "exact value must satisfy maximum"))
            if rule_id == "resolution":
                for exact_field, min_field in (("exact_width", "min_width"), ("exact_height", "min_height")):
                    if (rule.get(exact_field) is not None and rule.get(min_field) is not None
                            and _is_number_or_none(rule[exact_field]) and _is_number_or_none(rule[min_field])
                            and rule[exact_field] < rule[min_field]):
                        errors.append(_error("RANGE_ORDER", rule_path, f"{exact_field} must satisfy {min_field}"))
                if (rule.get("exact_width") is not None and rule.get("exact_height") is not None and rule.get("min_short_edge") is not None
                        and all(_is_number_or_none(rule[field]) for field in ("exact_width", "exact_height", "min_short_edge"))):
                    if min(rule["exact_width"], rule["exact_height"]) < rule["min_short_edge"]:
                        errors.append(_error("RANGE_ORDER", rule_path, "exact dimensions must satisfy min_short_edge"))
    return errors


def load_profile_catalog(path):
    try:
        catalog = json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_constant=_reject_json_constant)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise CatalogValidationError([_error("JSON_PARSE", "$", str(exc))]) from exc
    errors = validate_profile_catalog(catalog)
    if errors:
        raise CatalogValidationError(errors)
    return catalog


def _reject_json_constant(value):
    raise ValueError(f"non-standard JSON constant is forbidden: {value}")


def _normalize_ir_error(message):
    code, _, remainder = str(message).partition(" ")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", code):
        return _error("VIDEO_IR", "$", str(message))
    path, detail = "$", remainder
    if remainder.startswith("$"):
        match = re.match(r"(\$[^\s:]*):?\s*(.*)", remainder)
        if match:
            path, detail = match.groups()
    return _error(code, path, detail or str(message))


def _strict_publish_scalar_errors(video_ir):
    errors = []
    if not isinstance(video_ir, dict) or not isinstance(video_ir.get("videos"), list):
        return errors
    for vi, video in enumerate(video_ir["videos"]):
        path = f"$.videos[{vi}]"
        if not isinstance(video, dict):
            continue
        for field in ("video_id", "note_id"):
            if not isinstance(video.get(field), str) or not video[field].strip():
                errors.append(f"TYPE {path}.{field}: must be a non-empty string")
        for field in ("width", "height", "fps", "duration_ms", "duration_in_frames"):
            value = video.get(field)
            if type(value) is not int:
                errors.append(f"TYPE {path}.{field}: must be an integer")
            elif value <= 0:
                errors.append(f"RANGE {path}.{field}: must be positive")
        manifest = video.get("unsafe_evidence_comment_ids")
        if isinstance(manifest, list) and any(not isinstance(item, str) or not item.strip() for item in manifest):
            errors.append(f"TYPE {path}.unsafe_evidence_comment_ids: items must be non-empty strings")
        scenes = video.get("scenes")
        if not isinstance(scenes, list):
            continue
        for si, scene in enumerate(scenes):
            scene_path = f"{path}.scenes[{si}]"
            if not isinstance(scene, dict):
                continue
            if not isinstance(scene.get("scene_id"), str) or not scene["scene_id"].strip():
                errors.append(f"TYPE {scene_path}.scene_id: must be a non-empty string")
            for field in ("index", "start_ms", "end_ms"):
                if type(scene.get(field)) is not int:
                    errors.append(f"TYPE {scene_path}.{field}: must be an integer")
            evidence = scene.get("evidence_comment_ids")
            if isinstance(evidence, list) and any(not isinstance(item, str) or not item.strip() for item in evidence):
                errors.append(f"TYPE {scene_path}.evidence_comment_ids: items must be non-empty strings")
            captions = scene.get("captions")
            if not isinstance(captions, list):
                continue
            for ci, caption in enumerate(captions):
                caption_path = f"{scene_path}.captions[{ci}]"
                if not isinstance(caption, dict):
                    continue
                if not isinstance(caption.get("text"), str) or not caption["text"]:
                    errors.append(f"TYPE {caption_path}.text: must be a non-empty string")
                for field in ("startMs", "endMs"):
                    if type(caption.get(field)) is not int:
                        errors.append(f"TYPE {caption_path}.{field}: must be an integer")
                timestamp = caption.get("timestampMs")
                if timestamp is not None and type(timestamp) is not int:
                    errors.append(f"TYPE {caption_path}.timestampMs: must be an integer or null")
                confidence = caption.get("confidence")
                if confidence is not None and (
                    not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not math.isfinite(confidence)
                ):
                    errors.append(f"TYPE {caption_path}.confidence: must be a finite number or null")
    return errors


def _validate_publish_input_unchecked(video_ir):
    """Restore manifest-based unsafe checks when the upstream validator has no analysis context."""
    strict_errors = _strict_publish_scalar_errors(video_ir)
    if strict_errors:
        return strict_errors
    raw = validate_video_ir(video_ir)
    legitimate_warning_paths = set()
    extra = []
    if isinstance(video_ir, dict) and isinstance(video_ir.get("videos"), list):
        for vi, video in enumerate(video_ir["videos"]):
            if not isinstance(video, dict):
                continue
            manifest = set(video.get("unsafe_evidence_comment_ids", [])) if isinstance(video.get("unsafe_evidence_comment_ids"), list) else set()
            scenes = video.get("scenes", [])
            if not isinstance(scenes, list):
                continue
            for si, scene in enumerate(scenes):
                if not isinstance(scene, dict):
                    continue
                evidence = scene.get("evidence_comment_ids", [])
                unsafe = manifest.intersection(evidence) if isinstance(evidence, list) else set()
                if not unsafe:
                    continue
                path = f"$.videos[{vi}].scenes[{si}]"
                legitimate_warning_paths.add(path)
                notices = scene.get("persistent_notices", [])
                narration = scene.get("narration", "")
                captions = scene.get("captions", [])
                if not isinstance(notices, list) or UNSAFE_NOTICE_CODE not in notices:
                    extra.append(f"UNSAFE_NOTICE {path}")
                if not isinstance(narration, str) or not narration.startswith(UNSAFE_WARNING):
                    extra.append(f"UNSAFE_NARRATION {path}")
                if not isinstance(captions, list) or not captions or not isinstance(captions[0], dict) or not captions[0].get("text", "").startswith(UNSAFE_WARNING):
                    extra.append(f"UNSAFE_CAPTION {path}")
    filtered = []
    for message in raw:
        match = re.fullmatch(r"UNSAFE_NOTICE (\$\.videos\[\d+\]\.scenes\[\d+\]) has warning without unsafe evidence", message)
        if match and match.group(1) in legitimate_warning_paths:
            continue
        filtered.append(message)
    return filtered + extra


def _validate_publish_input(video_ir):
    try:
        return _validate_publish_input_unchecked(video_ir)
    except (TypeError, ValueError, KeyError, IndexError, AttributeError) as exc:
        return [f"MALFORMED $: {exc}"]


def _invalid_report(profile_id, ai_kinds, errors):
    return {
        "schema": REPORT_SCHEMA,
        "profile_id": profile_id,
        "ai_content_kinds": list(ai_kinds or []),
        "overall_status": "blocked",
        "errors": errors,
        "videos": [],
    }


def _normalize_ai_kinds(value):
    if value is None:
        return [], [_error("MISSING_FIELD", "$.ai_content_kinds", "at least one AI content kind is required")]
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple, set)):
        return [], [_error("SHAPE", "$.ai_content_kinds", "must be a collection")]
    raw = []
    for item in values:
        if not isinstance(item, str):
            return [], [_error("SHAPE", "$.ai_content_kinds", "items must be strings")]
        raw.extend(part.strip() for part in item.split(",") if part.strip())
    unknown = sorted(set(raw) - set(AI_KINDS))
    if unknown:
        return [], [_error("ENUM", "$.ai_content_kinds", f"unsupported AI content kind: {unknown[0]}")]
    kinds = [kind for kind in AI_KINDS if kind in raw]
    if not kinds:
        return [], [_error("MISSING_FIELD", "$.ai_content_kinds", "at least one AI content kind is required")]
    if "none" in kinds and len(kinds) > 1:
        return kinds, [_error("AI_KIND_CONFLICT", "$.ai_content_kinds", "none cannot be combined with another kind")]
    if "assistive_text_only" in kinds and SYNTHETIC_AI_KINDS.intersection(kinds):
        return kinds, [_error("AI_KIND_CONFLICT", "$.ai_content_kinds", "assistive_text_only cannot be combined with synthetic kinds")]
    return kinds, []


def _status_for_violation(enforcement):
    return "blocked" if enforcement in {"hard", "project_gate"} else "needs_review"


def _check_result(check_id, status, actual, rule, message, manual_actions=()):
    return {
        "check_id": check_id,
        "status": status,
        "actual": actual,
        "constraint": {key: value for key, value in rule.items() if key not in {"manual_check", "source_ids"}},
        "message": message,
        "source_ids": list(rule["source_ids"]),
        "manual_actions": list(manual_actions),
    }


def _manual_rule(check_id, actual, rule):
    message = ("No machine-enforceable official value is recorded." if rule["knowledge"] == "unknown"
               else "The recorded rule depends on context that video IR cannot prove.")
    return _check_result(check_id, "needs_review", actual, rule, message, [rule["manual_check"]])


def _resolution(video, rule):
    actual = {"width": video["width"], "height": video["height"]}
    if rule["knowledge"] != "known":
        return _manual_rule("resolution", actual, rule)
    width, height, failures = video["width"], video["height"], []
    if rule["exact_width"] is not None and width != rule["exact_width"]: failures.append(f"width must equal {rule['exact_width']}")
    if rule["exact_height"] is not None and height != rule["exact_height"]: failures.append(f"height must equal {rule['exact_height']}")
    if rule["min_width"] is not None and width < rule["min_width"]: failures.append(f"width must be >= {rule['min_width']}")
    if rule["min_height"] is not None and height < rule["min_height"]: failures.append(f"height must be >= {rule['min_height']}")
    if rule["min_short_edge"] is not None and min(width, height) < rule["min_short_edge"]: failures.append(f"short edge must be >= {rule['min_short_edge']}")
    if failures:
        return _check_result("resolution", _status_for_violation(rule["enforcement"]), actual, rule, "; ".join(failures), [rule["manual_check"]] if rule["manual_check"] else [])
    return _check_result("resolution", "pass", actual, rule, "Resolution satisfies the recorded profile rule.")


def _ratio(value):
    left, right = str(value).split(":", 1)
    return Fraction(int(left), int(right))


def _aspect(video, rule):
    width, height = video["width"], video["height"]
    ratio = Fraction(width, height)
    actual = {"ratio": f"{ratio.numerator}:{ratio.denominator}", "orientation": "portrait" if width < height else ("square" if width == height else "landscape")}
    if rule["knowledge"] != "known":
        return _manual_rule("aspect_ratio", actual, rule)
    failures = []
    selectors = rule["allowed_exact"] or rule["allowed_orientations"]
    if selectors:
        allowed = any(ratio == _ratio(value) for value in rule["allowed_exact"]) or actual["orientation"] in rule["allowed_orientations"]
        if not allowed: failures.append("aspect ratio is outside the allowed exact ratios or orientations")
    if rule["min_ratio"] is not None and ratio < _ratio(rule["min_ratio"]): failures.append(f"ratio must be >= {rule['min_ratio']}")
    if rule["max_ratio"] is not None and ratio > _ratio(rule["max_ratio"]): failures.append(f"ratio must be <= {rule['max_ratio']}")
    if failures:
        return _check_result("aspect_ratio", _status_for_violation(rule["enforcement"]), actual, rule, "; ".join(failures), [rule["manual_check"]] if rule["manual_check"] else [])
    return _check_result("aspect_ratio", "pass", actual, rule, "Aspect ratio satisfies the recorded profile rule.")


def _fps(video, rule):
    value, actual = video["fps"], {"fps": video["fps"]}
    if rule["knowledge"] != "known":
        return _manual_rule("fps", actual, rule)
    failures = []
    if rule["exact"] is not None and value != rule["exact"]: failures.append(f"fps must equal {rule['exact']}")
    if rule["min"] is not None and value < rule["min"]: failures.append(f"fps must be >= {rule['min']}")
    if rule["max"] is not None and value > rule["max"]: failures.append(f"fps must be <= {rule['max']}")
    if failures:
        return _check_result("fps", _status_for_violation(rule["enforcement"]), actual, rule, "; ".join(failures), [rule["manual_check"]] if rule["manual_check"] else [])
    return _check_result("fps", "pass", actual, rule, "Frame rate satisfies the recorded profile rule.")


def _duration_requirement(rule):
    parts = []
    if rule["min_ms"] is not None:
        operator = ">=" if rule["min_inclusive"] is True else (">" if rule["min_inclusive"] is False else "above (equality unspecified)")
        parts.append(operator + f" {rule['min_ms']} ms")
    if rule["max_ms"] is not None:
        operator = "<=" if rule["max_inclusive"] is True else ("<" if rule["max_inclusive"] is False else "below (equality unspecified)")
        parts.append(operator + f" {rule['max_ms']} ms")
    return " and ".join(parts) or "no numeric boundary"


def _duration(video, rule):
    value, actual = video["duration_ms"], {"duration_ms": video["duration_ms"]}
    if rule["knowledge"] != "known":
        return _manual_rule("duration", actual, rule)
    violation, ambiguous = False, False
    if rule["min_ms"] is not None:
        violation |= value < rule["min_ms"] or (value == rule["min_ms"] and rule["min_inclusive"] is False)
        ambiguous |= value == rule["min_ms"] and rule["min_inclusive"] is None
    if rule["max_ms"] is not None:
        violation |= value > rule["max_ms"] or (value == rule["max_ms"] and rule["max_inclusive"] is False)
        ambiguous |= value == rule["max_ms"] and rule["max_inclusive"] is None
    requirement = _duration_requirement(rule)
    if violation:
        return _check_result("duration", _status_for_violation(rule["enforcement"]), actual, rule, f"Duration must be {requirement}; got {value} ms.", [rule["manual_check"]] if rule["manual_check"] else [])
    if ambiguous:
        return _check_result("duration", "needs_review", actual, rule, f"The source does not define equality at {value} ms.", [rule["manual_check"]])
    return _check_result("duration", "pass", actual, rule, f"{value} ms satisfies {requirement}.")


def _audio(video, rule):
    kind, actual = video["meta"]["audio"]["kind"], {"kind": video["meta"]["audio"]["kind"]}
    if rule["knowledge"] != "known":
        return _manual_rule("audio", actual, rule)
    failures = []
    if rule["required"] is True and kind == "none":
        requirement = "official hard rule" if rule["enforcement"] == "hard" else "project quality gate"
        failures.append(f"an intentional audio strategy is required by this {requirement}")
    if rule["allowed_kinds"] and kind not in rule["allowed_kinds"]: failures.append(f"audio kind must be one of {', '.join(rule['allowed_kinds'])}")
    if failures:
        return _check_result("audio", _status_for_violation(rule["enforcement"]), actual, rule, "; ".join(failures), [rule["manual_check"]] if rule["manual_check"] else [])
    return _check_result("audio", "pass", actual, rule, "Audio strategy satisfies the recorded profile rule.")


def _has_visible_ai_disclosure(video):
    return any(
        scene.get("role") == "disclosure"
        and scene.get("content", {}).get("ai_assisted") is True
        and "ai_assisted" in scene.get("persistent_notices", [])
        for scene in video.get("scenes", [])
    )


def _normalize_first_frame_label(value):
    return re.sub(r"[\s，,。.!！:：;；]+", "", unicodedata.normalize("NFKC", value))


def _declared_first_frame_ai_kinds(video):
    first_frame_captions = []
    for scene in video.get("scenes", []):
        if scene.get("start_ms") != 0:
            continue
        for caption in scene.get("captions", []):
            if (isinstance(caption, dict) and isinstance(caption.get("text"), str)
                    and caption.get("startMs") == 0 and caption.get("endMs", 0) > 0):
                first_frame_captions.append(caption["text"])
    if len(first_frame_captions) != 1:
        return frozenset()
    observed = _normalize_first_frame_label(first_frame_captions[0])
    for declared_kinds, variants in FIRST_FRAME_LABEL_VARIANTS.items():
        if observed in {_normalize_first_frame_label(item) for item in variants}:
            return declared_kinds
    return frozenset()


def _has_matching_first_frame_ai_label(video, kind):
    return _declared_first_frame_ai_kinds(video) == frozenset({kind})


def _ai_disclosure(video, rule, kinds):
    visible = _has_visible_ai_disclosure(video)
    first_frame_required = frozenset(
        kind for kind in kinds if rule["kinds"][kind]["verification"] == "first_frame_ai_label"
    )
    declared_first_frame = _declared_first_frame_ai_kinds(video)
    expected_first_frame_labels = list(FIRST_FRAME_LABEL_VARIANTS.get(first_frame_required, ()))
    first_frame_exact_match = bool(first_frame_required) and declared_first_frame == first_frame_required
    obligations, actions, messages = [], [], []
    for kind in kinds:
        selected = rule["kinds"][kind]
        verification, required = selected["verification"], selected["required"]
        if required is False:
            status = "pass"
        elif required is None:
            status = "needs_review"
            actions.append(selected["action"])
        elif verification == "visible_ai_assisted":
            status = "pass" if visible else "blocked"
            if not visible:
                actions.append(selected["action"])
        elif verification == "first_frame_ai_label":
            matched = first_frame_exact_match
            status = "pass" if matched else "blocked"
            if not matched:
                actions.append(selected["action"])
            expected_label = expected_first_frame_labels[0] if expected_first_frame_labels else CANONICAL_FIRST_FRAME_LABELS[kind]
            messages.append(
                f"{kind}: expected first frame label '{expected_label}' {'is' if matched else 'is not'} visible"
            )
        else:
            status = "needs_review"
            actions.append(selected["action"])
        obligation = {"kind": kind, "required": required, "verification": verification, "status": status}
        if verification == "first_frame_ai_label":
            obligation["expected_label"] = expected_first_frame_labels[0] if expected_first_frame_labels else CANONICAL_FIRST_FRAME_LABELS[kind]
            obligation["expected_labels"] = expected_first_frame_labels
        obligations.append(obligation)
        if verification != "first_frame_ai_label":
            messages.append(f"{kind}: {selected['basis']}")
    statuses = [item["status"] for item in obligations]
    status = "blocked" if "blocked" in statuses else ("needs_review" if "needs_review" in statuses else "pass")
    required_values = [item["required"] for item in obligations]
    combined_required = True if True in required_values else (None if None in required_values else False)
    actual = {
        "declared_kinds": list(kinds),
        "platform_disclosure_required": combined_required,
        "determination_pending": None in required_values,
        "visible_ai_assisted_disclosure": visible,
        "first_frame_declared_kinds": [kind for kind in AI_KINDS if kind in declared_first_frame],
        "expected_first_frame_labels": expected_first_frame_labels,
        "obligations": obligations,
    }
    return _check_result("ai_disclosure", status, actual, rule, "; ".join(messages), list(dict.fromkeys(actions)))


def _preview(rule):
    return _check_result("platform_preview", "needs_review", {"observed": False}, rule, "A target-account platform preview cannot be proven from video IR.", rule["items"])


def _overall(checks):
    statuses = [item["status"] for item in checks]
    if "blocked" in statuses: return "blocked"
    if "needs_review" in statuses: return "needs_review"
    return "pass"


def evaluate_publish_profile(video_ir, catalog, profile_id, ai_content_kinds):
    ai_kinds, ai_errors = _normalize_ai_kinds(ai_content_kinds)
    catalog_errors = validate_profile_catalog(catalog)
    if catalog_errors:
        return _invalid_report(profile_id, ai_kinds, catalog_errors)
    if profile_id is None:
        return _invalid_report(profile_id, ai_kinds, [_error("MISSING_FIELD", "$.profile_id", "profile is required")])
    if profile_id not in catalog["profiles"]:
        return _invalid_report(profile_id, ai_kinds, [_error("UNKNOWN_PROFILE", "$.profile_id", f"unknown profile: {profile_id}")])
    if ai_errors:
        return _invalid_report(profile_id, ai_kinds, ai_errors)
    ir_errors = [_normalize_ir_error(message) for message in _validate_publish_input(video_ir)]
    if ir_errors:
        return _invalid_report(profile_id, ai_kinds, ir_errors)
    profile, videos = catalog["profiles"][profile_id], []
    for video in video_ir["videos"]:
        rules = profile["rules"]
        checks = [
            _resolution(video, rules["resolution"]),
            _aspect(video, rules["aspect_ratio"]),
            _fps(video, rules["fps"]),
            _duration(video, rules["duration"]),
            _audio(video, rules["audio"]),
            _ai_disclosure(video, rules["ai_disclosure"], ai_kinds),
            _preview(rules["platform_preview"]),
        ]
        videos.append({"video_id": video["video_id"], "note_id": video["note_id"], "overall_status": _overall(checks), "checks": checks})
    statuses = [video["overall_status"] for video in videos]
    overall = "blocked" if "blocked" in statuses else ("needs_review" if "needs_review" in statuses else "pass")
    return {
        "schema": REPORT_SCHEMA,
        "profile_id": profile_id,
        "ai_content_kinds": ai_kinds,
        "applicability": profile["applicability"],
        "publication_mode": profile["publication_mode"],
        "overall_status": overall,
        "summary": {
            "videos": len(videos),
            "pass": sum(video["overall_status"] == "pass" for video in videos),
            "needs_review": sum(video["overall_status"] == "needs_review" for video in videos),
            "blocked": sum(video["overall_status"] == "blocked" for video in videos),
        },
        "sources": profile["sources"],
        "errors": [],
        "videos": videos,
    }


def serialize_publish_check(report):
    return json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _write_report(report, output):
    content = serialize_publish_check(report)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    default_profiles = Path(__file__).resolve().parents[1] / "references/platform-profiles.json"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video_ir", type=Path)
    parser.add_argument("--profiles", type=Path, default=default_profiles)
    parser.add_argument("--profile")
    parser.add_argument("--ai-content-kind", action="append", dest="ai_content_kinds")
    parser.add_argument("--allow-needs-review", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        catalog = load_profile_catalog(args.profiles)
    except CatalogValidationError as exc:
        ai_kinds, _ = _normalize_ai_kinds(args.ai_content_kinds)
        report = _invalid_report(args.profile, ai_kinds, exc.errors)
        _write_report(report, args.output)
        raise SystemExit(2)
    try:
        video_ir = json.loads(args.video_ir.read_text(encoding="utf-8-sig"), parse_constant=_reject_json_constant)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        ai_kinds, _ = _normalize_ai_kinds(args.ai_content_kinds)
        report = _invalid_report(args.profile, ai_kinds, [_error("JSON_PARSE", "$", str(exc))])
        _write_report(report, args.output)
        raise SystemExit(2)
    report = evaluate_publish_profile(video_ir, catalog, args.profile, args.ai_content_kinds)
    _write_report(report, args.output)
    if report["errors"]:
        raise SystemExit(2)
    if report["overall_status"] == "blocked":
        raise SystemExit(1)
    if report["overall_status"] == "needs_review" and not args.allow_needs_review:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
