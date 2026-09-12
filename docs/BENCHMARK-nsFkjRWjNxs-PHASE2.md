# `nsFkjRWjNxs` Phase 2 benchmark result

## Result

- Overall QA: `PASS`
- Semantic alignment: `PASS`
- Expected / accepted images: `5 / 5`
- Layout: `quote-first` on all five
- Hero time: exact original quote time on all five
- Subtitle horizontal retention: `100%` on all five
- Output size: RGB JPEG `1440 x 1920` on all five
- Contact sheet: RGB JPEG `1440 x 960`
- Source-wide fallback events: `0`
- Logged decisions: 82 visual candidates, 5 undeclared-speaking-window rejections, 5 semantic-window exhaustion events, and 5 quote-first switches

The five local semantic windows remained low-value star-field footage. The renderer did not borrow presenter frames from the distant 2475-2488 second section. It switched each item to a native-pixel quote-first hero at its exact original timestamp, so the semantic relationship is directly proven by timestamp provenance.

## Per-image QA

| # | Topic | Hero time | Layout | Semantic alignment | Subtitle retention | Full-size visual QA |
|---:|---|---:|---|---|---:|---|
| 1 | 你來地球是來玩的 | 574.3 | `quote-first` | `PASS` - exact quote timestamp | 100% | hero quote and all four following strips complete |
| 2 | 你其實是你宇宙的源頭 | 655.8 | `quote-first` | `PASS` - exact quote timestamp | 100% | long hero line remains complete; all strips readable |
| 3 | 你的價值就是你本身 | 910.4 | `quote-first` | `PASS` - exact quote timestamp | 100% | wide punctuation and text remain inside the hero panel |
| 4 | 接納自己才是一切的答案 | 1202.0 | `quote-first` | `PASS` - exact quote timestamp | 100% | hero and four native subtitle units complete |
| 5 | 同頻相吸豐盛創造豐盛 | 1506.3 | `quote-first` | `PASS` - exact quote timestamp | 100% | no distant presenter substitution; all text complete |

Human inspection was performed on the contact sheet and all five full-size outputs. The star field remains as genuine source imagery behind the subtitle strips and as a subdued source-derived backdrop, but it is no longer accepted as the hero subject. The enlarged native quote is the visual subject on every image.

## Phase 1 comparison

| Check | Phase 1 | Phase 2 |
|---|---|---|
| Overall QA | `PARTIAL_PASS` | `PASS` |
| Semantic alignment | not serialized; manual review required | `PASS` serialized per image and at top level |
| Hero source | distant presenter frames at 2475.957-2487.957s | exact quote timestamps |
| Default source-wide scan | yes | no; explicit compatibility flag only |
| Final hero treatment | 5 presenter replacements | 5 quote-first native-pixel heroes |
| Main subtitle clipping | absent | absent |
| Pure star field accepted as hero | no, replaced with distant person | no, converted to quote-first |
| Decision evidence | 289 visual decisions plus repair/layout/QA | 82 local visual decisions plus semantic rejection/exhaustion/switch events |

## Reproduction

```bash
python3 skills/native-subtitle-quote-image/scripts/native_subtitle_stitch.py render \
  VIDEO \
  --manifest MANIFEST.json \
  --out-dir benchmark/nsFkjRWjNxs/output-phase2 \
  --band-top 0.38 --band-bottom 0.62
```

Artifacts are kept outside version control under `benchmark/nsFkjRWjNxs/output-phase2/`:

- five final JPEGs;
- `final_contact_sheet.jpg`;
- `qa-results.json`;
- `render-decisions.jsonl`;
- byte-identical manifest copy.
