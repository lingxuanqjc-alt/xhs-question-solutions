#!/usr/bin/env python3
"""Normalize JSON/JSONL XHS exports without semantic judgments."""
import argparse, hashlib, json
from pathlib import Path


def first(obj, *keys, default=""):
    return next((obj[k] for k in keys if obj.get(k) is not None), default)


def author(value):
    return str(first(value, "nickname", "name", "user_name") if isinstance(value, dict) else value or "")


def number(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_comment(raw, note_id, parent_id=None):
    content = str(first(raw, "content", "text", "desc")).strip()
    who = author(first(raw, "author", "user", "nickname"))
    created = first(raw, "created_at", "create_time", "time")
    cid = str(first(raw, "comment_id", "commentId", "id")).strip()
    if not cid:
        seed = "\x1f".join(map(str, (note_id, parent_id, who, content, created)))
        cid = "generated:" + hashlib.sha256(seed.encode()).hexdigest()[:16]
    item = {"kind": "comment", "comment_id": cid, "note_id": note_id,
            "parent_id": parent_id, "author": who, "content": content,
            "likes": number(first(raw, "likes", "like_count", "likeCount", default=0)),
            "created_at": created}
    replies = first(raw, "replies", "sub_comments", "subComments", default=[])
    return item, replies if isinstance(replies, list) else []


def normalize_note(raw):
    note_id = str(first(raw, "note_id", "noteId", "id")).strip()
    if not note_id:
        raise ValueError("note is missing note_id/id")
    comments = first(raw, "comments", "comment_list", "commentList", default=[])
    comments = comments if isinstance(comments, list) else []
    note = {"kind": "note", "note_id": note_id,
            "url": str(first(raw, "url", "link")), "title": str(first(raw, "title")).strip(),
            "content": str(first(raw, "content", "desc", "text")).strip(),
            "author": author(first(raw, "author", "user")),
            "likes": number(first(raw, "likes", "like_count", "likeCount", default=0)),
            "comments_count": number(first(raw, "comments_count", "comment_count", "commentCount", default=len(comments)))}
    return note, comments


def load(path):
    text = path.read_text(encoding="utf-8-sig")
    return ([json.loads(line) for line in text.splitlines() if line.strip()]
            if path.suffix.lower() == ".jsonl" else json.loads(text))


def normalize(payload):
    records = payload.get("notes") if isinstance(payload, dict) and isinstance(payload.get("notes"), list) else payload
    records = records if isinstance(records, list) else [records]
    if not all(isinstance(row, dict) for row in records):
        raise ValueError("input must contain JSON objects")
    output, seen = [], {}
    loose_comments = [r for r in records if r.get("kind") == "comment"]
    for raw in (r for r in records if r.get("kind") != "comment"):
        note, comments = normalize_note(raw)
        output.append(note)
        queue = [(item, None) for item in comments]
        while queue:
            raw_comment, parent_id = queue.pop(0)
            item, replies = normalize_comment(raw_comment, note["note_id"], parent_id)
            previous_note = seen.get(item["comment_id"])
            if previous_note and previous_note != item["note_id"]:
                raise ValueError(f"comment_id {item['comment_id']} belongs to multiple notes")
            if not previous_note:
                output.append(item); seen[item["comment_id"]] = item["note_id"]
            queue.extend((reply, item["comment_id"]) for reply in replies)
    for raw in loose_comments:
        item, _ = normalize_comment(raw, str(raw.get("note_id", "")), raw.get("parent_id"))
        if not item["note_id"]:
            raise ValueError("comment is missing note_id")
        previous_note = seen.get(item["comment_id"])
        if previous_note and previous_note != item["note_id"]:
            raise ValueError(f"comment_id {item['comment_id']} belongs to multiple notes")
        if not previous_note:
            output.append(item); seen[item["comment_id"]] = item["note_id"]
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path); parser.add_argument("output", type=Path)
    args = parser.parse_args()
    records = normalize(load(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    print(f"normalized {len(records)} records")


if __name__ == "__main__": main()
