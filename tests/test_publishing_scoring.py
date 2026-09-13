import importlib.util
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "publishing_scoring",
    Path(__file__).resolve().parents[1] / "scripts" / "publishing_scoring.py",
)
scoring = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scoring)


def candidate(candidate_id="t1", text="越努力迎合别人，越容易失去自己的判断"):
    return {
        "id": candidate_id,
        "text": text,
        "evidence_status": "supported",
        "supporting_evidence": [
            {"candidate_id": "c01", "timestamp": 12.4, "quote": "source quote"}
        ],
        "scores": {
            dimension: {"score": 4.0, "reason": "publishing editor assessment"}
            for dimension in scoring.POSITIVE_DIMENSIONS
        },
    }


class PublishingScoringTest(unittest.TestCase):
    def test_cold_profile_is_independent_and_selected(self):
        profile = scoring.load_profile(platform="douyin", account_stage="cold")
        self.assertEqual(profile["platform"], "douyin")
        self.assertEqual(profile["account_stage"], "cold")
        self.assertEqual(profile["configuration_version"], "phase5a-1")
        self.assertAlmostEqual(sum(profile["weights"].values()), 1.0)
        self.assertNotIn("visual_viability", profile["weights"])
        result = scoring.rank_candidates([candidate()], "title", profile)
        self.assertEqual(result["account_stage"], "cold")
        self.assertEqual(result["selected"]["id"], "t1")

    def test_exaggerated_title_is_penalized_and_rejected(self):
        normal = scoring.score_candidate(candidate(), "title")
        exaggerated = scoring.score_candidate(
            candidate("t2", "所有人都必须知道的唯一真相"), "title"
        )
        self.assertGreater(exaggerated["penalties"]["exaggeration"]["score"], 0)
        self.assertLess(exaggerated["final_score"], normal["final_score"])
        self.assertTrue(exaggerated["rejected"])
        self.assertIn(
            "exaggeration exceeds cold-start publishing threshold",
            exaggerated["rejection_reasons"],
        )

    def test_cliche_title_is_penalized(self):
        plain = scoring.score_candidate(candidate(), "title")
        cliche = scoring.score_candidate(candidate("t2", "看完沉默了，这也太真实了"), "title")
        self.assertGreater(cliche["penalties"]["cliche"]["score"], 0)
        self.assertGreater(cliche["penalty_total"], plain["penalty_total"])
        self.assertLess(cliche["final_score"], plain["final_score"])

    def test_duplicate_hook_or_title_gets_diversity_penalty(self):
        first = candidate("h1", "努力不一定带来自由")
        duplicate = candidate("h2", "努力不一定带来自由")
        distinct = candidate("h3", "真正困难的是停止迎合")
        result = scoring.rank_candidates([first, duplicate, distinct], "hook")
        by_id = {item["id"]: item for item in result["ranked"]}
        duplicate_scores = sorted(
            [by_id["h1"]["penalties"]["diversity"]["score"], by_id["h2"]["penalties"]["diversity"]["score"]]
        )
        self.assertEqual(duplicate_scores, [0.0, 1.0])
        self.assertGreater(by_id["h3"]["final_score"], min(by_id["h1"]["final_score"], by_id["h2"]["final_score"]))

    def test_cross_type_comparison_penalizes_title_that_copies_hook(self):
        result = scoring.score_candidate(
            candidate("t1", "努力不一定带来自由"),
            "title",
            comparison_texts=["努力不一定带来自由"],
        )
        self.assertEqual(result["penalties"]["diversity"]["score"], 1.0)

    def test_unsupported_claim_is_rejected_even_with_high_scores(self):
        unsupported = candidate("t2", "一个方法让所有人收入翻倍")
        unsupported["evidence_status"] = "unsupported"
        unsupported["supporting_evidence"] = []
        result = scoring.score_candidate(unsupported, "title")
        self.assertTrue(result["rejected"])
        self.assertEqual(result["scores"]["evidence_support"]["score"], 0.0)
        self.assertEqual(result["scores"]["evidence_support"]["source"], "heuristic")
        self.assertIn("unsupported substantive claim", result["rejection_reasons"])

    def test_missing_scores_use_auditable_heuristics(self):
        item = candidate()
        item.pop("scores")
        result = scoring.score_candidate(item, "hook")
        self.assertTrue(all(result["scores"][key]["source"] == "heuristic" for key in scoring.POSITIVE_DIMENSIONS))
        self.assertEqual(result["scores"]["evidence_support"]["score"], 5.0)


if __name__ == "__main__":
    unittest.main()
