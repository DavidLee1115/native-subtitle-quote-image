# Phase 4A.2 Final Artifact Contract & Closed-loop QA

## Verdict

**PASS**. Phase 4A.1 的 15 个 tentative candidates 全部产出可发布成品：结构化 pipeline QA 为 15/15 PASS，逐张原分辨率人工复核为 15/15 PASS。旧的 5 个 final artifact failures 全部修复。Phase 3 五类泛化回归为 5/5 PASS。

Phase 4A ranking 保持冻结：`artifact-sha256.json` 中 60 个条目全部匹配，configuration SHA-256 仍为 `413275ab1a15214fb8caa5ef3ee4397b8963dc1fb249028c3f5ea9898077828d`。没有改 scoring weights、cold/growth/mature profiles 或 diversity 权重。

## 15-item remediation

| 集合 | 4A.1 | 4A.2 | 修复结果 |
|---|---:|---:|---|
| `5p248yoa3oE` | 5/5 | 5/5 | `c12`/`c18` 转 contain，其余原布局通过 |
| `CBYhVcO4WgI` | 2/5 | 5/5 | `c20`/`c24`/`c05` 严重裁切修复；`c23` 也按同一规则保留完整 hero |
| `nsFkjRWjNxs` | 3/5 | 5/5 | `c22` 4 条、`c20` 1 条动态 subtitle band 自动修复 |

详细 15 项表在 `benchmark/phase4a2/remediation-matrix.md` 和 `remediation-matrix.json`。每个 native candidate 都要求 4/4 final strips 通过，本轮合计 20/20。`c15` 和 `c14` 的 quote-first 回归为 2/2 PASS。

## Tentative vs final selection

3 个素材的 tentative final 5 与 final publishable 5 完全相同，15 个 slot 均在同候选 repair 阶段前或阶段内通过。因此 replacement count 为 0，ranked reserve pool 未被访问。这符合“只有 repair exhaustion 后才允许晋升 reserve”的约束。对照数据在 `benchmark/phase4a2/selection-comparison.json`。

## Closed-loop audit

每个 tentative candidate 的 original candidate、failure reason、repairs attempted、final status 和 promoted candidate 字段都写入 `benchmark/phase4a2/candidate-finalization-audit.jsonl`。`replacement-audit.json` 明确记录 0 replacements。

闭环接口强制 `layout_crop → semantic_window → legal_fallback → UNRENDERABLE → reserve promotion`。自动测试覆盖 repair exhaustion 和 reserve promotion；晋升必须同时通过 semantic alignment、duplicate/cluster、set diversity 和 visual viability，缺失任一检查即 fail-closed。

## Phase 3 regression

| 类别 | ID | 布局 | 结果 |
|---|---|---|---|
| bottom native | `Jb_FEXwUmq8` | bottom-band | PASS |
| centered native | `nIwU-9ZTTJc` | centered-band | PASS |
| B-roll | `ydj-gpaRgh8` | low-visual-fallback | PASS |
| script subtitle | `RhaepLsP5eg` | contain | PASS |
| low-visual / quote-first | `t3JlTcbv80o` | quote-first | PASS |

这 5 张也都以原分辨率打开复核。机器可读结果在 `benchmark/phase4a2/phase3-regression-matrix.json`。

## Automated verification

`python3 -m unittest discover -s tests -v` 通过 74 项。新增或显式回归覆盖 post-crop blank slide、severe crop、valid script hero、missing native subtitle strip、valid native strip、大字连通组件、彩色原生字幕、repair exhaustion 和 automatic reserve promotion。`python3 scripts/validate_repo.py`、`py_compile` 和 `git diff --check` 均 PASS。

本阶段到此停止：未进入 publishing layer，未创建 upstream PR。
