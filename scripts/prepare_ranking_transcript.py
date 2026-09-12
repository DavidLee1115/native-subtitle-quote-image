#!/usr/bin/env python3
"""Bind a complete manual VTT to stable cue ids for agent content assessment."""
import argparse
import hashlib
import json
from pathlib import Path

from auto_select_timeline import parse_vtt


def prepare(source, video_id, subtitle_type='manual'):
    raw = source.read_bytes()
    # Rolling auto-captions require a separate audited normalization policy.
    # Preserve the existing parser and all source wording, including errors.
    if b'<c>' in raw or b'<c.' in raw:
        raise ValueError('Rolling auto captions need audited normalization; use an available manual track.')
    cues = [dict(id=f'q{i:04}', **cue) for i, cue in enumerate(parse_vtt(source))]
    if not cues:
        raise ValueError('No timestamped cues found')
    return {'video_id': video_id, 'source_path': str(source.resolve()),
            'source_sha256': hashlib.sha256(raw).hexdigest(),
            'subtitle_type': subtitle_type, 'cues': cues}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vtt', type=Path, required=True)
    parser.add_argument('--video-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.vtt, args.video_id)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'transcript.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (args.output / 'transcript-readable.txt').write_text('\n'.join(
        f'{c["id"]} {c["start"]:.2f}-{c["end"]:.2f} {c["text"]}' for c in result['cues']))
    print(json.dumps({'video_id': args.video_id, 'cue_count': len(result['cues'])}))


if __name__ == '__main__':
    main()
