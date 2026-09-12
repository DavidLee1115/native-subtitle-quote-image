# Phase 4A.2 final render QA

`skills/native-subtitle-quote-image/scripts/final_render_qa.py` is the
deterministic boundary between ranking and a final 3:4 asset. It does not alter
the Phase 4A ranking weights, profiles, or diversity constants.

## Structured final QA

`assess_final_renderability(source, rendered, source_crop_box=..., hero_box=...,
subject_box=..., source_visual=..., layout_mode=...)` returns:

- `final_renderability.score/passed/reasons`
- `subject_retention`
- `active_content_ratio`
- `excessive_blank_area`
- `crop_safety`, including crop geometry and cropped-slide-text evidence

For a 16:9 script hero, pass the exact-frame `visual_assessment` as
`source_visual`. A failed/document-like source rendered through `fit` fails
closed and can be repaired with the renderer's `contain` + blurred-background
layout. `render-script` performs this repair automatically and writes
`<output-stem>.qa-results.json` plus `<output-stem>.render-decisions.jsonl`.

## Native strip evidence

`assess_native_strips(rendered, hero_height=..., strip_heights=...)` evaluates
every final strip using compact high-contrast text-like components. The
aggregate `all_pass` is true only when all N strips pass.

Native manifests may add `cue_windows`, aligned one-to-one with `times`:

```json
{
  "images": [{
    "title": "candidate-id",
    "times": [10.8, 12.4, 14.2, 16.0, 18.1],
    "cue_windows": [
      {"start": 10.2, "end": 11.1},
      {"start": 12.0, "end": 12.8},
      {"start": 13.8, "end": 14.6},
      {"start": 15.6, "end": 16.4},
      {"start": 17.7, "end": 18.5}
    ]
  }]
}
```

When a fixed band misses a strip, `render` compares the cue frame with
`cue_start - 0.18s`, locates the changing subtitle rows, repairs only the
corresponding `strip_bands` entry at the `layout_crop` stage, re-renders, and
re-runs all-strip presence QA. The temporal measurements and final band are
preserved in `qa-results.json` and `render-decisions.jsonl`.

If the planned time itself still lacks subtitle evidence, the renderer probes
a fixed set of positions inside that same `cue_window`. A successful probe is
recorded as `same-cue-window-time` at the `semantic_window` repair stage. It
never crosses into another cue. Tiny text is downsampled with Lanczos; uniform
black padding around a light subtitle card is removed only when the retained
card is symmetrically padded and demonstrably light. Saturated-color text is
accepted only with matching temporal evidence.

## Closed-loop orchestration

`run_finalization_loop(selected, ranked_reserves, repairs_for=..., render=...,
qa=..., promotion_validate=..., audit_path=...)` enforces this order:

1. initial render and QA;
2. `layout_crop` repairs;
3. `semantic_window` hero/cue repairs;
4. `legal_fallback` repairs;
5. `UNRENDERABLE` after repair exhaustion;
6. ranked reserve validation and promotion.

`promotion_validate(candidate, current_accepted, slot)` must return all four
boolean checks: `semantic_alignment`, `duplicate_cluster`, `set_diversity`, and
`visual_viability`. Missing checks fail closed. Audit events retain the original
candidate, final failure, attempted repairs, every reserve validation, rejection
reason, and promoted candidate.
