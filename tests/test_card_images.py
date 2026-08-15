import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents/skills/xhs-question-solutions/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from render_card_images import APPENDIX_PAGE_SIZE, SCHEMA, _safe_name, _validate_ir, capture_pngs, render_deck_html, write_decks
from render_result import build_card_decks
from test_pipeline_v2 import canonical, good


class CardImageTests(unittest.TestCase):
    def test_html_is_self_contained_and_has_one_canvas_per_ir_card(self):
        with tempfile.TemporaryDirectory() as directory:
            ir, written = write_decks(canonical(), good(), directory)
            self.assertEqual(SCHEMA, ir["schema"])
            self.assertEqual(1, len(written))
            page = written[0][1].read_text(encoding="utf-8")
            evidence_count = len(ir["decks"][0]["appendix"]["evidence"])
            appendix_pages = (evidence_count + APPENDIX_PAGE_SIZE - 1) // APPENDIX_PAGE_SIZE
            self.assertEqual(len(ir["decks"][0]["cards"]) + appendix_pages, page.count('class="card theme-'))
            self.assertIn("width: 1080px", page)
            self.assertIn("height: 1440px", page)
            self.assertNotIn("https://", page)
            self.assertTrue((Path(directory) / "card-decks.json").is_file())

    def test_evidence_appendix_is_rendered_as_readable_separate_pages(self):
        deck = build_card_decks(canonical(), good())["decks"][0]
        page = render_deck_html(deck, ".card{}")
        self.assertIn('data-role="evidence_appendix"', page)
        self.assertIn("附 01 / 01", page)
        self.assertIn("#c1 · 直接答案", page)
        self.assertIn("#c2 · 亲历经验", page)
        self.assertEqual(1, page.count('data-role="evidence_appendix"'))

    def test_detached_appendix_keeps_safety_warning_and_explicit_unknown_likes(self):
        deck = build_card_decks(canonical(), good())["decks"][0]
        evidence = deck["appendix"]["evidence"][0]
        evidence["category_label"] = "直接答案"
        evidence["safety_warning"] = "未核验高风险观点，不是操作建议"
        evidence["likes"] = None
        evidence["likes_label"] = "赞数未知"
        page = render_deck_html(deck, ".card{}")
        self.assertIn("未核验高风险观点，不是操作建议", page)
        self.assertIn("赞数未知 · thread", page)
        self.assertNotIn("赞 None", page)

    def test_action_evidence_is_visible_without_a_duplicate_field(self):
        deck = build_card_decks(canonical(), good())["decks"][0]
        page = render_deck_html(deck, ".card{}")
        self.assertIn("证据 · c1", page)
        self.assertNotIn('<span class="field-label">证据</span>', page)

    def test_model_text_is_escaped_before_entering_html(self):
        analysis = good()
        analysis["posts"][0]["solution"]["summary"] = '<img src=x onerror="alert(1)">'
        deck = build_card_decks(canonical(), analysis)["decks"][0]
        page = render_deck_html(deck, ".card{}")
        self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", page)
        self.assertNotIn('<img src=x onerror="alert(1)">', page)

    def test_unknown_schema_and_block_type_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "unsupported card-deck schema"):
            _validate_ir({"schema": "future/v9", "decks": []})
        ir = build_card_decks(canonical(), good())
        ir["decks"][0]["cards"][0]["blocks"].append({"type": "raw_html", "text": "<b>x</b>"})
        with self.assertRaisesRegex(ValueError, "unsupported block type"):
            _validate_ir(ir)

    def test_malformed_appendix_contract_fails_before_html_rendering(self):
        ir = build_card_decks(canonical(), good())
        ir["decks"][0]["appendix"]["evidence"][0]["safety_warning"] = ["not text"]
        with self.assertRaisesRegex(ValueError, "safety_warning must be a non-empty string"):
            _validate_ir(ir)

    def test_output_names_are_safe_and_deterministic(self):
        first = _safe_name("../危险/帖子:1")
        self.assertEqual(first, _safe_name("../危险/帖子:1"))
        self.assertNotIn("..", first)
        self.assertNotIn("/", first)
        self.assertNotIn("\\", first)

    def test_ir_sidecar_is_deterministic_json(self):
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            write_decks(canonical(), good(), left)
            write_decks(canonical(), good(), right)
            a = (Path(left) / "card-decks.json").read_bytes()
            b = (Path(right) / "card-decks.json").read_bytes()
            self.assertEqual(a, b)
            self.assertEqual(SCHEMA, json.loads(a)["schema"])

    def test_successful_png_capture_replaces_the_whole_previous_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "cards"
            target.mkdir()
            (target / "old-01.png").write_bytes(b"old")
            html_path = root / "deck.html"
            html_path.write_text("<html></html>", encoding="utf-8")

            def succeed(command, **_kwargs):
                staging = Path(command[3])
                (staging / "01-cover.png").write_bytes(b"new")
                payload = {"input": "deck.html", "output": staging.name, "cards": []}
                return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

            with patch("render_card_images.subprocess.run", side_effect=succeed):
                result = capture_pngs([({"deck_id": "note:n1"}, html_path, target)], node="node")
            self.assertEqual(["01-cover.png"], [item.name for item in target.iterdir()])
            self.assertEqual("cards", result[0]["output"])

    def test_failed_png_capture_preserves_the_previous_complete_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "cards"
            target.mkdir()
            (target / "old-01.png").write_bytes(b"old")
            html_path = root / "deck.html"
            html_path.write_text("<html></html>", encoding="utf-8")

            def fail_after_partial_write(command, **_kwargs):
                (Path(command[3]) / "01-partial.png").write_bytes(b"partial")
                return SimpleNamespace(returncode=6, stdout="", stderr="card overflow")

            with patch("render_card_images.subprocess.run", side_effect=fail_after_partial_write):
                with self.assertRaisesRegex(RuntimeError, "previous PNG set is unchanged"):
                    capture_pngs([({"deck_id": "note:n1"}, html_path, target)], node="node")
            self.assertEqual(["old-01.png"], [item.name for item in target.iterdir()])
            self.assertEqual([], list(root.glob(".cards.rendering-*")))


if __name__ == "__main__":
    unittest.main()
