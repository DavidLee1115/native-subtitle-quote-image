# Phase 4C — Source-text / Generated-strip Collision QA

## Verdict

`PASS`

The Phase 4B Veritasium `rhgwIhB58PA / c19` false acceptance is now caught by an independent final-artifact collision gate. The unchanged problematic render fails on line 3, enters the existing `layout_crop` repair stage, moves only that source-strip sampling position from `0.82` to `0.46`, and passes the same gate on the repaired 1440×1920 artifact. Five script controls pass the new gate; four native controls confirm that native artifacts remain outside this script-only gate. All 82 tests and every required repository check pass with zero skipped tests.

Phase 4B remains `NOT_TRIGGERED`. This phase did not search for reserve-promotion material, alter ranking, publish, create an upstream PR, or create a tag.

## Isolation and frozen baseline

- Baseline tag: `v2.1.1-davidlee.1`
- Baseline commit: `62a978417cccec7d633a6766c0985efc2f1d734c`
- Isolated branch: `enhancement/final-text-collision-qa`
- Isolated worktree: `/Users/davidlee/Documents/ChatGPT/字幕拼图/worktrees/native-subtitle-quote-image-phase4c`
- Ranking configuration SHA-256 before and after: `413275ab1a15214fb8caa5ef3ee4397b8963dc1fb249028c3f5ea9898077828d`
- Ranking implementation hashes, scoring weights, `cold` / `growth` / `mature` profiles, near-quality rule, redundancy penalty, lexical threshold, cluster policy, diversity behavior, and final count are identical before and after.
- The sealed tag, Phase 4A.2 branch/worktree, and Phase 4B branch/worktree were not modified.

The complete before/after values and comparison are in `benchmark/phase4c/ranking-freeze-before.json`, `ranking-freeze-after.json`, and `ranking-freeze-check.json`.

## Gate design

`final_render_qa.py` now exposes a script-specific source-text collision assessment. For each script line, the renderer keeps the source-only pixels before drawing, records the exact generated text bounding box and stroke clearance, then checks only the source pixels that occupy that generated-text clearance area.

The detector uses local luminance contrast, edges, 8-connected components, compact component geometry, row alignment, horizontal distribution, and glyph-scale consistency. A source frame is not rejected merely because it contains text: text outside the generated clearance passes. Ordinary talking-head texture and non-text object edges also remain below the gate's combined component criteria.

The three responsibilities remain separate:

1. Native subtitle validation checks retained burned-in subtitle pixels and optional cue-timing evidence.
2. Source visual text collision checks source-only pixels against the exact generated script text placement.
3. Generated script placement records the chosen hero text center and per-strip source band center, then redraws and reruns final QA.

The Phase 4B `INVALID_METHOD` experiment is not reused. `native_subtitle_presence()` remains a native-pixel presence detector and is not connected to the script collision verdict.

## Repair behavior

`render-script` first evaluates the user's requested placement unchanged. When collision is detected, the first legal repair scans bounded vertical positions independently for the affected hero or strip, preserving the source timestamp, text, dimensions, ordering, and all ranking inputs. Every attempted position is recorded.

If placement repair does not clear the collision or another final-renderability check still fails, the existing `contain` layout/crop repair runs next. A remaining failure stays a QA failure so the enclosing finalization loop can continue through `semantic_window`, `legal_fallback`, `UNRENDERABLE`, and only then reserve promotion. No reserve was needed for c19.

## Real c19 acceptance

| Check | Result |
| --- | --- |
| Frozen Phase 4B artifact | `problematic-c19-before.jpg`, SHA-256 `c2fd9739ec8a7b47f7d87c50e1b42188f7cef69609d7df3eaa5f1d08048946ac` |
| Exact rerender match | PASS; byte hash equals the Phase 4B artifact |
| New initial gate | FAIL; `collision_line_indexes: [3]` |
| Failure reason | `source_text_overlaps_generated_script_clearance` |
| Repair stage | `layout_crop` |
| Repair action | `adjust-script-strip-placement` |
| Selected strip centers | `[0.82, 0.46, 0.82, 0.82]` |
| Repaired automated QA | PASS; no remaining collision lines |
| Repaired artifact | `problematic-c19-after.jpg`, SHA-256 `dec1faa5c2e4f0ea224d9d34dbd68713e140cd93e42a04bc7571393b734421a4` |
| Original-resolution visual QA | PASS; all four 720×960 lossless quadrants inspected |

The production `render-script` entry point was also run directly against the frozen c19 script. Its sidecar reports `overall: PASS`, `collision_repaired: true`, initial collision `[3]`, final collision `[]`, and the same repaired centers. The exact result is in `c19-cli-after.qa-results.json`; its append-only decisions are in `c19-cli-after.render-decisions.jsonl`.

## Negative controls

Five script-mode controls were rerendered from retained source video and frozen timelines through the new source-only collision gate. Four native-mode controls retain their separate native validation route, because they contain no generated script layer to collide with.

| Control | Coverage | Result |
| --- | --- | --- |
| Veritasium `c12` | script talking-head | PASS |
| BBC `c03` | script document/graphic with safe placement | PASS |
| Vox logo `c07` | script diagram/B-roll | PASS |
| Vox dyslexia `c20` | script diagram/B-roll | PASS |
| Phase 3 `RhaepLsP5eg` | clean script subtitle | PASS |
| Phase 3 `Jb_FEXwUmq8` | talking-head/native bottom | PASS, script gate not applicable |
| Phase 3 `nIwU-9ZTTJc` | native centered | PASS, script gate not applicable |
| Phase 3 `ydj-gpaRgh8` | native B-roll | PASS, script gate not applicable |
| Phase 3 `t3JlTcbv80o` | native quote-first | PASS, script gate not applicable |

Machine-readable per-line metrics, method identifiers, artifact hashes, and routing results are in `benchmark/phase4c/negative-control-results.json`.

## Tests and repository checks

The new automated coverage includes:

- obvious source-text/generated-strip collision → FAIL;
- source text near but outside generated clearance → PASS;
- ordinary talking-head texture → PASS;
- document/slide source with safe placement → PASS;
- repaired placement → PASS;
- short source label inside a generated text area → FAIL;
- missing or incomplete per-line collision evidence → FAIL closed;
- real Veritasium `rhgwIhB58PA / c19` regression → initial FAIL and repaired PASS.

Required final checks:

| Command | Result |
| --- | --- |
| `python3 -m unittest discover -s tests -v` | PASS — 82 tests, 0 skipped |
| `python3 scripts/validate_repo.py` | PASS |
| `python3 -m py_compile ...` for all production Python entry points | PASS |
| `git diff --check` | PASS |

Full command lines, exit codes, logs, and log SHA-256 values are in `benchmark/phase4c/full-regression-results.json` and `benchmark/phase4c/full-regression/`.

## Evidence index

- Before artifact: `benchmark/phase4c/problematic-c19-before.jpg`
- After artifact: `benchmark/phase4c/problematic-c19-after.jpg`
- Collision QA result: `benchmark/phase4c/c19-collision-qa-result.json`
- Closed-loop repair log: `benchmark/phase4c/c19-repair-decision-log.jsonl`
- Production CLI QA sidecar: `benchmark/phase4c/c19-cli-after.qa-results.json`
- Production CLI decision log: `benchmark/phase4c/c19-cli-after.render-decisions.jsonl`
- Original-resolution review: `benchmark/phase4c/original-resolution-review.json`
- Original-resolution quadrants: `benchmark/phase4c/original-resolution-qa/c19-before/` and `c19-after/`
- Negative controls: `benchmark/phase4c/negative-control-results.json`
- Full regression: `benchmark/phase4c/full-regression-results.json`
- Frozen invariants: `benchmark/phase4c/ranking-freeze-check.json`

## Stop condition

Phase 4C is complete and stops at `PASS`. No publishing layer, upstream PR, release tag, or further reserve-promotion search was started. Formal sealing as `v2.1.1-davidlee.2` remains pending review.
