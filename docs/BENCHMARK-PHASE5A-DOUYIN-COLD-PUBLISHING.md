# Phase 5A — Douyin Cold-start Publishing Benchmark

## Verdict

**PASS**.

`nsFkjRWjNxs` 的 frozen final 5 已形成一套可直接交给人工发布的新抖音账号草稿。生成器、结构化 Evidence QA 与逐字编辑复核均通过：12 个 substantive claims 为 `supported`，7 个账号/包装判断明确标为 `inferred`，没有 `unsupported` claim。所选 hook、标题和正文各自承担不同功能，没有机械复读图片字幕。

本结论不是“脚本退出成功”：本轮打开了 5 张 1440×1920 原图和 contact sheet，并逐字阅读 `publishing-package.md`、claim map 与 QA 输出。复核记录在 `benchmark/phase5a/output/manual-publishing-review.json`。

## Frozen baseline and scope

- Branch: `feature/publishing-layer`
- Tag: `v2.1.1-davidlee.2`
- Baseline commit: `c719db908a6b5d9fcacb537cfc8d55f5395801f4`
- Frozen content-ranking configuration: `413275ab1a15214fb8caa5ef3ee4397b8963dc1fb249028c3f5ea9898077828d`
- Platform/profile: `douyin` / `cold`

Phase 5A 只新增发布包、发布评分配置和独立测试。没有修改 `content_ranking.py`、renderer、`final_render_qa.py`、Phase 4 profiles、ranking weights 或 QA thresholds；没有重新渲染、替换候选、登录抖音或发布内容。

## Architecture

- `scripts/publishing_package.py`: 消费 source metadata、完整 transcript、ranked pool、final 5、artifact 与 final QA；输出 JSON、Markdown、claim map 和 publishing QA。任一 final artifact 缺失、QA 非 PASS、SHA-256 不匹配或最终 claim 为 unsupported 时 fail closed。
- `scripts/publishing_scoring.py`: 只比较 hook/title/copy 候选，不导入或修改 Phase 4 ranking 配置。
- `config/publishing-profiles.json`: 独立 `douyin/cold` 权重、penalties、长度范围和拒绝阈值。
- `tests/test_publishing_package.py`、`tests/test_publishing_scoring.py`: 14 个 Phase 5A tests。

Publishing positive dimensions 为 clarity、hook strength、standalone value、conflict、specificity、audience relevance 与 evidence support；penalties 为 cliché、exaggeration 与 diversity。`evidence_support` 只能由 provenance 字段计算，候选自报分数不能覆盖它。

## Benchmark input provenance

来源：[YouTube `nsFkjRWjNxs`](https://www.youtube.com/watch?v=nsFkjRWjNxs)，标题为《#49 違反常理、但有用！你從此再也沒有困境｜如何解決人生問題》，频道为 `凱特玩地球 Kate Sun`。

Phase 5A 复用了 Phase 4A/4A.2 已有文件：1536-cue transcript、25-candidate ranking pool、final selection、final artifact QA、render manifest、selection comparison、人工原图复核矩阵和 5 张 final JPEG。复制后的 SHA-256 与来源文件一致。原视频没有复制进 publishing worktree。

`benchmark/**` 按仓库现有 `.gitignore` 保持为本地 benchmark 证据，不属于 frozen tag 的 Git tree。因此 `publishing-input.json` 同时记录来源 SHA-256；不能只用 tag 代替 artifact provenance。

| Rank | Candidate | Quote time | Topic | Layout | Final QA | Artifact SHA-256 |
|---:|---|---|---|---|---|---|
| 1 | `c22` | 1781.000–1811.466 | 不打魔王的支线体验 | centered-band | PASS | `5eb3447b...d5f657` |
| 2 | `c21` | 1718.533–1733.533 | 只有一条命时的选择 | low-visual-fallback | PASS | `92452c50...d7ad3` |
| 3 | `c15` | 1133.433–1156.666 | 归零后的家庭重聚 | quote-first | PASS | `82ec4f15...7f42c` |
| 4 | `c20` | 1579.366–1625.633 | 游戏等级与当下能力 | centered-band | PASS | `4d08b541...2cf64` |
| 5 | `c14` | 1097.733–1121.466 | 余额一两百时的珍贵支持 | quote-first | PASS | `d1ae7750...41ac` |

`c14` 的金额与家人支持、`c15` 的归零与灵性解释均保持为讲者自述；claim map 的 `source_claim_status` 明确记录其证据边界。

## Publishing package

未提供用户确认的账号定位。系统提出的定位是：

> 面向有进度焦虑的年轻人，用视频原话拆解人生选择、自我成长与低谷重估

该字段状态为 `inferred`，没有伪装成用户确认信息。

Primary angle：

> 人生不是只有升级打怪：支线、归零和当前等级，也会改变主线

这 5 张图没有被包装成原视频摘要。共同主题是：当人生没有按预期推进时，怎样重新评价支线、当下能力和来自家人的支持。游戏等级与魔王提供冲突，归零与家庭支持把抽象类比落到具体经历。

Selected cover hook：

> 为什么有人玩两遍《塞尔达》，却一次都没打魔王？

Selected Douyin title：

> 人生只有一条命，为什么还要允许自己走支线？

Final body copy：

> 我们常把人生想成升级游戏：能力要更强、进度要更快，等到“60 级”才算足够好。可这 5 张图提出的问题是：如果人生只有一条命，真的只能沿着同一条主线往前冲吗？
>
> 第一，当前等级并不等于不够好。素材里的说法是，每个等级都有当下足以应付的事情；只盯着未来，会与此刻的自己脱节。
>
> 第二，支线不是白走。有人把两部《塞尔达》各玩两遍，却从没打魔王；探索、看风景、和朋友聚会，也可能带来让主线更轻松的“道具”和灵感。
>
> 第三，归零会改变衡量价值的尺子。账户只剩一两百台币时，三五百元的支持变得格外具体；搬回家与父母同住，也让讲者重新理解家人互相理解和“丰盛”的含义。
>
> 目标依然可以推进，只是眼前这一步也值得被看见。你现在更需要推进主线，还是允许自己走一段支线？

Hashtags：core `#人生选择 #自我成长`；niche `#进度焦虑 #支线任务`；optional discovery `#塞尔达传说 #人生思考`。

Posting recommendation：`evening`，confidence `0.45`。理由仅是五图信息密度较高、晚间适合完整阅读和讨论；没有真实账号历史或实时抖音流量数据，这只是首轮试发假设。

## Hook candidate ranking

Confidence 是 normalized publishing score，不是流量或转化率预测。

| Rank | Type | Hook | Score | Confidence | Evidence |
|---:|---|---|---:|---:|---|
| 1 | curiosity | 为什么有人玩两遍《塞尔达》，却一次都没打魔王？ | 4.940 | 0.9880 | `c22` |
| 2 | pain-point | 总觉得自己还不够好？别只盯着“60 级” | 4.698 | 0.9396 | `c20` |
| 3 | contradiction | 人生不只剩升级打怪，支线也可能帮到主线 | 4.662 | 0.9324 | `c22,c20` |
| 4 | strong-claim | 现在的你，已经足够应付当前这一关 | 4.522 | 0.9044 | `c20` |
| 5 | identity | 写给总怕落后的人：现在的等级也有它的价值 | 4.164 | 0.8328 | `c20`; `inferred` audience framing |

## Title candidate ranking

| Rank | Title | Score | Evidence | Rationale |
|---:|---|---:|---|---|
| 1 | 人生只有一条命，为什么还要允许自己走支线？ | 4.910 | `c21,c22` | 用有限人生和支线选择构成冲突，覆盖五图共同问题。 |
| 2 | 玩两遍《塞尔达》却不打魔王：人生也可以走支线 | 4.788 | `c22` | 具体行为反差进入主题。 |
| 3 | 总想升到 60 级的人，容易错过当下的自己 | 4.756 | `c20` | 将等级类比对应到当下与未来焦虑。 |
| 4 | 从账户只剩一两百，到重新理解“丰盛” | 4.674 | `c14,c15` | 保留讲者自述语境，不扩写成普遍结论。 |
| 5 | 支线、归零、当下：5 张图重看人生进度 | 4.510 | `c22,c15,c20,c14,c21` | 明确列出三条内容线索。 |

## Evidence and publishing QA

`claim-evidence-map.json` 为每个 claim 保留 candidate ID、精确 quote time、完整 transcript quote、speaker 与 source claim status。结果为 12 `supported`、7 `inferred`、0 `unsupported`。

| Check | Result | Evidence |
|---|---|---|
| Evidence QA | PASS | 无 unsupported claim；讲者自述未升级为独立事实 |
| Hook QA | PASS | c22 直接支持；独立可懂；无夸张 |
| Title QA | PASS | c21+c22 支持；与五图主线一致 |
| Body QA | PASS | 4 个核心观点记录，最后一个明确 inferred；无虚构经历、结论或数据 |
| Redundancy QA | PASS | hook/title lexical similarity 0.1875，阈值 0.72；正文未逐字复制完整图片 quote |
| Cold-start QA | PASS | 不依赖老粉或账号前情 |
| Manual editorial readthrough | PASS | hook、title、body、tags 与 daypart 组成可直接人工发布的完整草稿 |

## Tests and verification

`python3 -m unittest discover -s tests -v`：**96 tests PASS**，0 failed，0 errors，4 个既有 artifact-dependent tests 因当前 worktree 不包含其 ignored fixture 而按既有逻辑 skip。原有 82 tests 全部继续 PASS；Phase 5A 新增 14 tests 全部 PASS。

必需覆盖均通过：supported claim、unsupported fail closed、inferred positioning、exaggerated title rejection、cliché penalty、duplicate diversity penalty、no-positioning fallback 与 cold profile selection。

复现命令：

```bash
python3 scripts/publishing_package.py \
  --input benchmark/phase5a/input/publishing-input.json \
  --output-dir benchmark/phase5a/output \
  --config config/publishing-profiles.json
```

## Deliverables

- `benchmark/phase5a/output/publishing-package.json`
- `benchmark/phase5a/output/publishing-package.md`
- `benchmark/phase5a/output/claim-evidence-map.json`
- `benchmark/phase5a/output/publishing-qa-result.json`
- `benchmark/phase5a/output/manual-publishing-review.json`
- `benchmark/phase5a/output/test-results.json`
- `benchmark/phase5a/output/frozen-integrity.json`
- `benchmark/phase5a/input/publishing-input.json`
- `benchmark/phase5a/artifacts/01_c22.jpg` through `05_c14.jpg`
- `benchmark/phase5a/artifacts/final_contact_sheet.jpg`

Phase 5A 到此停止。未创建 upstream PR、release tag、真实平台发布或其他平台适配。
