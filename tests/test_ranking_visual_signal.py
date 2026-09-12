import importlib.util
from pathlib import Path
import unittest
from PIL import Image

PATH = Path(__file__).resolve().parents[1] / 'scripts/ranking_visual_signal.py'
spec = importlib.util.spec_from_file_location('ranking_visual_signal', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class VisualSignalTests(unittest.TestCase):
    def test_samples_stay_inside_window_and_avoid_eof(self):
        times = module.sample_times({'start': 8, 'end': 10}, 10)
        self.assertEqual(len(times), 5)
        self.assertTrue(all(8 < t < 10 for t in times))
        for bad in ({'start': -1, 'end': 10}, {'start': 3, 'end': 11},
                    {'start': 2, 'end': 2}, {'start': 0, 'end': float('nan')}):
            with self.assertRaises(ValueError):
                module.sample_times(bad, 10)

    def test_black_frames_use_frozen_gate_and_do_not_claim_rendered_fallback(self):
        layer = module.load_visual_layer()
        layer.grab_frame = lambda *_: Image.new('RGB', (320, 180), 'black')
        result = module.measure_candidate('unused', {'id': 'a', 'semantic_window': {'start': 0, 'end': 10}}, 10, layer)
        self.assertEqual(result['status'], 'quote_first')
        self.assertFalse(result['fallback_production_verified'])
        self.assertTrue(all('dark_or_empty' in x['metrics']['reasons'] for x in result['evidence']))

    def test_decode_failure_is_unknown_even_if_other_frames_pass(self):
        layer = module.load_visual_layer()
        def grab(*_):
            raise SystemExit('decode failed')
        layer.grab_frame = grab
        result = module.measure_candidate('unused', {'candidate_id': 'a', 'semantic_window': {'start': 0, 'end': 10}}, 10, layer)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['score'], 0)
        self.assertEqual(len(result['evidence']), 5)


if __name__ == '__main__':
    unittest.main()
