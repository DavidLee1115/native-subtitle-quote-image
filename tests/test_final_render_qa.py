import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "skills"
    / "native-subtitle-quote-image"
    / "scripts"
    / "final_render_qa.py"
)
SPEC = importlib.util.spec_from_file_location("final_render_qa", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def textured_image(size=(300, 280)):
    image = Image.effect_noise(size, 72).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.ellipse((90, 45, 210, 245), fill=(205, 145, 105))
    draw.rectangle((112, 95, 188, 230), fill=(32, 70, 150))
    return image


def valid_subtitle_strip(size=(300, 30)):
    image = Image.new("RGB", size, "#111318")
    draw = ImageDraw.Draw(image)
    for x in range(58, 242, 12):
        draw.rectangle((x, 9, x + 7, 13), fill="white")
        draw.rectangle((x + 3, 17, x + 9, 21), fill="white")
    return image


def source_text_row(size=(300, 80), y=26, color="white", background="#30343b"):
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    for x in range(38, size[0] - 38, 16):
        draw.rectangle((x, y, x + 4, y + 15), fill=color)
        draw.rectangle((x + 4, y + 5, x + 10, y + 9), fill=color)
    return image


class FinalRenderabilityTests(unittest.TestCase):
    def test_fit_crop_box_matches_portrait_center_crop(self):
        crop = MODULE.fit_crop_box((640, 360), (300, 280))
        self.assertAlmostEqual(crop[1], 0.0)
        self.assertAlmostEqual(crop[3], 360.0)
        self.assertLess(crop[2] - crop[0], 640)

    def test_blank_slide_fails_actual_final_renderability(self):
        source = Image.new("RGB", (640, 360), "#ece6da")
        rendered = Image.new("RGB", (300, 400), "#ece6da")
        result = MODULE.assess_final_renderability(
            source,
            rendered,
            source_crop_box=MODULE.fit_crop_box(source.size, (300, 280)),
            hero_box=[0, 0, 300, 280],
        )
        self.assertFalse(result["final_renderability"]["passed"])
        self.assertTrue(result["excessive_blank_area"]["detected"])
        self.assertIn(
            "insufficient_active_content", result["final_renderability"]["reasons"]
        )

    def test_blank_slide_still_fails_contain_layout(self):
        source = Image.new("RGB", (640, 360), "#ece6da")
        rendered = Image.new("RGB", (300, 400), "#ece6da")
        result = MODULE.assess_final_renderability(
            source,
            rendered,
            source_crop_box=[0, 0, 640, 360],
            hero_box=[0, 0, 300, 280],
            layout_mode="contain",
        )
        self.assertFalse(result["final_renderability"]["passed"])
        self.assertTrue(result["excessive_blank_area"]["detected"])

    def test_severe_subject_crop_fails_crop_safety(self):
        source = textured_image((640, 360))
        rendered = textured_image((300, 400))
        result = MODULE.assess_final_renderability(
            source,
            rendered,
            source_crop_box=[240, 0, 510, 360],
            hero_box=[0, 0, 300, 280],
            subject_box=[80, 40, 300, 340],
        )
        self.assertLess(result["subject_retention"]["score"], 0.5)
        self.assertFalse(result["crop_safety"]["passed"])
        self.assertFalse(result["final_renderability"]["passed"])

    def test_valid_portrait_hero_passes(self):
        source = textured_image((640, 360))
        hero = textured_image((300, 280))
        rendered = Image.new("RGB", (300, 400), "black")
        rendered.paste(hero, (0, 0))
        result = MODULE.assess_final_renderability(
            source,
            rendered,
            source_crop_box=[140, 0, 500, 360],
            hero_box=[0, 0, 300, 280],
            subject_box=[210, 45, 420, 345],
        )
        self.assertTrue(result["crop_safety"]["passed"])
        self.assertFalse(result["excessive_blank_area"]["detected"])
        self.assertTrue(result["final_renderability"]["passed"])

    def test_real_cby_c24_document_signal_requires_contain_repair(self):
        path = (
            ROOT
            / "benchmark"
            / "phase4a1"
            / "final-renders"
            / "CBYhVcO4WgI"
            / "c24.jpg"
        )
        if not path.is_file():
            self.skipTest("ignored Phase 4A.1 artifact is not present")
        with Image.open(path) as opened:
            rendered = opened.convert("RGB")
        source = rendered.crop((0, 0, 1440, 1344))
        source_visual = {
            "passed": False,
            "document_like": True,
            "reasons": ["document_or_slide_low_visual"],
        }
        fit = MODULE.assess_final_renderability(
            source,
            rendered,
            source_crop_box=[0, 0, source.width, source.height],
            hero_box=[0, 0, 1440, 1344],
            source_visual=source_visual,
            layout_mode="fit",
        )
        contain = MODULE.assess_final_renderability(
            source,
            rendered,
            source_crop_box=[0, 0, source.width, source.height],
            hero_box=[0, 0, 1440, 1344],
            source_visual=source_visual,
            layout_mode="contain",
        )
        self.assertFalse(fit["final_renderability"]["passed"])
        self.assertIn(
            "low_visual_or_document_source_requires_contain_layout",
            fit["final_renderability"]["reasons"],
        )
        self.assertTrue(contain["crop_safety"]["passed"])


class ScriptSourceTextCollisionTests(unittest.TestCase):
    def test_obvious_source_text_generated_strip_collision_fails(self):
        source = source_text_row()
        result = MODULE.assess_script_source_text_collision(
            source,
            generated_text_box=[24, 18, 276, 58],
            line_index=2,
        )
        self.assertFalse(result["passed"], result)
        self.assertTrue(result["collision_detected"])
        self.assertEqual(
            result["reason"], "source_text_overlaps_generated_script_clearance"
        )

    def test_short_source_label_inside_generated_text_fails(self):
        source = Image.new("RGB", (500, 90), "#30343b")
        draw = ImageDraw.Draw(source)
        for x in (20, 36, 52, 68):
            draw.rectangle((x, 24, x + 5, 48), fill="white")
            draw.rectangle((x + 5, 24, x + 11, 30), fill="white")
            draw.rectangle((x + 5, 36, x + 10, 42), fill="white")
        result = MODULE.assess_script_source_text_collision(
            source,
            generated_text_box=[10, 20, 490, 65],
        )
        self.assertFalse(result["passed"], result)

    def test_source_text_near_but_outside_generated_clearance_passes(self):
        source = source_text_row(size=(300, 100), y=4)
        result = MODULE.assess_script_source_text_collision(
            source,
            generated_text_box=[24, 58, 276, 92],
        )
        self.assertTrue(result["passed"], result)

    def test_ordinary_talking_head_texture_passes(self):
        source = textured_image((300, 100))
        result = MODULE.assess_script_source_text_collision(
            source,
            generated_text_box=[24, 28, 276, 72],
        )
        self.assertTrue(result["passed"], result)

    def test_document_source_with_safe_strip_placement_passes(self):
        source = Image.new("RGB", (300, 120), "#f2efe6")
        draw = ImageDraw.Draw(source)
        for row_y in (8, 24, 40):
            for x in range(32, 268, 15):
                draw.rectangle((x, row_y, x + 8, row_y + 7), fill="#242424")
        result = MODULE.assess_script_source_text_collision(
            source,
            generated_text_box=[24, 76, 276, 110],
        )
        self.assertTrue(result["passed"], result)

    def test_repaired_placement_clears_source_text(self):
        source = source_text_row(size=(300, 120), y=63)
        colliding = MODULE.assess_script_source_text_collision(
            source,
            generated_text_box=[24, 54, 276, 96],
        )
        repaired = MODULE.assess_script_source_text_collision(
            source,
            generated_text_box=[24, 5, 276, 39],
        )
        self.assertFalse(colliding["passed"], colliding)
        self.assertTrue(repaired["passed"], repaired)

    def test_missing_collision_evidence_fails_closed(self):
        result = MODULE.assess_script_collisions([], expected_line_count=1)
        self.assertFalse(result["all_pass"])
        self.assertFalse(result["coverage_passed"])

    def test_real_veritasium_c19_collision_then_safe_band(self):
        phase4b = ROOT.parent / "native-subtitle-quote-image-phase4b"
        video = (
            phase4b
            / "benchmark"
            / "phase4b"
            / "source"
            / "veritasium-education-rhgwIhB58PA"
            / "rhgwIhB58PA.mp4"
        )
        if not video.is_file():
            self.skipTest("Phase 4B Veritasium source video is not present")
        renderer_path = (
            ROOT
            / "skills"
            / "native-subtitle-quote-image"
            / "scripts"
            / "native_subtitle_stitch.py"
        )
        spec = importlib.util.spec_from_file_location("phase4c_renderer", renderer_path)
        renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renderer)
        frame = renderer.grab_frame(video, 589.365)

        def collision_at(center):
            source_height = 128
            center_y = round(frame.height * center)
            y0 = max(
                0,
                min(frame.height - source_height, center_y - source_height // 2),
            )
            source = ImageOps.fit(
                frame.crop((0, y0, frame.width, y0 + source_height)),
                (1440, 144),
                method=Image.Resampling.LANCZOS,
            )
            rendered = source.copy()
            text = renderer.draw_scripted_subtitle(
                rendered,
                "of the learning styles approach within education",
                72,
                None,
                80,
                1325,
            )
            return MODULE.assess_script_source_text_collision(
                source,
                generated_text_box=text["text_box"],
                line_index=3,
            )

        original = collision_at(0.82)
        repaired = collision_at(0.46)
        self.assertFalse(original["passed"], original)
        self.assertTrue(repaired["passed"], repaired)


class NativeSubtitlePresenceTests(unittest.TestCase):
    def test_temporal_band_finds_moving_bottom_subtitle(self):
        before = Image.new("RGB", (640, 360), "#22252b")
        active = before.copy()
        draw = ImageDraw.Draw(active)
        for x in range(150, 490, 18):
            draw.rectangle((x, 294, x + 11, 314), fill="white")
        result = MODULE.detect_temporal_subtitle_band(
            active, before, configured_band=[0.38, 0.62]
        )
        self.assertTrue(result["detected"])
        self.assertGreater(result["top"], 0.70)
        self.assertLess(result["configured_band_overlap"], 0.20)

    def test_missing_native_strip_fails(self):
        strip = Image.new("RGB", (300, 30), "#111318")
        result = MODULE.native_subtitle_presence(strip)
        self.assertFalse(result["passed"])
        self.assertEqual(result["score"], 0.0)

    def test_textured_frame_without_subtitle_fails(self):
        strip = Image.effect_noise((300, 30), 72).convert("RGB")
        result = MODULE.native_subtitle_presence(strip)
        self.assertFalse(result["passed"], result)
        self.assertIn("no_compact_text_row_peak", result["reasons"])

    def test_valid_native_strip_passes(self):
        result = MODULE.native_subtitle_presence(valid_subtitle_strip())
        self.assertTrue(result["passed"], result)
        self.assertGreaterEqual(result["score"], 0.5)

    def test_saturated_colored_native_strip_requires_temporal_evidence(self):
        strip = Image.new("RGB", (300, 30), "#080a0e")
        draw = ImageDraw.Draw(strip)
        for x in range(55, 245, 14):
            draw.rectangle((x, 8, x + 8, 13), fill="#16e3dc")
            draw.rectangle((x + 2, 17, x + 10, 22), fill="#f229a8")
        result = MODULE.native_subtitle_presence(strip)
        self.assertFalse(result["passed"], result)
        self.assertEqual(result["metrics"]["polarity"], "saturated_color")
        rendered = Image.new("RGB", (300, 400), "#333333")
        for y in (280, 310, 340, 370):
            rendered.paste(strip, (0, y))
        aggregate = MODULE.assess_native_strips(
            rendered,
            hero_height=280,
            strip_heights=[30, 30, 30, 30],
            temporal_evidence=[
                {"detected": True, "final_band_overlap": 1.0}
                for _ in range(4)
            ],
        )
        self.assertTrue(aggregate["all_pass"], aggregate)

    def test_every_final_native_strip_must_pass(self):
        rendered = Image.new("RGB", (300, 400), "#333333")
        rendered.paste(valid_subtitle_strip(), (0, 280))
        rendered.paste(valid_subtitle_strip(), (0, 310))
        rendered.paste(Image.new("RGB", (300, 30), "#111318"), (0, 340))
        rendered.paste(valid_subtitle_strip(), (0, 370))
        result = MODULE.assess_native_strips(
            rendered, hero_height=280, strip_heights=[30, 30, 30, 30]
        )
        self.assertEqual(result["required_strip_count"], 4)
        self.assertEqual(result["passed_strip_count"], 3)
        self.assertFalse(result["all_pass"])

    def test_temporal_evidence_can_corroborate_large_connected_glyphs(self):
        artifact = (
            ROOT
            / "benchmark"
            / "phase4a2"
            / "final-renders"
            / "nsFkjRWjNxs"
            / "01_c22.jpg"
        )
        if not artifact.is_file():
            self.skipTest("Phase 4A.2 repaired artifact is not present")
        with Image.open(artifact) as opened:
            rendered = opened.convert("RGB")
        temporal = [None, None, {
            "detected": True,
            "final_band_overlap": 1.0,
        }, None]
        result = MODULE.assess_native_strips(
            rendered,
            hero_height=1344,
            strip_heights=[144, 144, 144, 144],
            temporal_evidence=temporal,
        )
        third = result["items"][2]
        self.assertTrue(third["passed"], third)
        self.assertTrue(
            third["pixel_presence_passed"] or third["temporal_corroborated"]
        )

    def test_temporal_evidence_can_corroborate_colored_subtitle(self):
        artifact = (
            ROOT
            / "benchmark"
            / "phase4a2"
            / "phase3-regression"
            / "final-renders"
            / "Jb_FEXwUmq8"
            / "01_Or quick questions and answers.jpg"
        )
        if not artifact.is_file():
            self.skipTest("Phase 3 repaired artifact is not present")
        with Image.open(artifact) as opened:
            rendered = opened.convert("RGB")
        temporal = [None, None, None, {
            "detected": True,
            "final_band_overlap": 1.0,
            "peak_score": 82.0,
            "baseline_score": 5.0,
        }]
        result = MODULE.assess_native_strips(
            rendered,
            hero_height=672,
            strip_heights=[72, 72, 72, 72],
            temporal_evidence=temporal,
        )
        fourth = result["items"][3]
        self.assertTrue(fourth["passed"], fourth)
        self.assertTrue(fourth["temporal_corroborated"])

    def test_real_phase4a_native_artifacts_match_manual_strip_labels(self):
        artifact_root = ROOT / "benchmark" / "phase4a1" / "final-renders" / "nsFkjRWjNxs"
        qa_path = artifact_root / "qa-results.json"
        if not qa_path.is_file():
            self.skipTest("ignored Phase 4A.1 artifacts are not present")
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
        expected = {
            "c22": [False, False, False, False],
            "c21": [True, True, True, True],
            "c15": [True, True, True, True],
            "c20": [False, True, True, True],
            "c14": [True, True, True, True],
        }
        for item in qa["items"]:
            with Image.open(artifact_root / item["file"]) as image:
                result = MODULE.assess_native_strips(
                    image.convert("RGB"),
                    hero_height=1344,
                    strip_heights=[144, 144, 144, 144],
                )
            self.assertEqual(
                [strip["passed"] for strip in result["items"]],
                expected[item["title"]],
                item["title"],
            )


class ClosedLoopTests(unittest.TestCase):
    def test_repair_order_is_enforced(self):
        with self.assertRaisesRegex(ValueError, "repair order"):
            MODULE.validate_repair_plan(
                [
                    {"stage": "semantic_window", "name": "new cue"},
                    {"stage": "layout_crop", "name": "contain hero"},
                ]
            )

    def test_exhaustion_is_recorded_before_reserve_promotion(self):
        selected = [{"id": "primary"}]
        reserves = [{"id": "reserve-1", "base_rank": 6}]

        def repairs(_candidate):
            return [
                {"stage": "layout_crop", "name": "contain"},
                {"stage": "semantic_window", "name": "nearby-cue"},
                {"stage": "legal_fallback", "name": "quote-first"},
            ]

        def render(candidate, repair):
            return {
                "candidate_id": candidate["id"],
                "repair": repair and repair["name"],
            }

        def qa(candidate, _artifact, _repair):
            return {"passed": candidate["id"] == "reserve-1"}

        def validate(_candidate, _accepted, _slot):
            return {
                "checks": {
                    "semantic_alignment": True,
                    "duplicate_cluster": True,
                    "set_diversity": True,
                    "visual_viability": True,
                },
                "reason": "eligible next ranked reserve",
            }

        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.jsonl"
            result = MODULE.run_finalization_loop(
                selected,
                reserves,
                repairs_for=repairs,
                render=render,
                qa=qa,
                promotion_validate=validate,
                audit_path=audit_path,
            )
            persisted = [
                json.loads(line)
                for line in audit_path.read_text(encoding="utf-8").splitlines()
            ]
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["accepted"][0]["candidate_id"], "reserve-1")
        events = [item["event"] for item in persisted]
        self.assertLess(events.index("repair_exhaustion"), events.index("reserve_promoted"))
        primary_stages = [
            item["stage"]
            for item in persisted
            if item["event"] == "render_attempt"
            and item["candidate_id"] == "primary"
        ]
        self.assertEqual(
            primary_stages,
            ["initial", "layout_crop", "semantic_window", "legal_fallback"],
        )
        validation = next(
            item for item in persisted if item["event"] == "reserve_validation"
        )
        self.assertTrue(all(validation["checks"].values()))
        promoted = next(item for item in persisted if item["event"] == "reserve_promoted")
        self.assertEqual(promoted["original_candidate_id"], "primary")
        self.assertEqual(promoted["replaced_candidate_id"], "primary")

    def test_reserve_promotion_is_fail_closed_without_four_checks(self):
        result = MODULE.run_finalization_loop(
            [{"id": "primary"}],
            [{"id": "reserve"}],
            repairs_for=lambda _candidate: [],
            render=lambda candidate, _repair: candidate,
            qa=lambda _candidate, _artifact, _repair: {"passed": False},
            promotion_validate=lambda _candidate, _accepted, _slot: {
                "checks": {"semantic_alignment": True}
            },
        )
        self.assertEqual(result["status"], "EXHAUSTED")
        self.assertFalse(result["promotion_decisions"][0]["passed"])
        self.assertIn("reserve_rejected", [item["event"] for item in result["audit"]])

    def test_reserve_exhaustion_returns_explicit_status(self):
        result = MODULE.run_finalization_loop(
            [{"id": "primary"}],
            [],
            repairs_for=lambda _candidate: [],
            render=lambda candidate, _repair: candidate["id"],
            qa=lambda _candidate, _artifact, _repair: {
                "passed": False,
                "reason": "final QA failed",
            },
        )
        self.assertEqual(result["status"], "EXHAUSTED")
        self.assertEqual(result["unrenderable"][0]["status"], "UNRENDERABLE")
        self.assertIn("reserve_exhausted", [item["event"] for item in result["audit"]])


if __name__ == "__main__":
    unittest.main()
