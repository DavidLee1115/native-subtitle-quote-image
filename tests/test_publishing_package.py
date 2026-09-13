import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publishing_package", ROOT / "scripts/publishing_package.py")
publishing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publishing)


def fixture(with_positioning=True):
    cues = []
    ranked = []
    final = []
    topics = ["选择", "行动", "判断", "成长", "边界"]
    for index, topic in enumerate(topics):
        quote = f"这是第{index + 1}个有来源的观点"
        cues.append({"id": f"q{index}", "start": index * 10, "end": index * 10 + 8, "text": quote})
        ranked.append({"candidate_id": f"c{index}", "quote": quote})
        final.append({
            "candidate_id": f"c{index}", "quote": quote, "quote_cue_ids": [f"q{index}"],
            "timestamp": index * 10 + 2, "semantic_topic": topic,
            "artifact_path": str(ROOT / "assets" / "native-subtitle-quote-image-icon.png"), "layout_type": "contain",
            "qa_result": {"status": "PASS"},
        })
    data = {
        "platform": "douyin", "account_stage": "cold",
        "source_video_metadata": {"video_id": "demo", "title": "Demo"},
        "source_transcript": {"cues": cues},
        "ranked_candidate_metadata": ranked,
        "final_selected_candidates": final,
    }
    if with_positioning:
        data["account_positioning"] = "用视频原话拆解决策问题"
    return data


class PublishingPackageTest(unittest.TestCase):
    def test_supported_claims_pass_and_write_all_outputs(self):
        package, claim_map = publishing.generate_package(fixture())
        self.assertEqual(package["status"], "PASS")
        self.assertTrue(package["publishable"])
        self.assertEqual(claim_map["status"], "PASS")
        self.assertFalse(claim_map["unsupported_claim_ids"])
        self.assertEqual({item["hook_type"] for item in package["hook_candidates"]}, set(publishing.HOOK_TYPES))
        self.assertEqual(len(package["title_candidates"]), 5)
        self.assertEqual(package["publishing_qa"]["status"], "PASS")
        with tempfile.TemporaryDirectory() as tmp:
            publishing.write_package(fixture(), Path(tmp))
            self.assertEqual(
                {path.name for path in Path(tmp).iterdir()},
                {"publishing-package.json", "publishing-package.md", "claim-evidence-map.json", "publishing-qa-result.json"},
            )

    def test_unsupported_claim_fails_closed_without_publishable_copy(self):
        data = fixture()
        data["editorial_draft"] = {
            "body_copy": "没有来源的业绩断言。",
            "body_claims": [{"text": "视频作者一年赚了1000万", "status": "unsupported"}],
        }
        package, claim_map = publishing.generate_package(data)
        self.assertEqual(package["status"], "FAIL")
        self.assertFalse(package["publishable"])
        self.assertNotIn("body_copy", package)
        self.assertEqual(claim_map["status"], "FAIL")
        self.assertIn("body-1", claim_map["unsupported_claim_ids"])

    def test_missing_positioning_is_inferred_and_marked(self):
        package, claim_map = publishing.generate_package(fixture(with_positioning=False))
        self.assertEqual(package["status"], "PASS")
        self.assertEqual(package["account_positioning"]["status"], "inferred")
        positioning = next(claim for claim in claim_map["claims"] if claim["claim_id"] == "positioning-1")
        self.assertEqual(positioning["status"], "inferred")

    def test_editorial_inferred_positioning_stays_inferred(self):
        data = fixture(with_positioning=False)
        data["inferred_positioning"] = "面向处在选择压力中的年轻人"
        package, _ = publishing.generate_package(data)
        self.assertEqual(package["account_positioning"]["status"], "inferred")
        self.assertEqual(package["account_positioning"]["value"], data["inferred_positioning"])

    def test_user_positioning_is_confirmed(self):
        package, _ = publishing.generate_package(fixture(with_positioning=True))
        self.assertEqual(package["account_positioning"]["status"], "confirmed")

    def test_rejects_non_frozen_or_failed_artifact(self):
        data = fixture(); data["final_selected_candidates"][0]["candidate_id"] = "not-ranked"
        with self.assertRaisesRegex(publishing.PublishingInputError, "absent from ranked"):
            publishing.generate_package(data)
        data = fixture(); data["final_selected_candidates"][0]["qa_result"] = {"status": "FAIL"}
        with self.assertRaisesRegex(publishing.PublishingInputError, "must be PASS"):
            publishing.generate_package(data)

    def test_rejects_artifact_hash_mismatch(self):
        data = fixture()
        data["input_provenance"] = {"artifact_sha256": {"c0": "0" * 64}}
        with self.assertRaisesRegex(publishing.PublishingInputError, "does not match frozen provenance"):
            publishing.generate_package(data)


if __name__ == "__main__":
    unittest.main()
