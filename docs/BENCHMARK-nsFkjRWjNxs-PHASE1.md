# `nsFkjRWjNxs` Phase 1 benchmark result

## Result

- Mode: native subtitle
- Source and original manifest: reused from the upstream `v2.1.1` benchmark
- Enhanced command result: `PARTIAL_PASS`
- Expected / accepted final images: `5 / 5`
- Output size: all five images are RGB JPEG `1440 x 1920`
- Contact sheet: RGB JPEG `1440 x 960`
- Manifest copy: byte-identical to the original manifest
- Automatic repair events: `5`
- Layout decisions: all five `low-visual-fallback`
- Subtitle horizontal retention: `100%` on all five

`PARTIAL_PASS` is intentional. Every original, same-line, and bounded same-topic-region candidate remained a star field, so all five visual heroes came from the same source video's presenter section. Visual, geometry, subtitle, and technical checks passed; automatic semantic proof between those replacement hero frames and each earlier quote is unavailable.

## Upstream comparison

| Check | Upstream `v2.1.1` | Enhanced Phase 1 |
|---|---|---|
| Hero subtitle clipping | `FAIL`, clipped left and right on 5/5 | no clipping on 5/5; full source-width band retained |
| Final visual heroes | star field on 5/5 | presenter on 5/5 |
| Pure-star final set | yes | no; star field remains only behind the genuine quote strips |
| Visual gate | none before final selection | 289 logged candidate decisions; dark/low-information and near-duplicate frames rejected |
| Automatic repair | none | 5 source-wide hero replacements after nearby and topic-region attempts failed |
| QA result | `FAIL` | explainable `PARTIAL_PASS` |
| QA evidence | separate manually created report | generated `qa-results.json` plus `render-decisions.jsonl` |

## Selected heroes and per-image QA

| # | Topic | Original subtitle time | Visual hero time | Automatic QA | Human full-size QA |
|---:|---|---:|---:|---|---|
| 1 | 你來地球是來玩的 | 574.3 | 2487.957 | `PARTIAL_PASS`; visual score 0.9412; subtitle retention 1.0 | face complete and centered; intended first subtitle and four strips complete; no unrelated overlay |
| 2 | 你其實是你宇宙的源頭 | 655.8 | 2475.957 | `PARTIAL_PASS`; visual score 0.9340; subtitle retention 1.0 | face complete and centered; all five native subtitle units complete; no unrelated overlay |
| 3 | 你的價值就是你本身 | 910.4 | 2478.957 | `PARTIAL_PASS`; visual score 0.9180; subtitle retention 1.0 | face complete; mild motion softness remains; all subtitle units complete |
| 4 | 接納自己才是一切的答案 | 1202.0 | 2484.957 | `PARTIAL_PASS`; visual score 0.9355; subtitle retention 1.0 | face complete and clear; all subtitle units complete; no unrelated overlay |
| 5 | 同頻相吸豐盛創造豐盛 | 1506.3 | 2481.957 | `PARTIAL_PASS`; visual score 0.9322; subtitle retention 1.0 | face complete with downward gaze; all subtitle units complete; no unrelated overlay |

The human notes do not upgrade the automatic result. Items 3 and 5 are usable for this minimal benchmark but leave expression selection as a future refinement; the Phase 1 goals of complete native subtitles, removal of the all-star final set, and failure-driven automatic repair are met.

## Output files

Generated evidence is kept outside version control under:

```text
benchmark/nsFkjRWjNxs/output-enhanced/
  01_你來地球是來玩的.jpg
  02_你其實是你宇宙的源頭.jpg
  03_你的價值就是你本身.jpg
  04_接納自己才是一切的答案.jpg
  05_同頻相吸豐盛創造豐盛.jpg
  final_contact_sheet.jpg
  原生字幕时间点.json
  qa-results.json
  render-decisions.jsonl
```

## Verification

```text
python3 -m unittest discover -s tests -v
27 tests: PASS

python3 scripts/validate_repo.py
Repository is valid: native-subtitle-quote-image v2.1.1

git diff --check
PASS
```

The installed Skill renderer and the clean upstream checkout both retain SHA-256:

```text
61423e76469d1fe8633c8565cb05d90bc91e51139387e580fcf20e2989efe0b4
```

The enhanced renderer exists only in the isolated worktree branch.
