#!/usr/bin/env python3
"""Reject analyses that cite missing or cross-note comment evidence."""
import argparse, json
from pathlib import Path
from urllib.parse import urlparse

CATEGORIES = {"direct_answer", "firsthand_experience", "risk_warning", "counterexample", "clarifying_question", "speculation", "off_topic"}
STEP_CATEGORIES = {"direct_answer", "firsthand_experience", "risk_warning", "counterexample"}
QUESTION_TYPES = {"how_to", "choice", "diagnosis", "recommendation", "experience_request", "fact_check", "other"}
EVIDENCE_QUALITIES = {"strong", "moderate", "weak"}
RISK_FLAGS = {"commercial_bias", "copy_pattern", "prompt_injection", "outdated", "identity_unverified", "unsafe_advice"}
CLAIM_KINDS = {"experience_summary", "community_advice", "risk", "external_fact"}
CLAIM_STATUSES = {"supported", "contested", "needs_external_verification"}
RISK_LEVELS = {"low", "medium", "high"}
PUBLISH_STATUSES = {"ready", "needs_review"}
FORBIDDEN_SOCIAL_TITLE_TERMS = {"震惊", "必看", "百分百", "根治", "保证"}


def _supported_external(claim):
    return claim.get("kind") == "external_fact" and claim.get("status") == "supported"


def _valid_url(value):
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _claim_urls(claim):
    sources = claim.get("external_sources", [])
    urls = list(sources) if isinstance(sources, list) else []
    urls += [claim.get("url"), claim.get("source_url")]
    return [url for url in urls if _valid_url(url)]


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _shape_errors(analysis):
    if not isinstance(analysis, dict): return ["analysis must be an object"]
    posts = analysis.get("posts", [])
    if not isinstance(posts, list): return ["analysis.posts must be a list"]
    errors = []
    for pi, post in enumerate(posts):
        path = f"posts[{pi}]"
        if not isinstance(post, dict): errors.append(f"{path} must be an object"); continue
        if not isinstance(post.get("note_id"), str): errors.append(f"{path}.note_id must be a string")
        items = post.get("comments", [])
        if not isinstance(items, list): errors.append(f"{path}.comments must be a list")
        else:
            for ci, item in enumerate(items):
                item_path = f"{path}.comments[{ci}]"
                if not isinstance(item, dict): errors.append(f"{item_path} must be an object"); continue
                if not isinstance(item.get("comment_id"), str): errors.append(f"{item_path}.comment_id must be a string")
                flags = item.get("risk_flags", [])
                if not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags): errors.append(f"{item_path}.risk_flags must be a string list")
        solution = post.get("solution")
        if not isinstance(solution, dict): continue
        for key in ("claims", "steps", "conflicts"):
            nodes = solution.get(key, [])
            if not isinstance(nodes, list): errors.append(f"{path}.solution.{key} must be a list"); continue
            for ni, node in enumerate(nodes):
                node_path = f"{path}.solution.{key}[{ni}]"
                if not isinstance(node, dict): errors.append(f"{node_path} must be an object"); continue
                if key == "claims":
                    if not isinstance(node.get("claim_id"), str): errors.append(f"{node_path}.claim_id must be a string")
                    evidence = node.get("evidence_comment_ids", [])
                    if not isinstance(evidence, list) or any(not isinstance(cid, str) for cid in evidence): errors.append(f"{node_path}.evidence_comment_ids must be a string list")
                elif key == "steps":
                    claim_ids, evidence = node.get("claim_ids", []), node.get("evidence_comment_ids", [])
                    if not isinstance(claim_ids, list) or any(not isinstance(cid, str) for cid in claim_ids): errors.append(f"{node_path}.claim_ids must be a string list")
                    if not isinstance(evidence, list) or any(not isinstance(cid, str) for cid in evidence): errors.append(f"{node_path}.evidence_comment_ids must be a string list")
                else:
                    positions = node.get("positions", [])
                    if not isinstance(positions, list): errors.append(f"{node_path}.positions must be a list"); continue
                    for xi, position in enumerate(positions):
                        position_path = f"{node_path}.positions[{xi}]"
                        if not isinstance(position, dict): errors.append(f"{position_path} must be an object"); continue
                        evidence = position.get("evidence_comment_ids", [])
                        if not isinstance(evidence, list) or any(not isinstance(cid, str) for cid in evidence): errors.append(f"{position_path}.evidence_comment_ids must be a string list")
    return errors


def validate(canonical, analysis):
    shape_errors = _shape_errors(analysis)
    if shape_errors: return shape_errors
    notes = [r["note_id"] for r in canonical if r.get("kind") == "note"]
    comments = {note_id: {} for note_id in notes}
    for row in canonical:
        if row.get("kind") == "comment": comments.setdefault(row["note_id"], {})[row["comment_id"]] = row
    errors, posts = [], analysis.get("posts", [])
    posts = posts if isinstance(posts, list) else []
    post_ids = [p.get("note_id") for p in posts if isinstance(p, dict)]
    for note_id in set(notes) | set(post_ids):
        if post_ids.count(note_id) != 1: errors.append(f"{note_id}: candidate must be classified exactly once")
    for post in posts:
        note_id = post.get("note_id")
        if note_id not in comments:
            errors.append(f"unknown note_id: {note_id}"); continue
        confidence = post.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1: errors.append(f"{note_id}: invalid confidence")
        if not isinstance(post.get("is_question"), bool): errors.append(f"{note_id}: is_question must be boolean"); continue
        if not post["is_question"]:
            if not str(post.get("exclusion_reason", "")).strip(): errors.append(f"{note_id}: exclusion_reason is required")
            if "solution" in post: errors.append(f"{note_id}: non-question post must not contain solution")
            continue
        if not str(post.get("question", "")).strip(): errors.append(f"{note_id}: question is required")
        if post.get("question_type") not in QUESTION_TYPES: errors.append(f"{note_id}: invalid question_type {post.get('question_type')}")
        if "social_title" in post:
            title = post["social_title"]
            if not isinstance(title, str): errors.append(f"{note_id}: social_title must be a string")
            else:
                visible_length = sum(not char.isspace() for char in title)
                if not 8 <= visible_length <= 28: errors.append(f"{note_id}: social_title must contain 8-28 visible characters")
                if any(char in title for char in "\r\n\t"): errors.append(f"{note_id}: social_title must be a single line")
                forbidden = sorted(term for term in FORBIDDEN_SOCIAL_TITLE_TERMS if term in title)
                if forbidden: errors.append(f"{note_id}: social_title contains forbidden promise: {', '.join(forbidden)}")
        expected = set(comments[note_id]); items = post.get("comments", [])
        items = items if isinstance(items, list) else []
        ids = [item.get("comment_id") for item in items]
        for cid in expected | set(ids):
            if cid not in expected or ids.count(cid) != 1: errors.append(f"{note_id}: comment {cid} must be classified exactly once")
        classified = {}
        for item in items:
            cid, category = item.get("comment_id"), item.get("category"); classified[cid] = category
            if category not in CATEGORIES: errors.append(f"{note_id}: invalid category {category}")
            if not str(item.get("claim", "")).strip(): errors.append(f"{note_id}/{cid}: claim is required")
            value = item.get("confidence")
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1: errors.append(f"{note_id}/{cid}: invalid confidence")
            if item.get("evidence_quality") not in EVIDENCE_QUALITIES: errors.append(f"{note_id}/{cid}: invalid evidence_quality {item.get('evidence_quality')}")
            flags = item.get("risk_flags")
            if not isinstance(flags, list) or any(flag not in RISK_FLAGS for flag in flags): errors.append(f"{note_id}/{cid}: invalid risk_flags")
        solution = post.get("solution")
        if not isinstance(solution, dict): errors.append(f"{note_id}: solution is required"); continue
        if not str(solution.get("summary", "")).strip(): errors.append(f"{note_id}: solution summary is required")
        if solution.get("risk_level") not in RISK_LEVELS: errors.append(f"{note_id}: invalid risk_level {solution.get('risk_level')}")
        if solution.get("publish_status") not in PUBLISH_STATUSES: errors.append(f"{note_id}: invalid publish_status {solution.get('publish_status')}")
        claims, claim_map = solution.get("claims", []), {}
        claims = claims if isinstance(claims, list) else []
        for claim in claims:
            claim_id = claim.get("claim_id")
            if not str(claim_id or "").strip() or claim_id in claim_map: errors.append(f"{note_id}: invalid or duplicate claim_id {claim_id}"); continue
            claim_map[claim_id] = set(claim.get("evidence_comment_ids", []))
            if not str(claim.get("claim") or claim.get("text") or "").strip(): errors.append(f"{note_id}/{claim_id}: claim text is required")
            if claim.get("kind") not in CLAIM_KINDS: errors.append(f"{note_id}/{claim_id}: invalid claim kind {claim.get('kind')}")
            if claim.get("status") not in CLAIM_STATUSES: errors.append(f"{note_id}/{claim_id}: invalid claim status {claim.get('status')}")
            sources = claim.get("external_sources", [])
            if not isinstance(sources, list) or any(not _valid_url(url) for url in sources): errors.append(f"{note_id}/{claim_id}: external_sources must contain valid URLs")
            if claim.get("kind") != "external_fact" and not claim_map[claim_id]: errors.append(f"{note_id}/{claim_id}: non-external claim requires comment evidence")
            if _supported_external(claim) and not _claim_urls(claim): errors.append(f"{note_id}/{claim_id}: supported external fact requires URL")
            for cid in claim_map[claim_id]:
                if cid not in expected: errors.append(f"{note_id}/{claim_id}: invalid evidence comment {cid}")
        if not claim_map: errors.append(f"{note_id}: claim ledger is required")
        steps = solution.get("steps", []); steps = steps if isinstance(steps, list) else []
        if not steps: errors.append(f"{note_id}: solution steps are required")
        for index, step in enumerate(steps, 1):
            evidence, claim_ids = step.get("evidence_comment_ids", []), step.get("claim_ids", [])
            if not str(step.get("text", "")).strip(): errors.append(f"{note_id}: step {index} text is required")
            applies_when = step.get("applies_when")
            if not isinstance(applies_when, list) or not applies_when or any(not isinstance(value, str) or not value.strip() for value in applies_when):
                errors.append(f"{note_id}: step {index} applies_when must be a non-empty string list")
            if not str(step.get("verification", "")).strip(): errors.append(f"{note_id}: step {index} verification is required")
            stop_conditions = step.get("stop_conditions")
            if not isinstance(stop_conditions, list) or not stop_conditions or any(not isinstance(value, str) or not value.strip() for value in stop_conditions):
                errors.append(f"{note_id}: step {index} stop_conditions must be a non-empty string list")
            if not evidence: errors.append(f"{note_id}: step {index} has no evidence")
            for cid in evidence:
                if cid not in expected or classified.get(cid) not in STEP_CATEGORIES: errors.append(f"{note_id}: step {index} cites ineligible comment {cid}")
            if not claim_ids or any(claim_id not in claim_map for claim_id in claim_ids): errors.append(f"{note_id}: step {index} has invalid claim_ids")
            covered = set().union(*(claim_map.get(claim_id, set()) for claim_id in claim_ids))
            if not set(evidence) <= covered: errors.append(f"{note_id}: step {index} evidence is not covered by claims")
        conflicts = solution.get("conflicts", []); conflicts = conflicts if isinstance(conflicts, list) else []
        for index, conflict in enumerate(conflicts, 1):
            positions = conflict.get("positions", []) if isinstance(conflict, dict) else []
            if len(positions) < 2: errors.append(f"{note_id}: conflict {index} requires at least two positions")
            for position in positions:
                evidence_ids = position.get("evidence_comment_ids", [])
                if not str(position.get("claim", "")).strip() or not evidence_ids: errors.append(f"{note_id}: conflict {index} position requires claim and evidence")
                for cid in evidence_ids:
                    if cid not in expected: errors.append(f"{note_id}: conflict {index} cites invalid comment {cid}")
        external_claims = [claim for claim in claims if claim.get("kind") == "external_fact"]
        all_external_verified = bool(external_claims) and all(_supported_external(claim) and _claim_urls(claim) for claim in external_claims)
        if solution.get("risk_level") == "high" and solution.get("publish_status") == "ready" and not all_external_verified:
            errors.append(f"{note_id}: high-risk ready result requires every external fact to be supported with URL")
        unsafe_ids = {item.get("comment_id") for item in items if isinstance(item.get("risk_flags"), list) and "unsafe_advice" in item["risk_flags"]}
        unsafe_used = any(unsafe_ids.intersection(step.get("evidence_comment_ids", [])) for step in steps)
        if unsafe_used and solution.get("risk_level") != "high":
            errors.append(f"{note_id}: steps using unsafe advice require high risk_level")
        if unsafe_used and solution.get("publish_status") == "ready" and not all_external_verified:
            errors.append(f"{note_id}: steps using unsafe advice require needs_review until every external fact is verified")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("canonical", type=Path); parser.add_argument("analysis", type=Path)
    args = parser.parse_args()
    errors = validate(load_jsonl(args.canonical), json.loads(args.analysis.read_text(encoding="utf-8-sig")))
    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors)); raise SystemExit(1)
    print("analysis evidence is valid")


if __name__ == "__main__": main()
