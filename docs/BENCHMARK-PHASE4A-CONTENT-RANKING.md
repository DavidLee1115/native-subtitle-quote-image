# Phase 4A Content Ranking benchmark

2026-09-12 完成。三条完整长视频各生成25个候选，再使用冻结的评分、聚类、去重和视觉规则选出5个。共75候选、15个最终主题、375次真实取帧测量。结果是选题 benchmark，不是发布验收或流量实测。

## 输入与 cold 结果

| 视频 | 来源与时长 | 全片 cues | 候选 / final | 全池 viable / quote-first | final viable / quote-first |
| --- | --- | ---: | ---: | ---: | ---: |
| nsFkjRWjNxs | 凱特玩地球，43:24 | 1536 | 25 / 5 | 6 / 19 | 3 / 2 |
| CBYhVcO4WgI | Stanford 创业课，43:53 | 1274 | 25 / 5 | 15 / 10 | 5 / 0 |
| 5p248yoa3oE | Andrew Ng: Opportunities in AI（2023），36:54 | 798 | 25 / 5 | 25 / 0 | 5 / 0 |

后两条在本轮锁定后取得完整视频与人工英文字幕，未用于此前 ranking 调参。来源选择不依据排名结果。第一条复用本地完整视频与人工中文字幕。所有 source、transcript 和配置均有 SHA256。

## 已实现

- 完整 transcript → Codex 语义提名和逐维判断 → 20–30候选 → 全池窗口视觉评分 → 聚类/重复惩罚 → final5。语义阶段通过已记录的 agent 协议自动执行，确定性Python引擎消费其结构化产物；没有新增付费模型API依赖，也没有将词频规则冒充语义理解。
- Hook、Standalone、Conflict、Emotion、Novelty、Specificity、Shareability、Account fit、Visual viability、Evidence integrity，每维均有0–5分及reason。
- cold / growth / mature 三种权重；三条视频均以cold作为主结果，同一输入的growth/mature对照另存。
- 连续cue原文核对、上下文审核与未知visual fail closed。Evidence integrity不能被高分抵消。Andrew Ng的c09因疑似否定词字幕错误被拒绝，未擅自修正原话。
- 一cluster最多一条，加跨cluster语义重复关系和词面重复补充检查；输出逐轮选择记录、全部淘汰项与前十高分淘汰项。
- Phase 3仅复用取帧与现有像素gate；全池各窗口5个均匀采样，先测后选。质量接近时优先视觉。

Account fit采用明确的第一版假设：面向陌生成年用户的认知启发、个人成长与学习型知识摘录。它表示受众相关性，不表示认同讲者观点。

## Final 5

| 视频 | 排名顺序中的五个主题 |
| --- | --- |
| nsFkjRWjNxs | 不打魔王的支线体验；只有一条命时的选择；归零后的家庭重聚；游戏等级与当下能力；余额一两百时的珍贵支持 |
| CBYhVcO4WgI | 老板处理的是最少让谁失望；不创业也能创造大影响；世界需要与个人适合是两道条件；困难项目可能更容易获得支持；领导责任包括别人的机会成本 |
| 5p248yoa3oE | 手电筒应用与长期壁垒；部分应用从一年到一周；高薪任务也暴露于自动化；五百万美元披萨项目的经济账；不懂恋爱的AI专家找搭档 |

这些名称是内部topic标签，不是发布标题。原引文、精确quote span、窗口、各维理由及主张归属见各视频报告。旧演讲中的“现在”、预测和商业数字均保留历史/讲者归属，不宣称为当下事实。

## 多样性和限制

三条均通过5个不同认知角度的检查。ns的细角度具有推进，但母主题较集中：3/5共享游戏隐喻，2/5共享经济匮乏重估。独立代理审阅确认不是五次复述同一句，同时明确记录c20/c22与c14/c15的近邻关系，未看到结果后调分。

全部375帧解码成功，unknown=0。`viable`只表示在5个采样帧中找到通过现有gate的帧。0/5通过不证明整个窗口不可用；`quote_first`是测量后的fallback建议，本轮未验证它能产出合格成品。ns最终仍有2条此类候选，不能称全部可直接生产。

评分和聚类是模型编辑判断，尚未校准真实陌生用户停留/分享效果。只读复核发现ns c05的Standalone=4可能偏乐观；两池Conflict高分较多但可回到原话中的实际张力。保留这些偏差，不用本轮结果反向调参。新一轮代理提名可能变化；已保存assessment的排名重放是确定性的。

## 对照证据

`benchmark/phase4a/nsFkjRWjNxs/comparison/`保留：

- upstream原manifest、选句记录及历史证据文档；
- `known-creao-selection.json`：历史主题级对应；原始quote与精确时间点缺失，明确为null/unknown；
- `phase4-selection.json`：本轮完整选择。

尚未进行blind comparison，因此不宣称Phase4优于upstream或CREAO。CREAO主题级证据不能代替其精确引文恢复。

## 验证与交付

55项测试通过，含10项ranking边界/硬约束/去重/视觉偏好测试及3项visual adapter测试。完整55项也涵盖既有视觉回归，测试渲染仅在临时目录使用合成测试数据；未渲染benchmark成品。真实normalization CLI与benchmark transcript逐字节一致；75/75原文cue绑定验证通过；cold重放一致。16个冻结文件SHA256均未变化。

实现协议：[PHASE4A-CONTENT-RANKING.md](PHASE4A-CONTENT-RANKING.md)。

本地完整交付目录：`benchmark/phase4a/`（沿用仓库ignored benchmark约定，不把视频/字幕提交到仓库）。

每视频目录包含 `ranking-candidates.json`、`final-selection.json`、`scoring-table.md`、`selection-review.md`、`semantic-diversity.json`、`visual-viability.json`、完整transcript及agent assessment。总表为 `benchmark-results.json`，三profile对照为 `profile-comparison.json`，冻结配置为 `locked-ranking-policy.json`。

Phase 1–3源码和视觉逻辑无改动；未修改Hermes、Kinocut或OpenClaw；未产生发布文案/hashtags/时间安排，未创建upstream PR。Phase 4A benchmark完成后停止。
