#!/usr/bin/env python3
"""Normalize JSON/JSONL XHS exports without semantic judgments."""
import argparse, hashlib, json, re
from collections import deque
from pathlib import Path


def first(obj, *keys, default=""):
    return next((obj[k] for k in keys if obj.get(k) is not None), default)


def author(value):
    return str(first(value, "user_id", "userId", "id", "nickname", "name", "user_name") if isinstance(value, dict) else value or "")


def anonymous_author(note_id, value):
    identity = author(value).strip()
    return "匿名用户" if not identity else "用户-" + hashlib.sha256((note_id + "\x1f" + identity).encode("utf-8")).hexdigest()[:8]


def number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool): return int(value)
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*([万千wk]?)", str(value or "0").strip().lower().replace(",", ""))
    return 0 if not match else int(float(match.group(1)) * {"": 1, "千": 1000, "k": 1000, "万": 10000, "w": 10000}[match.group(2)])


def boolean(value):
    if isinstance(value, bool): return value
    if isinstance(value, (int, float)): return value != 0
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y"}: return True
    if text in {"false", "0", "no", "n", ""}: return False
    return False


def normalize_comment(raw, note_id, parent_id=None, thread_id=None):
    content = str(first(raw, "content", "text", "desc")).strip()
    raw_author = first(raw, "author", "user", "nickname")
    created = first(raw, "created_at", "create_time", "time")
    cid = str(first(raw, "comment_id", "commentId", "id")).strip()
    if not cid:
        seed = "\x1f".join(map(str, (note_id, parent_id, author(raw_author), content, created)))
        cid = "generated:" + hashlib.sha256(seed.encode()).hexdigest()[:16]
    parent_id = first(raw, "parent_id", "parentId", "parent_comment_id", "parentCommentId", default=parent_id) or None
    thread_id = str(first(raw, "thread_id", "threadId", "root_comment_id", "rootCommentId", default=thread_id or cid))
    item = {"kind": "comment", "comment_id": cid, "note_id": note_id,
            "parent_id": str(parent_id) if parent_id else None, "thread_id": thread_id,
            "author": anonymous_author(note_id, raw_author), "content": content,
            "likes": number(first(raw, "likes", "like_count", "likeCount", default=0)),
            "created_at": created}
    replies = first(raw, "replies", "reply_list", "sub_comments", "subComments", default=[])
    return item, replies if isinstance(replies, list) else []


def normalize_note(raw, defaults=None):
    defaults = defaults or {}
    note_id = str(first(raw, "note_id", "noteId", "id")).strip()
    if not note_id:
        raise ValueError("note is missing note_id/id")
    comments = first(raw, "comments", "comment_list", "commentList", default=[])
    comments = comments if isinstance(comments, list) else []
    cap = first(raw, "capture", default={}); cap = cap if isinstance(cap, dict) else {}
    total = number(first(raw, "comments_total", "comments_count", "comment_count", "commentCount", default=first(cap, "comments_total", default=0)))
    note = {"kind": "note", "note_id": note_id,
            "url": str(first(raw, "url", "link")), "title": str(first(raw, "title")).strip(),
            "content": str(first(raw, "content", "desc", "text")).strip(),
            "author": anonymous_author(note_id, first(raw, "author", "user")),
            "likes": number(first(raw, "likes", "like_count", "likeCount", default=0)),
            "comments_count": total,
            "capture": {"source": str(first(cap, "source", default=first(raw, "source", default=defaults.get("source", "unknown")))),
                        "captured_at": first(cap, "captured_at", default=first(raw, "captured_at", default=defaults.get("captured_at", ""))),
                        "comments_total": total, "comments_collected": 0,
                        "is_truncated": boolean(first(cap, "is_truncated", default=first(raw, "is_truncated", default=False))),
                        "failure_reason": str(first(cap, "failure_reason", default=first(raw, "failure_reason", default="")))}}
    return note, comments


def load(path):
    text = path.read_text(encoding="utf-8-sig")
    return ([json.loads(line) for line in text.splitlines() if line.strip()]
            if path.suffix.lower() == ".jsonl" else json.loads(text))


def _records(payload):
    if isinstance(payload, list): return payload, {}
    if not isinstance(payload, dict): return [payload], {}
    defaults = {k: payload[k] for k in ("source", "captured_at") if payload.get(k) is not None}
    for key in ("notes", "items", "note_list", "noteList", "list"):
        if isinstance(payload.get(key), list):
            loose = []
            for extra in ("comments", "replies"):
                if isinstance(payload.get(extra), list): loose.extend(payload[extra])
            return payload[key] + loose, defaults
    for key in ("data", "result"):
        if isinstance(payload.get(key), (dict, list)):
            rows, nested = _records(payload[key]); defaults.update(nested); return rows, defaults
    return [payload], defaults


def normalize(payload):
    records, defaults = _records(payload)
    records = records if isinstance(records, list) else [records]
    if not all(isinstance(row, dict) for row in records):
        raise ValueError("input must contain JSON objects")
    output, notes, seen, fingerprints, pending = [], {}, {}, {}, []
    loose_comments = [r for r in records if r.get("kind") in {"comment", "reply"} or any(k in r for k in ("comment_id", "commentId", "parent_id", "parentId", "parent_comment_id", "parentCommentId"))]
    for raw in (r for r in records if r not in loose_comments):
        note, comments = normalize_note(raw, defaults)
        if note["note_id"] in notes: raise ValueError(f"duplicate note_id {note['note_id']}")
        notes[note["note_id"]] = note; output.append(note)
        pending.extend((item, note["note_id"], None, None) for item in comments)
    pending.extend((raw, str(first(raw, "note_id", "noteId")), None, None) for raw in loose_comments)
    queue = deque(pending)
    while queue:
        raw_comment, note_id, parent_id, thread_id = queue.popleft()
        if not isinstance(raw_comment, dict): raise ValueError("comments and replies must be JSON objects")
        if note_id not in notes: raise ValueError(f"comment references unknown note_id {note_id}")
        item, replies = normalize_comment(raw_comment, note_id, parent_id, thread_id)
        fp = (note_id, item["parent_id"], item["author"], item["content"])
        if item["comment_id"] in fingerprints and fingerprints[item["comment_id"]] != fp:
            raise ValueError(f"conflicting content for comment_id {item['comment_id']}")
        if item["comment_id"] not in seen:
            output.append(item); seen[item["comment_id"]] = item; fingerprints[item["comment_id"]] = fp
        queue.extend((reply, note_id, item["comment_id"], item["thread_id"]) for reply in replies)
    for item in seen.values():
        parent = item["parent_id"]
        if parent and (parent not in seen or seen[parent]["note_id"] != item["note_id"] or parent == item["comment_id"]):
            raise ValueError(f"comment {item['comment_id']} has invalid parent_id {parent}")
        ancestors, cursor = set(), item
        while cursor.get("parent_id"):
            if cursor["parent_id"] in ancestors: raise ValueError(f"comment {item['comment_id']} has cyclic parent_id")
            ancestors.add(cursor["parent_id"]); cursor = seen[cursor["parent_id"]]
        item["thread_id"] = cursor["comment_id"]
    for note_id, note in notes.items():
        collected = sum(item["note_id"] == note_id for item in seen.values())
        note["capture"]["comments_collected"] = collected
        note["capture"]["is_truncated"] = note["capture"]["is_truncated"] or note["capture"]["comments_total"] > collected
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
