#!/usr/bin/env python3
"""Read-only Phase 3 frame measurements for Phase 4 candidates; never renders."""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / 'skills/native-subtitle-quote-image/scripts/native_subtitle_stitch.py'


def load_visual_layer():
    spec = importlib.util.spec_from_file_location('frozen_visual_layer', RENDERER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_times(window, duration):
    start, end = float(window['start']), float(window['end'])
    if not all(math.isfinite(v) for v in (start, end)) or not 0 <= start < end <= duration:
        raise ValueError('semantic_window must satisfy 0 <= start < end <= video duration')
    # Five bin centers: bounded, uniform and never exactly at the decode EOF.
    return [round(start + (end - start) * (i + .5) / 5, 6) for i in range(5)]


def measure_candidate(video, candidate, duration, layer):
    window = candidate['semantic_window']
    evidence = []
    for t in sample_times(window, duration):
        try:
            frame = layer.grab_frame(video, t)
            metrics = layer.public_assessment(layer.visual_assessment(frame))
            evidence.append({'timestamp': t, 'frame_size': list(frame.size),
                             'frame_rgb_sha256': hashlib.sha256(frame.tobytes()).hexdigest(),
                             'metrics': metrics})
        except (Exception, SystemExit) as exc:
            evidence.append({'timestamp': t, 'error': str(exc)})
    measured = [e for e in evidence if 'metrics' in e]
    passed = [e for e in measured if e['metrics']['passed']]
    if len(measured) != 5:
        status, score = 'unknown', 0.0
        reason = f'Only {len(measured)}/5 frames decoded; viability remains unknown.'
    elif passed:
        status = 'viable'
        score = round(5 * (.7 * max(e['metrics']['score'] for e in passed) + .3 * len(passed)/5), 3)
        reason = f'{len(passed)}/5 uniform semantic-window frames pass the frozen Phase 3 visual gate.'
    else:
        # Legal fallback indication only, not a rendered/QA-approved product claim.
        status = 'quote_first'
        score = round(min(1.5, max(e['metrics']['score'] for e in measured) * 1.5), 3)
        reasons = sorted({r for e in measured for r in e['metrics']['reasons']})
        reason = '0/5 frames pass; quote-first fallback would be required. Gate reasons: ' + ', '.join(reasons)
    return {'candidate_id': candidate.get('candidate_id', candidate.get('id')), 'semantic_window': window,
            'status': status, 'score': score, 'reason': reason, 'evidence': evidence,
            'fallback_production_verified': False,
            'limitation': 'Pixel-statistic viability signal, not human semantic/face/subtitle QA; five samples do not exhaust all window frames.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--candidates', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    layer = load_visual_layer()
    width, height, duration = layer.video_metadata(args.video)
    data = json.loads(args.candidates.read_text())
    candidates = data['candidates'] if isinstance(data, dict) else data
    ids = [c.get('candidate_id', c.get('id')) for c in candidates]
    if any(not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('candidate_id must be unique')
    results = [measure_candidate(args.video, c, duration, layer) for c in candidates]
    report = {'schema_version': 'phase4a-visual-v1', 'video': str(args.video.resolve()),
              'video_size': [width, height], 'video_duration': duration,
              'renderer_sha256': hashlib.sha256(RENDERER.read_bytes()).hexdigest(),
              'adapter_policy': 'five uniform bin centers per semantic window; existing grab_frame + visual_assessment + public_assessment; no render/layout calls',
              'score_formula': 'viable: 5*(0.7*best_passing_gate_score + 0.3*passing_fraction); quote_first: min(1.5, 1.5*best_gate_score); unknown: 0',
              'candidates': results}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'output': str(args.out), 'candidates': len(results),
                      'statuses': {s: sum(r['status']==s for r in results) for s in ['viable','quote_first','unknown']}}))


if __name__ == '__main__':
    main()
