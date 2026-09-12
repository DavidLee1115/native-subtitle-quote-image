# Phase 1: adaptive native layout and repairable visual QA

## Traceability and scope

- Upstream: `chengyi-ai/native-subtitle-quote-image`
- Upstream base commit: `f9485e20f03fc0b9e5dfd77d03d5be24f7cebdcd`
- Fork: `DavidLee1115/native-subtitle-quote-image`
- Isolated branch: `enhancement/adaptive-visual-qa`
- The existing `sample`, `band`, `render`, and `render-script` commands remain available.
- The change is local Python/Pillow/FFmpeg logic. It does not use Hermes, Kinocut, OpenClaw, OCR, a vision API, or another external service.

## Pipeline changes

```text
manifest validation
  -> subtitle-band position classification (bottom / centered)
  -> hero visual gate
       original frame
       -> same-line nearby frames (-/+0.8 s, -/+1.5 s)
       -> explicit same-theme candidates and bounded local offsets
       -> sparse source-wide candidates
  -> near-duplicate gate against already accepted final heroes
  -> adaptive native layout
       bottom-band
       centered-band
       low-visual-fallback
       -> preserve-band if geometry QA fails
  -> render
  -> technical, subtitle-retention, and visual QA
  -> accepted image enters final contact sheet; unresolved failure stays out
```

### Adaptive native layouts

`auto` classifies the configured subtitle band by its vertical center. A band centered at or below `0.68` uses `bottom-band`; a higher band uses `centered-band`. When the semantic frame fails the visual gate and another frame supplies the visual hero, the recorded layout is `low-visual-fallback`.

All adaptive layouts separate the visual hero from the first native subtitle band. The visual area may be cropped for the portrait canvas, while the complete source-width subtitle band is resized without horizontal cropping and attached to the hero. This keeps native subtitle pixels intact and makes horizontal retention measurable. `preserve-band` is the final layout downgrade when post-render geometry reports less than 98% horizontal retention.

`--native-layout legacy` starts with the upstream crop. Post-render QA can still downgrade it to `preserve-band`. `--visual-gate off` is the explicit compatibility route when callers need rendering without automatic visual selection.

### Visual gate

The gate uses a 160 x 90 thumbnail and records these explainable metrics:

- grayscale entropy;
- luminance standard deviation;
- edge mean;
- average saturation;
- a skin-tone-like pixel ratio used only to prioritize likely presenter frames;
- ratio of pixels darker than 32/255;
- a weighted visual score.

A candidate is rejected when it is very dark, has low entropy, has low luminance variation, or misses the score threshold. Accepted heroes are also compared with earlier final heroes using normalized mean absolute pixel difference; near duplicates are rejected.

The gate is deliberately a low-cost heuristic. It detects the benchmark's star-field failure without claiming face detection, semantic understanding, or aesthetic certainty. When a coarse source-wide candidate has enough skin-tone-like pixels, the scanner checks a bounded set of nearby frames and ranks those likely presenter frames ahead of game or player UI. The low-visual fallback uses the upper 60% of that visual source before portrait fitting, which removes the candidate frame's unrelated lower subtitle or player-control region. It estimates horizontal subject position from the largest upper-frame skin-tone component that does not touch a side edge, then centers the portrait crop on that component. The intended native subtitle band is appended from the original semantic timestamp.

### Repair order and QA meaning

The renderer attempts repairs in a fixed, bounded order and logs every candidate:

1. keep the original hero if it passes;
2. replace only the visual hero with a nearby frame from the same line;
3. try `images[].hero_candidates`, then bounded offsets of 4, 10, 20, and 30 seconds for the same topic region;
4. try a sparse source-wide pool;
5. downgrade layout when subtitle retention is below 98%;
6. report `FAIL` only when no candidate or layout passes.

The original first timestamp still supplies the native subtitle band even when the visual hero changes. A source-wide fallback is accepted as `PARTIAL_PASS`: visual and subtitle QA passed, but the semantic relationship between the replacement hero and the topic still requires human confirmation.

## Outputs and logs

Native `render` now writes two additional files beside the existing images, manifest copy, and contact sheet:

- `qa-results.json`: per-image result, original and selected hero timestamps, repair phase, layout, subtitle retention, visual metrics, and explanation;
- `render-decisions.jsonl`: one event per candidate, repair, layout decision, and final QA result.

Only `PASS` and `PARTIAL_PASS` images enter `final_contact_sheet.jpg`. A candidate that remains `FAIL` is not included in the final contact sheet, and the command exits non-zero after writing QA evidence.

## Code change list

- `skills/native-subtitle-quote-image/scripts/native_subtitle_stitch.py`
  - added layout classification and subtitle-preserving hero composition;
  - added lightweight visual scoring and final-set diversity comparison;
  - added bounded repair candidate selection;
  - added structured decision logging and generated QA results;
  - added optional `hero_candidates` to native manifest items;
  - added `--native-layout`, `--visual-gate`, `--global-candidates`, and `--minimum-visual-distance` without removing existing arguments.
- `tests/test_native_subtitle_stitch.py`
  - added coverage for layout classification, low-value rejection, nearby repair, and full-width centered-subtitle preservation;
  - retained the existing CLI flow through the explicit compatibility switch.
- `README.md`, `skills/native-subtitle-quote-image/SKILL.md`, and `skills/native-subtitle-quote-image/references/visual-style.md`
  - document the new automatic gate, repair order, logs, and partial-pass boundary.

## Benchmark command

```bash
python3 skills/native-subtitle-quote-image/scripts/native_subtitle_stitch.py render \
  VIDEO \
  --manifest MANIFEST.json \
  --out-dir OUTPUT_DIR \
  --aspect 3:4 --width 1440 \
  --band-top 0.38 --band-bottom 0.62
```

No benchmark-specific timestamp is hardcoded in the renderer. The original manifest remains valid; `hero_candidates` is optional.
