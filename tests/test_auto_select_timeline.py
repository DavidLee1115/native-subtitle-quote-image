import importlib.util
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "auto_select_timeline.py"
SPEC = importlib.util.spec_from_file_location("auto_select_timeline", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AutoTimelineTests(unittest.TestCase):
    def test_frame_time_uses_stable_cue_middle(self):
        cue = {"start": 10.0, "end": 12.0, "text": "Stable subtitle"}
        self.assertEqual(MODULE.cue_frame_time(cue), 11.1)

    def test_rolling_caption_variants_do_not_fill_all_five_slots(self):
        texts = [
            "we can build",
            "we can build this",
            "we can build this safely",
            "First useful thought",
            "Another separate lesson",
            "New practical example",
            "Different closing point",
            "Final takeaway now",
        ]
        cues = [
            {"start": 20.0 + index * 2, "end": 21.8 + index * 2, "text": text}
            for index, text in enumerate(texts)
        ]
        selected = MODULE.select_contiguous_cues(cues, 100, mode="native")
        self.assertEqual(len(selected), 5)
        self.assertEqual(selected[0]["text"], "we can build")
        self.assertNotIn("we can build this", [item["text"] for item in selected])
        self.assertNotIn("we can build this safely", [item["text"] for item in selected])

    def test_script_mode_regression_skips_unrenderable_long_cue(self):
        cues = [
            {"start": 20, "end": 22, "text": "x" * 61},
            {"start": 23, "end": 25, "text": "First useful point"},
            {"start": 26, "end": 28, "text": "Another concrete lesson"},
            {"start": 29, "end": 31, "text": "Fresh practical example"},
            {"start": 32, "end": 34, "text": "Different closing thought"},
            {"start": 35, "end": 37, "text": "Final takeaway now"},
        ]
        selected = MODULE.select_contiguous_cues(cues, 100, mode="script")
        self.assertEqual(len(selected), 5)
        self.assertTrue(all(len(item["text"]) <= 60 for item in selected))

    def test_centered_multiline_band_uses_pixel_union(self):
        frame = Image.new("RGB", (640, 360), "#080808")
        for y0, y1 in ((120, 150), (180, 210), (245, 280)):
            for y in range(y0, y1):
                for x in range(90, 550):
                    if (x // 8 + y // 5) % 3 == 0:
                        frame.putpixel((x, y), (245, 245, 245))
        with mock.patch.object(MODULE.RENDERER, "grab_frame", return_value=frame):
            band = MODULE.estimate_native_band("unused.mp4", [1, 2, 3, 4, 5])
        self.assertEqual(band["method"], "multi_frame_native_pixel_union")
        self.assertLess(band["top"], 0.34)
        self.assertGreater(band["bottom"], 0.80)
        self.assertEqual(
            MODULE.RENDERER.classify_native_layout(band["top"], band["bottom"]),
            "centered-band",
        )

    def test_bottom_band_is_classified_without_manual_layout(self):
        frame = Image.new("RGB", (640, 360), "#080808")
        for y in range(298, 330):
            for x in range(140, 500):
                if (x // 8 + y // 5) % 3 == 0:
                    frame.putpixel((x, y), (245, 245, 245))
        with mock.patch.object(MODULE.RENDERER, "grab_frame", return_value=frame):
            band = MODULE.estimate_native_band("unused.mp4", [1, 2, 3, 4, 5])
        self.assertEqual(
            MODULE.RENDERER.classify_native_layout(band["top"], band["bottom"]),
            "bottom-band",
        )


if __name__ == "__main__":
    unittest.main()
