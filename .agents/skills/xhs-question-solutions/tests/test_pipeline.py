import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from normalize_xhs_export import normalize
from validate_result import validate


class PipelineTests(unittest.TestCase):
    def test_normalization_preserves_reply_evidence_once(self):
        """Replies remain traceable without double-counting duplicated comments."""
        payload = {"notes": [{"id": "n1", "comments": [
            {"id": "c1", "content": "先重启", "replies": [{"id": "c2", "content": "亲测有效"}]},
            {"id": "c1", "content": "先重启"}]}]}
        comments = [r for r in normalize(payload) if r["kind"] == "comment"]
        self.assertEqual(["c1", "c2"], [r["comment_id"] for r in comments])
        self.assertEqual("c1", comments[1]["parent_id"])

    def test_validator_rejects_unsupported_solution(self):
        """A polished solution is unsafe when its evidence is absent."""
        canonical = normalize({"id": "n1", "comments": [{"id": "c1", "content": "亲测有效"}]})
        analysis = {"posts": [{"note_id": "n1", "is_question": True, "question": "怎么做", "solution": {"steps": [
            {"text": "执行", "evidence_comment_ids": ["missing"]}]}}]}
        self.assertTrue(validate(canonical, analysis))

    def test_normalization_rejects_cross_note_evidence_collision(self):
        """The same evidence ID cannot silently refer to two different notes."""
        payload = {"notes": [{"id": "n1", "comments": [{"id": "c1"}]},
                             {"id": "n2", "comments": [{"id": "c1"}]}]}
        with self.assertRaises(ValueError):
            normalize(payload)

    def test_validator_accepts_traceable_solution(self):
        canonical = normalize({"id": "n1", "comments": [{"id": "c1", "content": "亲测有效"}]})
        analysis = {"posts": [{"note_id": "n1", "is_question": True, "question": "怎么做", "comments": [
            {"comment_id": "c1", "category": "firsthand_experience"}], "solution": {"steps": [
            {"text": "执行", "evidence_comment_ids": ["c1"]}]}}]}
        self.assertEqual([], validate(canonical, analysis))


if __name__ == "__main__": unittest.main()
