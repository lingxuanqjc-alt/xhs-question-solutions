#!/usr/bin/env python3
"""Reject analyses that cite missing or cross-note comment evidence."""
import argparse, json
from pathlib import Path

CATEGORIES = {"direct_answer", "firsthand_experience", "risk_warning", "counterexample", "clarifying_question", "speculation", "off_topic"}


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def validate(canonical, analysis):
    notes = {r["note_id"] for r in canonical if r.get("kind") == "note"}
    comments = {r["comment_id"]: r["note_id"] for r in canonical if r.get("kind") == "comment"}
    errors = []
    for post in analysis.get("posts", []):
        note_id = post.get("note_id")
        if note_id not in notes:
            errors.append(f"unknown note_id: {note_id}"); continue
        if post.get("is_question") and not str(post.get("question", "")).strip():
            errors.append(f"{note_id}: question text is required")
        for item in post.get("comments", []):
            cid = item.get("comment_id")
            if comments.get(cid) != note_id: errors.append(f"{note_id}: invalid comment reference {cid}")
            if item.get("category") not in CATEGORIES: errors.append(f"{note_id}: invalid category {item.get('category')}")
        solution = post.get("solution", {})
        groups = [step.get("evidence_comment_ids", []) for step in solution.get("steps", [])]
        for conflict in solution.get("conflicts", []):
            groups.extend(p.get("evidence_comment_ids", []) for p in conflict.get("positions", []))
        for ids in groups:
            if not ids: errors.append(f"{note_id}: solution claim has no evidence")
            for cid in ids:
                if comments.get(cid) != note_id: errors.append(f"{note_id}: solution cites invalid comment {cid}")
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
