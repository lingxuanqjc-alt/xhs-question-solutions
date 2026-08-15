#!/usr/bin/env python3
"""Render validated card-deck IR as self-contained HTML and optional PNG files."""
import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from render_result import build_card_decks
from validate_result import load_jsonl

SCHEMA = "xhs-card-deck/v1"
THEMES = ("morandi", "academic", "dark", "mint", "sunset", "bw")
ROLE_LABELS = {
    "cover": "评论证据解法",
    "scope": "样本范围",
    "action": "可执行动作",
    "experience": "亲历样本",
    "counterexample": "失败反例",
    "conflicts_risks": "分歧与风险",
    "unknowns": "还缺什么",
    "disclosure": "披露与互动",
}
BLOCK_TYPES = {"paragraph", "field", "bullet", "notice"}
APPENDIX_PAGE_SIZE = 3


def _escape(value):
    return html.escape(str(value or ""), quote=True)


def _safe_name(value):
    raw = str(value or "deck")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")[:32] or "deck"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def _validate_ir(ir):
    if not isinstance(ir, dict) or ir.get("schema") != SCHEMA:
        raise ValueError(f"unsupported card-deck schema; expected {SCHEMA}")
    decks = ir.get("decks")
    if not isinstance(decks, list) or not decks:
        raise ValueError("card-deck IR must contain at least one deck")
    for deck in decks:
        cards = deck.get("cards") if isinstance(deck, dict) else None
        if not isinstance(cards, list) or not cards:
            raise ValueError("each card deck must contain cards")
        expected = list(range(1, len(cards) + 1))
        indexes = [card.get("index") for card in cards if isinstance(card, dict)]
        if indexes != expected:
            raise ValueError(f"deck {deck.get('deck_id')} card indexes must be continuous")
        for card in cards:
            if card.get("role") not in ROLE_LABELS:
                raise ValueError(f"card {card.get('card_id')} has unsupported role {card.get('role')}")
            if not isinstance(card.get("blocks"), list):
                raise ValueError(f"card {card.get('card_id')} blocks must be a list")
            for block in card["blocks"]:
                if not isinstance(block, dict) or block.get("type") not in BLOCK_TYPES:
                    raise ValueError(f"card {card.get('card_id')} has unsupported block type")
        appendix = deck.get("appendix")
        evidence = appendix.get("evidence") if isinstance(appendix, dict) else None
        if not isinstance(evidence, list):
            raise ValueError(f"deck {deck.get('deck_id')} appendix.evidence must be a list")
        for item in evidence:
            if not isinstance(item, dict):
                raise ValueError(f"deck {deck.get('deck_id')} appendix evidence must be objects")
            for field in ("comment_id", "category", "category_label", "author", "thread_id", "excerpt"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    raise ValueError(f"deck {deck.get('deck_id')} appendix evidence {field} must be a non-empty string")
            likes = item.get("likes")
            if likes is not None and (not isinstance(likes, int) or isinstance(likes, bool) or likes < 0):
                raise ValueError(f"deck {deck.get('deck_id')} appendix evidence likes must be a non-negative integer or null")
            for field in ("likes_label", "safety_warning"):
                if field in item and (not isinstance(item[field], str) or not item[field].strip()):
                    raise ValueError(f"deck {deck.get('deck_id')} appendix evidence {field} must be a non-empty string")
    return ir


def _block_html(block):
    kind = block["type"]
    if kind == "paragraph":
        return f'<div class="block block-paragraph">{_escape(block.get("text"))}</div>'
    if kind == "field":
        return (
            '<div class="block block-field">'
            f'<span class="field-label">{_escape(block.get("label"))}</span>'
            f'<div class="field-text">{_escape(block.get("value"))}</div></div>'
        )
    if kind == "bullet":
        return f'<div class="block block-bullet">{_escape(block.get("text"))}</div>'
    tone = block.get("tone", "caution")
    if tone not in {"warning", "caution", "cta"}:
        raise ValueError(f"unsupported notice tone: {tone}")
    return f'<div class="block block-notice tone-{tone}">{_escape(block.get("text"))}</div>'


def _card_html(card, total, theme, evidence_labels):
    role = card["role"]
    evidence = [str(item) for item in card.get("evidence_comment_ids", [])]
    evidence_items = [f"{item}/{evidence_labels[item]}" if evidence_labels.get(item) else item for item in evidence]
    evidence_text = "证据 · " + " · ".join(evidence_items) if evidence else "边界与完整证据见附录"
    swipe = "核对附录" if role == "disclosure" else "继续滑动 →"
    visible_blocks = [
        block for block in card["blocks"]
        if not (role == "action" and block.get("type") == "field" and block.get("label") == "证据")
    ]
    blocks = "".join(_block_html(block) for block in visible_blocks)
    return (
        f'<article class="card theme-{theme} role-{_escape(role)}" '
        f'data-card-id="{_escape(card["card_id"])}" data-role="{_escape(role)}">'
        '<header class="card-header">'
        f'<span class="eyebrow">{_escape(ROLE_LABELS[role])}</span>'
        f'<span class="page-number">{card["index"]:02d} / {total:02d}</span></header>'
        '<section class="card-body" data-fit>'
        f'<h1 class="card-title">{_escape(card.get("title"))}</h1><div class="title-mark"></div>'
        f'<div class="blocks">{blocks}</div></section>'
        '<footer class="card-footer">'
        f'<span class="evidence-label">{_escape(evidence_text)}</span>'
        f'<span class="swipe">{_escape(swipe)}</span></footer></article>'
    )


def _appendix_cards_html(deck, theme):
    evidence = deck.get("appendix", {}).get("evidence", [])
    pages = [evidence[index:index + APPENDIX_PAGE_SIZE] for index in range(0, len(evidence), APPENDIX_PAGE_SIZE)]
    cards = []
    for page_index, items in enumerate(pages, 1):
        entries = []
        for item in items:
            likes_label = item.get("likes_label")
            if not isinstance(likes_label, str) or not likes_label.strip():
                likes = item.get("likes")
                likes_label = f"赞 {likes}" if isinstance(likes, int) and not isinstance(likes, bool) and likes >= 0 else "赞数未知"
            warning = item.get("safety_warning")
            warning_html = f'<div class="evidence-warning">{_escape(warning)}</div>' if warning else ""
            entries.append(
                '<div class="evidence-entry">'
                '<div class="evidence-head">'
                f'<span>#{_escape(item.get("comment_id"))} · {_escape(item.get("category_label"))}</span>'
                f'<span class="evidence-meta">{_escape(likes_label)} · thread {_escape(item.get("thread_id"))}</span>'
                '</div>'
                f'{warning_html}<div class="evidence-quote">“{_escape(item.get("excerpt"))}”</div></div>'
            )
        card_id = f"{deck.get('note_id')}:evidence:{page_index:02d}"
        cards.append(
            f'<article class="card theme-{theme} role-evidence_appendix" '
            f'data-card-id="{_escape(card_id)}" data-role="evidence_appendix">'
            '<header class="card-header"><span class="eyebrow">证据附录</span>'
            f'<span class="page-number">附 {page_index:02d} / {len(pages):02d}</span></header>'
            '<section class="card-body" data-fit><h1 class="card-title">原评论短摘</h1>'
            '<div class="title-mark"></div>'
            f'<div class="evidence-list">{"".join(entries)}</div></section>'
            '<footer class="card-footer"><span class="evidence-label">匿名化短摘 · 点赞只表示关注</span>'
            f'<span class="swipe">附录 {page_index:02d}/{len(pages):02d}</span></footer></article>'
        )
    return cards


def render_deck_html(deck, css, theme="morandi"):
    if theme not in THEMES:
        raise ValueError(f"unsupported theme: {theme}")
    evidence_labels = {
        item["comment_id"]: item.get("category_label", "")
        for item in deck.get("appendix", {}).get("evidence", [])
    }
    main_cards = [
        _card_html(card, len(deck["cards"]), theme, evidence_labels) for card in deck["cards"]
    ]
    cards = "\n".join(main_cards + _appendix_cards_html(deck, theme))
    metadata = json.dumps(deck, ensure_ascii=False, sort_keys=True).replace("<", "\\u003c")
    title = deck["cards"][0].get("title") or deck.get("deck_id") or "小红书卡片"
    return (
        "<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_escape(title)}</title><style>{css}</style></head><body>"
        f'<main class="deck">{cards}</main>'
        f'<script type="application/json" id="deck-manifest">{metadata}</script>'
        "</body></html>\n"
    )


def write_decks(canonical, analysis, output_dir, theme="morandi"):
    ir = _validate_ir(build_card_decks(canonical, analysis))
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    css_path = Path(__file__).resolve().parents[1] / "assets" / "card-deck.css"
    css = css_path.read_text(encoding="utf-8")
    (output_dir / "card-decks.json").write_text(
        json.dumps(ir, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    written = []
    for deck in ir["decks"]:
        name = _safe_name(deck.get("note_id") or deck.get("deck_id"))
        html_path = output_dir / f"{name}.html"
        html_path.write_text(render_deck_html(deck, css, theme), encoding="utf-8")
        written.append((deck, html_path, output_dir / name))
    return ir, written


def _replace_output_directory(staging, target):
    staging, target = Path(staging), Path(target)
    if target.exists() and not target.is_dir():
        raise RuntimeError(f"PNG output target is not a directory: {target}")
    backup = target.with_name(f".{target.name}.backup-{uuid.uuid4().hex}")
    moved_old = False
    try:
        if target.exists():
            target.rename(backup)
            moved_old = True
        staging.rename(target)
    except Exception as error:
        if moved_old and backup.exists() and not target.exists():
            try:
                backup.rename(target)
            except Exception as restore_error:
                raise RuntimeError(f"PNG output replacement failed and rollback failed: {restore_error}") from error
        raise
    if moved_old:
        shutil.rmtree(backup)


def capture_pngs(written, node=None, browser=None):
    node_path = str(node) if node else shutil.which("node")
    if not node_path:
        raise RuntimeError("PNG rendering needs Node.js; HTML decks were generated successfully")
    capture = Path(__file__).with_name("capture_cards.cjs")
    summaries = []
    for deck, html_path, png_dir in written:
        png_dir = Path(png_dir)
        png_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{png_dir.name}.rendering-", dir=png_dir.parent))
        try:
            command = [node_path, str(capture), str(html_path), str(staging)]
            if browser:
                command.append(str(browser))
            result = subprocess.run(command, check=False, text=True, encoding="utf-8", capture_output=True)
            if result.returncode:
                detail = (result.stderr or result.stdout).strip()
                raise RuntimeError(f"PNG rendering failed for {deck['deck_id']}: {detail}; previous PNG set is unchanged and HTML remains at {html_path}")
            summary = json.loads(result.stdout)
            summary["output"] = png_dir.name
            _replace_output_directory(staging, png_dir)
            summaries.append(summary)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return summaries


def main():
    parser = argparse.ArgumentParser(description="Render xhs-card-deck/v1 as self-contained HTML and optional PNG files")
    parser.add_argument("canonical", type=Path)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--style", choices=THEMES, default="morandi")
    parser.add_argument("--png", action="store_true", help="also capture 1080x1440 PNG files with Node.js Playwright")
    parser.add_argument("--node", type=Path, help="Node.js executable used by --png")
    parser.add_argument("--browser", type=Path, help="Chromium, Edge, or Chrome executable used by --png")
    args = parser.parse_args()
    canonical = load_jsonl(args.canonical)
    analysis = json.loads(args.analysis.read_text(encoding="utf-8-sig"))
    ir, written = write_decks(canonical, analysis, args.output_dir, args.style)
    print(f"rendered {len(ir['decks'])} HTML card deck(s): {args.output_dir}")
    if args.png:
        summaries = capture_pngs(written, args.node, args.browser)
        main_count = sum(sum(card["role"] != "evidence_appendix" for card in item["cards"]) for item in summaries)
        appendix_count = sum(sum(card["role"] == "evidence_appendix" for card in item["cards"]) for item in summaries)
        (args.output_dir / "png-render-summary.json").write_text(
            json.dumps({"backend": "playwright", "style": args.style, "decks": summaries}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"rendered {main_count} main PNG card(s) + {appendix_count} evidence appendix card(s) at 1080x1440")


if __name__ == "__main__":
    main()
