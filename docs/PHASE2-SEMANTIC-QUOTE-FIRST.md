# Phase 2: semantic windows and quote-first fallback

## Scope

Phase 2 keeps the existing native `render` command and Phase 1 visual gate. It changes the default hero decision so visual attractiveness cannot override semantic provenance. Script-subtitle rendering and publishing are unchanged.

## Pipeline changes

The native pipeline now applies these nodes in order:

1. **Semantic window construction**
   - exact first-quote timestamp;
   - same-line offsets at `+/-0.8s` and `+/-1.5s`;
   - same-theme window, defaulting to the full quote span plus 30 seconds on each side;
   - same speaking shot only when the manifest explicitly supplies `speaking_window`.
2. **Semantic gate**
   - rejects `hero_candidates` outside `theme_window` before decoding;
   - rejects the speaking-shot layer when its window is not explicitly declared;
   - does not scan the rest of the video by default.
3. **Visual gate inside each eligible layer**
   - retains Phase 1 dark-frame, entropy, luminance, edge, score, and final-diversity checks;
   - logs the semantic basis and window with every visual candidate.
4. **Quote-first fallback**
   - activates after all eligible semantic candidates remain low-value;
   - detects the dense native subtitle pixels in the original band, enlarges those pixels as the hero, and keeps the four following native strips;
   - performs no OCR, font rendering, or subtitle rewriting.
5. **QA aggregation**
   - writes per-image and top-level `semantic_alignment`;
   - combines dimensions, subtitle retention, visual strategy, and semantic alignment into the final result.

## Semantic alignment states

| State | Automatic evidence |
|---|---|
| `PASS` | exact quote timestamp, same-line window, same-theme window, explicitly declared speaking window, or quote-first built from the exact quote timestamp |
| `PARTIAL_PASS` | explicit `--allow-source-wide-fallback`; the source video matches, but topic correspondence is not proven |
| `FAIL` | no eligible semantic hero and quote-first is disabled by a forced layout, or a candidate lies outside its declared window |

Time proximity alone is not labeled a same speaking shot. The renderer only enables that layer when `images[].speaking_window` is present. `theme_window` and its alias `semantic_window` are optional; if omitted, the bounded default is derived from `times`.

## Compatibility

- Existing manifests containing only `title` and `times` remain valid.
- Existing `hero_candidates` remain valid, but are now rejected if outside the theme window.
- Existing native layout choices remain valid; `quote-first` is added.
- Phase 1 source-wide selection remains available only through `--allow-source-wide-fallback` and cannot exceed `PARTIAL_PASS`.
- `--visual-gate off` and `render-script` retain their previous entry points.

## Decision log additions

`render-decisions.jsonl` now includes:

- `semantic_gate` for candidates or layers rejected before visual selection;
- `semantic_window` and `semantic_alignment` on visual candidate events;
- `semantic_window_exhausted` when all eligible candidates fail;
- `quote_first_switch` when the layout becomes quote-first;
- the existing `auto_repair`, `layout`, and `qa` events.

## Code changes

- `skills/native-subtitle-quote-image/scripts/native_subtitle_stitch.py`
  - semantic window validation and ordered candidate construction;
  - opt-in source-wide compatibility path;
  - native-pixel quote detection and quote-first composition;
  - semantic QA fields and expanded structured logs.
- `tests/test_native_subtitle_stitch.py`
  - verifies default source-wide exclusion, candidate order and bounds, explicit semantic rejection, quote-first native-pixel rendering, and QA serialization.
- `README.md`, `skills/native-subtitle-quote-image/SKILL.md`, and `references/visual-style.md`
  - document the new default and compatibility switch.

