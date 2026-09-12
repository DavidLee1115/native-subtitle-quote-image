#!/usr/bin/env python3
"""从完整 VTT 确定性选出一组连续台词，并为原生模式估计字幕带。"""

import argparse
import html
import importlib.util
import json
import math
import re
from pathlib import Path

from PIL import ImageFilter, ImageStat


ROOT = Path(__file__).resolve().parents[1]
RENDERER_PATH = (
    ROOT
    / "skills"
    / "native-subtitle-quote-image"
    / "scripts"
    / "native_subtitle_stitch.py"
)
SPEC = importlib.util.spec_from_file_location("native_subtitle_stitch", RENDERER_PATH)
RENDERER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RENDERER)

TIMING_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2}\.\d{3})"
)


def timestamp_seconds(value):
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def clean_vtt_text(value):
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def parse_vtt(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    blocks = re.split(r"\r?\n\s*\r?\n", text)
    cues = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next(
            (index for index, line in enumerate(lines) if TIMING_RE.search(line)),
            None,
        )
        if timing_index is None:
            continue
        match = TIMING_RE.search(lines[timing_index])
        start = timestamp_seconds(match.group("start"))
        end = timestamp_seconds(match.group("end"))
        content = clean_vtt_text(" ".join(lines[timing_index + 1 :]))
        if content and end > start:
            cues.append({"start": start, "end": end, "text": content})
    return cues


def cue_frame_time(cue):
    duration = cue["end"] - cue["start"]
    # 用中段而非尾部取帧，避免烧录字幕正在淡出。
    offset = min(duration * 0.55, max(0.25, duration - 0.30))
    return round(cue["start"] + offset, 3)


def text_similarity(left, right):
    """评估滚动字幕的词集重合，阻止同一句的累积版重复入选。"""
    tokenize = lambda value: set(re.findall(r"[\w']+", value.casefold()))
    left_tokens = tokenize(left)
    right_tokens = tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    return max(
        intersection / len(left_tokens | right_tokens),
        intersection / min(len(left_tokens), len(right_tokens)),
    )


def select_contiguous_cues(cues, duration, count=5, mode="native"):
    """选完整时间轴 20% 之后出现的第一组紧凑连续台词。"""
    if count < 2:
        raise SystemExit("--count 必须至少为 2")
    preferred_start = min(max(5.0, duration * 0.20), max(0.0, duration - 1.0))
    preferred_end = max(preferred_start, duration * 0.88)
    # script mode 要在 3:4 窄字幕条中保持最小可读字号。
    maximum_text = 60 if mode == "script" else 180
    eligible = []
    previous_text = None
    previous_time = -math.inf
    for cue in cues:
        cue_duration = cue["end"] - cue["start"]
        frame_time = cue_frame_time(cue)
        text = cue["text"]
        if not (0.45 <= cue_duration <= 16.0 and 3 <= len(text) <= maximum_text):
            continue
        if frame_time < preferred_start or frame_time > preferred_end:
            continue
        normalized = text.casefold()
        if (
            normalized == previous_text
            or (previous_text and text_similarity(normalized, previous_text) >= 0.60)
            or frame_time - previous_time < 1.0
        ):
            continue
        eligible.append({**cue, "time": frame_time})
        previous_text = normalized
        previous_time = frame_time

    for start in range(max(0, len(eligible) - count + 1)):
        window = eligible[start : start + count]
        if len(window) < count:
            break
        gaps = [right["time"] - left["time"] for left, right in zip(window, window[1:])]
        if max(gaps, default=0) <= 14.0 and window[-1]["time"] - window[0]["time"] <= 48.0:
            return window
    raise SystemExit("完整时间轴中没有找到 5 条紧凑且可用的连续台词")


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def merge_row_clusters(rows, maximum_gap):
    if not rows:
        return []
    clusters = [[rows[0]]]
    for row in rows[1:]:
        if row - clusters[-1][-1] <= maximum_gap:
            clusters[-1].append(row)
        else:
            clusters.append([row])
    return clusters


def estimate_native_band(video, times):
    """用多帧横向边缘密度估计文字带，不识别文字内容。"""
    per_frame = []
    credible_native_crops = []
    sample_height = None
    for seconds in times:
        frame = RENDERER.grab_frame(video, seconds)
        _quote, quote_report = RENDERER.detect_native_quote_crop(frame)
        x0, y0, x1, y1 = quote_report["crop"]
        crop_height = (y1 - y0) / frame.height
        if (
            quote_report["detected"]
            and 0.08 <= y0 / frame.height
            and crop_height <= 0.72
            and (x1 - x0) / frame.width >= 0.30
        ):
            credible_native_crops.append((y0 / frame.height, y1 / frame.height))
        sample = frame.convert("L").resize((320, 180))
        edges = sample.filter(ImageFilter.FIND_EDGES)
        row_scores = []
        for y in range(sample.height):
            row = edges.crop((0, y, sample.width, y + 1))
            pixels = [row.getpixel((x, 0)) for x in range(sample.width)]
            dense_edges = sum(value >= 34 for value in pixels) / sample.width
            edge_mean = ImageStat.Stat(row).mean[0] / 255.0
            row_scores.append(dense_edges * 0.72 + edge_mean * 0.28)
        per_frame.append(row_scores)
        sample_height = sample.height

    # 多帧都能找到完整字幕像素时，用并集保留居中多行字幕的全高度。
    union_top = min((value[0] for value in credible_native_crops), default=0.0)
    union_bottom = max((value[1] for value in credible_native_crops), default=1.0)
    if (
        len(credible_native_crops) >= max(3, math.ceil(len(times) * 0.60))
        and union_bottom - union_top <= 0.72
    ):
        top = max(0.0, union_top - 0.04)
        bottom = min(0.99, union_bottom + 0.025)
        return {
            "top": round(top, 3),
            "bottom": round(bottom, 3),
            "center": round((top + bottom) / 2, 3),
            "confidence": round(len(credible_native_crops) / len(times), 4),
            "method": "multi_frame_native_pixel_union",
        }

    aggregate = []
    for y in range(sample_height):
        values = sorted(frame[y] for frame in per_frame)
        aggregate.append(values[len(values) // 2])
    smooth = []
    for y in range(sample_height):
        values = aggregate[max(0, y - 2) : min(sample_height, y + 3)]
        smooth.append(sum(values) / len(values))

    interior = smooth[round(sample_height * 0.08) : round(sample_height * 0.97)]
    threshold = percentile(interior, 0.78)
    rows = [
        y
        for y, score in enumerate(smooth)
        if sample_height * 0.06 <= y <= sample_height * 0.97 and score >= threshold
    ]
    clusters = merge_row_clusters(rows, maximum_gap=max(3, round(sample_height * 0.045)))
    if not clusters:
        return {"top": 0.72, "bottom": 0.96, "center": 0.84, "confidence": 0.0}

    def cluster_rank(cluster):
        strength = sum(smooth[y] for y in cluster)
        horizontal_text_prior = 1.08 if sum(cluster) / len(cluster) >= sample_height * 0.48 else 1.0
        return strength * horizontal_text_prior

    # 底部小字幕往往不如人物轮廓强，但会形成独立的最底行簇。
    bottom_text_clusters = [
        candidate
        for candidate in clusters
        if len(candidate) >= 4
        and sum(candidate) / len(candidate) >= sample_height * 0.82
    ]
    cluster = (
        max(bottom_text_clusters, key=cluster_rank)
        if bottom_text_clusters
        else max(clusters, key=cluster_rank)
    )
    cluster_top = min(cluster) / sample_height
    cluster_bottom = (max(cluster) + 1) / sample_height
    center = (cluster_top + cluster_bottom) / 2
    padding = 0.26 if 0.64 <= center < 0.82 else 0.12
    band_height = max(0.20, min(0.50, cluster_bottom - cluster_top + padding))
    top = max(0.0, center - band_height / 2)
    bottom = min(0.99, center + band_height / 2)
    if center >= 0.72:
        bottom = min(0.98, max(bottom, 0.94))
        top = max(0.0, bottom - band_height)
    confidence = min(1.0, cluster_rank(cluster) / max(0.001, sum(smooth)))
    return {
        "top": round(top, 3),
        "bottom": round(bottom, 3),
        "center": round(center, 3),
        "confidence": round(confidence, 4),
        "method": "multi_frame_horizontal_edges",
    }


def automatic_title(text):
    words = text.split()
    title = " ".join(words[:8])
    return title[:72].strip(" .,:;!?") or "auto-selected-topic"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("video")
    parser.add_argument("--vtt", required=True)
    parser.add_argument("--mode", choices=("native", "script"), required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()

    video = Path(args.video).expanduser().resolve()
    vtt = Path(args.vtt).expanduser().resolve()
    _, _, duration = RENDERER.video_metadata(video)
    cues = parse_vtt(vtt)
    selected = select_contiguous_cues(cues, duration, args.count, args.mode)
    times = [item["time"] for item in selected]
    if args.mode == "native":
        band = estimate_native_band(video, times)
        payload = {
            "images": [
                {
                    "title": automatic_title(selected[0]["text"]),
                    "times": times,
                }
            ]
        }
    else:
        band = None
        payload = {
            "lines": [
                {"t": item["time"], "text": item["text"]} for item in selected
            ]
        }

    report = {
        "selection_policy": "first_contiguous_five_after_20_percent",
        "mode": args.mode,
        "video_duration": round(duration, 3),
        "selected": selected,
        "band": band,
    }
    out_path = Path(args.out).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
