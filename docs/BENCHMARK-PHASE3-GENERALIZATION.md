# Phase 3 跨素材泛化盲测

## 结论

`enhancement/adaptive-visual-qa` 在 5 类未参与 Phase 1/2 开发和调参的公开素材上为 **5 PASS / 0 PARTIAL_PASS / 0 FAIL**。选句规则固定为“完整时间轴 20% 之后首组紧凑的 5 个合格 cue”；没有人工选帧、人工改 manifest 或人工指定 layout。

所有原生模式输出的 `subtitle_horizontal_retention` 均为 `1.0`。居中大字幕使用全帧等比保留，主图文字不再被竖版裁宽。普通 talking-head 没有误触发 quote-first；唯一的 quote-first 是 30 分 48 秒静态浅色文本页素材，其语义窗口内所有候选均被统一规则判定为文档/幻灯片或低熵画面。

## 素材和自动方法

| 类别 | 公开素材 | 时长 | 模式 | 自动布局 |
|---|---|---:|---|---|
| talking-head + 底部烧录字幕 | `Jb_FEXwUmq8` | 344s | native | bottom-band |
| talking-head + 居中烧录字幕 | `nIwU-9ZTTJc` | 234s | native | centered-band |
| 人物 + B-roll / 多场景 | `ydj-gpaRgh8` | 222s | native | low-visual-fallback |
| 无烧录字幕，有字幕时间轴 | `RhaepLsP5eg` | 281s | script | script-subtitle |
| 长时间 PPT / 静态背景 | `t3JlTcbv80o` | 1848s | native | quote-first |

每个完整素材先生成 24 帧全片 contact sheet 用于类别锁定，之后由 `scripts/auto_select_timeline.py` 确定性选取时间点。选择器只使用完整 VTT 和本地帧像素，过滤淡出时刻、过长 script cue 和滚动字幕的累积重复。

## Benchmark matrix

| ID | hero 来源 @ timestamp | semantic window | visual gate / repair | quote-first | semantic alignment | subtitle integrity | visual diversity | 最终 QA |
|---|---|---|---|---|---|---|---|---|
| `Jb_FEXwUmq8` | 原句帧 @ 69.484s | 69.484s 精确点 | original 通过，未修复 | 否 | PASS | PASS，横向 1.0 | PASS，均值 0.0589 | **PASS** |
| `nIwU-9ZTTJc` | 原句全帧 @ 50.836s | 50.836s 精确点 | original 通过，未修复 | 否 | PASS | PASS，横向 1.0，主图无截断 | PASS，均值 0.0846 | **PASS** |
| `ydj-gpaRgh8` | 同主题 B-roll @ 56.542s | 16.715–100.178s | 原帧/同句邻帧拒绝；same-theme 修复成功 | 否 | PASS | PASS，横向 1.0 | PASS，均值 0.2597 | **PASS** |
| `RhaepLsP5eg` | 第一个时间轴 cue @ 79.815s | 79.815–95.295s | script 旧路径不运行 native visual gate | 否 | PASS，精确 cue | PASS，绘制完整 | PASS，均值 0.0965 | **PASS** |
| `t3JlTcbv80o` | 原句字幕像素 @ 370.032s | 340.032–420.240s | 17 个候选全部拒绝，修复耗尽后布局降级 | 是 | PASS，精确原句 | PASS，横向 1.0 | 源素材 FAIL，quote-first 消解 | **PASS** |

## 统计

- 标准帧 hero 布局使用率：4/5，80%
- quote-first 使用率：1/5，20%
- false fallback：0
- semantic alignment PASS 率：5/5，100%
- auto-repair 成功率：2/2，100%（一次同主题换帧，一次耗尽后 quote-first 降级）
- native 回归：4/4 PASS
- script 回归：1/1 PASS

## 本轮系统性修复

1. 新增确定性完整时间轴选择器；自动避开 cue 淡出、滚动累积重复和无法排版的过长 script cue。
2. native band 估计增加多帧原生文字像素并集，保留居中多行大字幕；底部孤立字幕簇作为通用兜底。
3. centered-band 主图与字幕条改为等比容纳，不再对已烧录的居中文字裁宽。
4. visual gate 新增浅色文档/幻灯片低视觉价值判定；quote-first 的像素检测同时支持“浅底深字”。
5. `qa-results.json` 新增 `subtitle_integrity` 和 `subtitle_strip_strategy`；既有 semantic 决策日志继续记录候选接受、拒绝、修复耗尽和 quote-first 切换。

## 回归验证

`python3 -m unittest discover -s tests -v` 通过 42 项。新增或显式覆盖：bottom-band 保留、centered-band 带宽和全帧保留、semantic-window constraint、quote-first trigger、quote-first false-positive、auto-repair exhaustion、script-mode cue 可渲染性与原 CLI 集成路径。

## 产物

- 总 contact sheet：`benchmark/phase3-blind/output/phase3-contact-sheet.jpg`
- 机器可读矩阵：`benchmark/phase3-blind/phase3-results.json`
- 每个 native 素材的 `qa-results.json` 和 `render-decisions.jsonl`：`benchmark/phase3-blind/output/<video-id>/`
- script 模式成品：`benchmark/phase3-blind/output/RhaepLsP5eg/script-mode.jpg`

素材和渲染产物保留在本地 ignored benchmark 目录，不进入仓库历史。
