import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "skills"
    / "native-subtitle-quote-image"
    / "scripts"
    / "native_subtitle_stitch.py"
)
ENV_SCRIPT = (
    ROOT
    / "skills"
    / "native-subtitle-quote-image"
    / "scripts"
    / "check_environment.py"
)
SPEC = importlib.util.spec_from_file_location("native_subtitle_stitch", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HelperTests(unittest.TestCase):
    def test_parse_aspect_and_safe_title(self):
        self.assertEqual(MODULE.parse_aspect("3:4"), (3.0, 4.0))
        self.assertEqual(MODULE.safe_title('a/b:c*?"<>|'), "a_b_c")

    def test_default_sample_times_cover_range_with_24_frames(self):
        times = MODULE.build_sample_times(0, 230, None, 48)
        self.assertEqual(len(times), 24)
        self.assertEqual(times[0], 0)
        self.assertAlmostEqual(times[-1], 230)

    def test_sample_times_enforce_frame_cap(self):
        with self.assertRaisesRegex(SystemExit, "超过上限"):
            MODULE.build_sample_times(0, 100, 1, 48)

    def test_focus_times_add_before_middle_and_after(self):
        times = MODULE.build_focus_times([1, 3], 0.5, 5, 48)
        self.assertEqual(times, [0.5, 1.0, 1.5, 2.5, 3.0, 3.5])

    def test_focus_times_clip_and_deduplicate_edges(self):
        times = MODULE.build_focus_times([0, 2.7], 0.5, 3, 48)
        self.assertEqual(times, [0.0, 0.5, 2.2, 2.7])

    def test_auto_layout_keeps_subtitle_strips_compact(self):
        self.assertEqual(MODULE.choose_hero_fraction(4), 0.7)
        self.assertEqual(MODULE.choose_hero_fraction(3), 0.775)
        self.assertEqual(MODULE.choose_hero_fraction(2), 0.82)
        self.assertEqual(MODULE.choose_hero_fraction(7), 0.48)
        self.assertEqual(MODULE.choose_hero_fraction(4, 0.6), 0.6)

    def test_native_layout_distinguishes_bottom_center_and_low_visual(self):
        self.assertEqual(MODULE.classify_native_layout(0.78, 0.96), "bottom-band")
        self.assertEqual(MODULE.classify_native_layout(0.38, 0.62), "centered-band")
        self.assertEqual(MODULE.classify_native_layout(0.381, 0.99), "centered-band")
        self.assertEqual(
            MODULE.classify_native_layout(0.38, 0.62, low_visual=True),
            "low-visual-fallback",
        )

    def test_visual_gate_rejects_dark_empty_and_accepts_rich_texture(self):
        empty = Image.new("RGB", (640, 360), "#050509")
        rich = Image.effect_noise((160, 90), 90).convert("RGB").resize(
            (640, 360), Image.Resampling.NEAREST
        )
        self.assertFalse(MODULE.visual_assessment(empty)["passed"])
        self.assertTrue(MODULE.visual_assessment(rich)["passed"])

    def test_visual_gate_rejects_light_document_or_slide(self):
        slide = Image.new("RGB", (640, 360), "#eadfcb")
        for y0 in (45, 170):
            for y in range(y0, y0 + 45):
                for x in range(70, 570):
                    if (x // 7 + y // 5) % 3 == 0:
                        slide.putpixel((x, y), (20, 20, 20))
        assessment = MODULE.visual_assessment(slide)
        self.assertFalse(assessment["passed"])
        self.assertTrue(assessment["document_like"])
        self.assertIn("document_or_slide_low_visual", assessment["reasons"])

    def test_quote_first_false_positive_keeps_rich_original_hero(self):
        rich = Image.effect_noise((160, 90), 90).convert("RGB").resize(
            (640, 360), Image.Resampling.NEAREST
        )
        with mock.patch.object(MODULE, "grab_frame", return_value=rich):
            result = MODULE.choose_visual_hero(
                "unused.mp4",
                10,
                [],
                30,
                [],
                [],
                lambda _event, **_details: None,
                quote_times=[10, 11, 12, 13, 14],
            )
        self.assertEqual(result["phase"], "original")
        self.assertEqual(result["semantic_alignment"], "PASS")

    def test_auto_repair_exhaustion_is_explicit_and_does_not_cross_source(self):
        empty = Image.new("RGB", (640, 360), "#050509")
        events = []
        with mock.patch.object(MODULE, "grab_frame", return_value=empty):
            result = MODULE.choose_visual_hero(
                "unused.mp4",
                10,
                [],
                200,
                [],
                [180],
                lambda event, **details: events.append((event, details)),
                quote_times=[10, 11, 12, 13, 14],
            )
        self.assertIsNone(result)
        exhausted = [details for event, details in events if event == "semantic_window_exhausted"]
        self.assertEqual(len(exhausted), 1)
        self.assertEqual(exhausted[0]["semantic_alignment"], "FAIL")

    def test_visual_gate_repairs_with_same_line_nearby_frame(self):
        empty = Image.new("RGB", (640, 360), "#050509")
        rich = Image.effect_noise((160, 90), 90).convert("RGB").resize(
            (640, 360), Image.Resampling.NEAREST
        )

        def fake_frame(_video, seconds):
            return rich if seconds == 10.8 else empty

        events = []
        with mock.patch.object(MODULE, "grab_frame", side_effect=fake_frame):
            result = MODULE.choose_visual_hero(
                "unused.mp4",
                10,
                [],
                30,
                [],
                [],
                lambda event, **details: events.append((event, details)),
            )
        self.assertEqual(result["phase"], "same-line-nearby")
        self.assertEqual(result["time"], 10.8)
        self.assertTrue(any(event == "auto_repair" for event, _ in events))

    def test_source_wide_fallback_prefers_human_like_candidate(self):
        empty = Image.new("RGB", (640, 360), "#050509")
        texture = Image.effect_noise((160, 90), 90).convert("RGB").resize(
            (640, 360), Image.Resampling.NEAREST
        )
        human_like = texture.copy()
        for y in range(80, 300):
            for x in range(180, 460):
                human_like.putpixel((x, y), (205, 145, 105))

        def fake_frame(_video, seconds):
            if seconds == 140:
                return texture
            if seconds == 150:
                return human_like
            return empty

        with mock.patch.object(MODULE, "grab_frame", side_effect=fake_frame):
            result = MODULE.choose_visual_hero(
                "unused.mp4",
                100,
                [],
                200,
                [],
                [140, 150],
                lambda _event, **_details: None,
                allow_source_wide=True,
            )
        self.assertEqual(result["phase"], "source-wide-fallback")
        self.assertEqual(result["time"], 150)
        self.assertGreater(result["assessment"]["skin_tone_ratio"], 0.10)

    def test_default_selection_does_not_cross_semantic_window(self):
        empty = Image.new("RGB", (640, 360), "#050509")
        rich = Image.effect_noise((160, 90), 90).convert("RGB").resize(
            (640, 360), Image.Resampling.NEAREST
        )
        seen = []

        def fake_frame(_video, seconds):
            seen.append(seconds)
            return rich if seconds == 180 else empty

        with mock.patch.object(MODULE, "grab_frame", side_effect=fake_frame):
            result = MODULE.choose_visual_hero(
                "unused.mp4",
                10,
                [],
                200,
                [],
                [180],
                lambda _event, **_details: None,
                quote_times=[10, 11, 12, 13, 14],
            )
        self.assertIsNone(result)
        self.assertNotIn(180, seen)

    def test_semantic_groups_are_ordered_and_bounded(self):
        groups = MODULE.candidate_groups(
            100,
            [40, 105, 180],
            240,
            quote_times=[100, 101, 102, 103, 104],
        )
        self.assertEqual(
            [name for name, _values in groups],
            ["original", "same-line-nearby", "same-theme-window", "same-speaking-shot"],
        )
        theme = dict(groups)["same-theme-window"]
        self.assertIn(105, theme)
        self.assertNotIn(40, theme)
        self.assertNotIn(180, theme)
        self.assertTrue(all(70 <= value <= 134 for value in theme))

    def test_outside_explicit_candidate_is_rejected_by_semantic_gate(self):
        empty = Image.new("RGB", (640, 360), "#050509")
        events = []
        with mock.patch.object(MODULE, "grab_frame", return_value=empty):
            MODULE.choose_visual_hero(
                "unused.mp4",
                100,
                [180],
                240,
                [],
                [],
                lambda event, **details: events.append((event, details)),
                quote_times=[100, 101, 102, 103, 104],
            )
        rejected = [
            details
            for event, details in events
            if event == "semantic_gate" and details.get("reason") == "outside_theme_window"
        ]
        self.assertEqual(rejected[0]["time"], 180)
        self.assertEqual(rejected[0]["reason"], "outside_theme_window")

    def test_declared_speaking_window_enables_final_semantic_layer(self):
        empty = Image.new("RGB", (640, 360), "#050509")
        rich = Image.effect_noise((160, 90), 90).convert("RGB").resize(
            (640, 360), Image.Resampling.NEAREST
        )

        def fake_frame(_video, seconds):
            return rich if seconds == 25 else empty

        with mock.patch.object(MODULE, "grab_frame", side_effect=fake_frame):
            result = MODULE.choose_visual_hero(
                "unused.mp4",
                100,
                [],
                240,
                [],
                [],
                lambda _event, **_details: None,
                quote_times=[100, 101, 102, 103, 104],
                speaking_window=[10, 190],
                speaking_window_verified=True,
            )
        self.assertEqual(result["phase"], "same-speaking-shot")
        self.assertEqual(result["semantic_basis"], "manifest_declared_speaking_window")

    def test_quote_first_uses_native_pixels_and_preserves_quote_width(self):
        frame = Image.new("RGB", (640, 360), "#02040a")
        for y in range(160, 195):
            for x in range(90, 550):
                if (x // 8 + y // 6) % 3 == 0:
                    frame.putpixel((x, y), (245, 245, 245))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            MODULE, "grab_frame", return_value=frame
        ):
            out = Path(tmp) / "quote-first.jpg"
            report = MODULE.render_one(
                "unused.mp4",
                [0, 1, 2, 3, 4],
                out,
                (3, 4),
                300,
                0.38,
                0.62,
                None,
                native_layout="quote-first",
            )
            self.assertTrue(report["quote_detection"]["detected"])
            self.assertEqual(report["subtitle_horizontal_retention"], 1.0)
            with Image.open(out) as rendered:
                self.assertEqual(rendered.size, (300, 400))

    def test_subject_center_ignores_edge_connected_background(self):
        width, height = 20, 10
        mask = [False] * (width * height)
        for y in range(6):
            for x in range(16, 20):
                mask[y * width + x] = True
        for y in range(2, 9):
            for x in range(4, 9):
                mask[y * width + x] = True
        center = MODULE.largest_interior_component_center(mask, width, height)
        self.assertAlmostEqual(center, 6 / 19)
        source = Image.new("RGB", (1920, 648))
        centering = MODULE.subject_aware_centering(source, (1440, 1150), center)
        self.assertLess(centering, 0.5)

    def test_centered_layout_preserves_full_subtitle_band_width(self):
        semantic = Image.new("RGB", (640, 360), "#202020")
        for y in range(round(360 * 0.38), round(360 * 0.62)):
            for x in range(24):
                semantic.putpixel((x, y), (240, 20, 20))
                semantic.putpixel((639 - x, y), (20, 220, 20))
        rich = Image.effect_noise((160, 90), 90).convert("RGB").resize(
            (640, 360), Image.Resampling.NEAREST
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            MODULE, "grab_frame", return_value=semantic
        ):
            out = Path(tmp) / "centered.jpg"
            report = MODULE.render_one(
                "unused.mp4",
                [0, 1, 2, 3, 4],
                out,
                (3, 4),
                300,
                0.38,
                0.62,
                None,
                hero_frame=rich,
                native_layout="centered-band",
            )
            self.assertEqual(report["subtitle_horizontal_retention"], 1.0)
            with Image.open(out) as rendered:
                x0, y0, x1, y1 = report["contained_frame_box"]
                band_y = round(y0 + (y1 - y0) * 0.50)
                self.assertGreater(rendered.getpixel((2, band_y))[0], 150)
                self.assertGreater(rendered.getpixel((297, band_y))[1], 130)

    def test_bottom_layout_preserves_full_subtitle_band_width(self):
        semantic = Image.new("RGB", (640, 360), "#202020")
        for y in range(round(360 * 0.78), round(360 * 0.96)):
            for x in range(24):
                semantic.putpixel((x, y), (240, 20, 20))
                semantic.putpixel((639 - x, y), (20, 220, 20))
        rich = Image.effect_noise((160, 90), 90).convert("RGB").resize(
            (640, 360), Image.Resampling.NEAREST
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            MODULE, "grab_frame", return_value=semantic
        ):
            out = Path(tmp) / "bottom.jpg"
            report = MODULE.render_one(
                "unused.mp4",
                [0, 1, 2, 3, 4],
                out,
                (3, 4),
                300,
                0.78,
                0.96,
                None,
                hero_frame=rich,
                native_layout="bottom-band",
            )
            self.assertEqual(report["subtitle_horizontal_retention"], 1.0)
            with Image.open(out) as rendered:
                band_y = report["hero_height"] - report["band_height"] // 2
                self.assertGreater(rendered.getpixel((2, band_y))[0], 150)
                self.assertGreater(rendered.getpixel((297, band_y))[1], 130)

    def test_quote_crop_supports_dark_text_on_light_background(self):
        band = Image.new("RGB", (640, 180), "#eadfcb")
        for y in range(60, 100):
            for x in range(120, 520):
                if (x // 7 + y // 5) % 3 == 0:
                    band.putpixel((x, y), (20, 20, 20))
        crop, report = MODULE.detect_native_quote_crop(band)
        self.assertTrue(report["detected"])
        self.assertEqual(report["polarity"], "dark_on_light")
        self.assertLess(crop.width, band.width)

    def test_low_visual_fallback_excludes_candidate_lower_overlay(self):
        semantic = Image.new("RGB", (640, 360), "#202020")
        hero = Image.new("RGB", (640, 360), "#1d4ed8")
        for y in range(216, 360):
            for x in range(640):
                hero.putpixel((x, y), (220, 20, 20))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            MODULE, "grab_frame", return_value=semantic
        ):
            out = Path(tmp) / "fallback.jpg"
            report = MODULE.render_one(
                "unused.mp4",
                [0, 1, 2, 3, 4],
                out,
                (3, 4),
                300,
                0.38,
                0.62,
                None,
                hero_frame=hero,
                native_layout="low-visual-fallback",
            )
            with Image.open(out) as rendered:
                visual_bottom = report["hero_height"] - report["band_height"] - 4
                blue = rendered.getpixel((150, visual_bottom))
                self.assertGreater(blue[2], blue[0] * 2)

    def test_script_lines_require_increasing_timestamps_and_text(self):
        lines = MODULE.normalize_script_lines(
            {
                "lines": [
                    {"t": 1, "text": "First"},
                    {"t": 2, "text": "Second"},
                ]
            },
            3,
        )
        self.assertEqual(lines[1]["text"], "Second")
        with self.assertRaisesRegex(SystemExit, "严格递增"):
            MODULE.normalize_script_lines(
                {
                    "lines": [
                        {"t": 2, "text": "First"},
                        {"t": 1, "text": "Second"},
                    ]
                },
                3,
            )
        with self.assertRaisesRegex(SystemExit, "最多支持 7"):
            MODULE.normalize_script_lines(
                {
                    "lines": [
                        {"t": index / 10, "text": f"Line {index}"}
                        for index in range(8)
                    ]
                },
                3,
            )

    def test_cjk_detection_covers_chinese_japanese_and_korean(self):
        self.assertTrue(MODULE.contains_cjk("中文"))
        self.assertTrue(MODULE.contains_cjk("かな"))
        self.assertTrue(MODULE.contains_cjk("한글"))
        self.assertFalse(MODULE.contains_cjk("English"))

    def test_environment_check_local_mode_is_machine_readable(self):
        proc = subprocess.run(
            [sys.executable, str(ENV_SCRIPT), "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["mode"], "local")
        self.assertTrue(payload["ok"])
        components = {item["component"] for item in payload["components"]}
        self.assertIn("Python 3.10+", components)
        self.assertIn("yt-dlp", components)
        self.assertIn("CJK font", components)

    def test_environment_check_url_mode_guides_cookie_recovery(self):
        proc = subprocess.run(
            [sys.executable, str(ENV_SCRIPT), "--url-mode"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn("不要退回本地模式", proc.stdout)
        self.assertIn("--cookies-from-browser chrome", proc.stdout)

    def test_render_one_has_requested_dimensions(self):
        frame = Image.new("RGB", (640, 360), "#336699")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            MODULE, "grab_frame", return_value=frame
        ):
            out = Path(tmp) / "render.jpg"
            MODULE.render_one(
                "unused.mp4",
                [0, 1, 2, 3, 4],
                out,
                (3, 4),
                300,
                0.68,
                0.96,
                0.42,
            )
            with Image.open(out) as rendered:
                self.assertEqual(rendered.size, (300, 400))

    def test_render_one_auto_layout_gives_hero_seventy_percent(self):
        def fake_frame(_video, seconds):
            color = "#cc0000" if seconds == 0 else "#0033cc"
            return Image.new("RGB", (640, 360), color)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            MODULE, "grab_frame", side_effect=fake_frame
        ):
            out = Path(tmp) / "auto-layout.jpg"
            MODULE.render_one(
                "unused.mp4",
                [0, 1, 2, 3, 4],
                out,
                (3, 4),
                300,
                0.78,
                0.96,
                None,
            )
            with Image.open(out) as rendered:
                self.assertGreater(rendered.getpixel((10, 279))[0], 180)
                self.assertGreater(rendered.getpixel((10, 280))[2], 150)

    def test_missing_input_is_readable_without_traceback(self):
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "render",
                "/no/such/video.mp4",
                "--manifest",
                "/no/such/manifest.json",
                "--out-dir",
                "/tmp/unused-native-subtitle-output",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("视频不存在或不是文件", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


class CliIntegrationTests(unittest.TestCase):
    def test_sample_band_and_render_with_synthetic_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            video = tmp_path / "synthetic.mp4"
            subprocess.run(
                [
                    MODULE.FFMPEG,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=640x360:rate=10",
                    "-t",
                    "3",
                    "-c:v",
                    "mpeg4",
                    "-pix_fmt",
                    "yuv420p",
                    str(video),
                ],
                check=True,
                capture_output=True,
            )

            sample = tmp_path / "candidate.jpg"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "sample",
                    str(video),
                    "--start",
                    "0.5",
                    "--end",
                    "2.5",
                    "--interval",
                    "1",
                    "--out",
                    str(sample),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(sample.is_file())

            default_sample = tmp_path / "default-candidate.jpg"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "sample",
                    str(video),
                    "--out",
                    str(default_sample),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(default_sample.is_file())

            focused_sample = tmp_path / "focused-candidate.jpg"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "sample",
                    str(video),
                    "-t",
                    "1",
                    "-t",
                    "2",
                    "--around",
                    "0.2",
                    "--out",
                    str(focused_sample),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(focused_sample.is_file())

            band = tmp_path / "band.jpg"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "band",
                    str(video),
                    "-t",
                    "1",
                    "--out",
                    str(band),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(band.is_file())

            manifest = tmp_path / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {"images": [{"title": "合成测试", "times": [0.5, 1, 1.5, 2, 2.5]}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            out_dir = tmp_path / "output"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "render",
                    str(video),
                    "--manifest",
                    str(manifest),
                    "--out-dir",
                    str(out_dir),
                    "--width",
                    "300",
                    "--visual-gate",
                    "off",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            output = out_dir / "01_合成测试.jpg"
            self.assertTrue(output.is_file())
            self.assertTrue((out_dir / "final_contact_sheet.jpg").is_file())
            self.assertTrue((out_dir / "原生字幕时间点.json").is_file())
            self.assertTrue((out_dir / "qa-results.json").is_file())
            self.assertTrue((out_dir / "render-decisions.jsonl").is_file())
            qa = json.loads((out_dir / "qa-results.json").read_text(encoding="utf-8"))
            self.assertEqual(qa["semantic_alignment"], "PASS")
            self.assertEqual(qa["items"][0]["semantic_alignment"], "PASS")
            with Image.open(output) as rendered:
                self.assertEqual(rendered.size, (300, 400))

            script = tmp_path / "script.json"
            script.write_text(
                json.dumps(
                    {
                        "lines": [
                            {"t": 0.5, "text": "First point"},
                            {"t": 1.0, "text": "Second point"},
                            {"t": 1.5, "text": "Third point"},
                            {"t": 2.0, "text": "Fourth point"},
                            {"t": 2.5, "text": "Fifth point"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            scripted_output = tmp_path / "scripted.jpg"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "render-script",
                    str(video),
                    "--script",
                    str(script),
                    "--out",
                    str(scripted_output),
                    "--width",
                    "300",
                    "--font-size",
                    "18",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            with Image.open(scripted_output) as rendered:
                self.assertEqual(rendered.size, (300, 400))

            repeated = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "render",
                    str(video),
                    "--manifest",
                    str(manifest),
                    "--out-dir",
                    str(out_dir),
                    "--width",
                    "300",
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(repeated.returncode, 0)
            self.assertIn("--overwrite", repeated.stderr)


if __name__ == "__main__":
    unittest.main()
