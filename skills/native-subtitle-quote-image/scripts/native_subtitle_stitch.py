#!/usr/bin/env python3
"""把视频精确取帧并拼成字幕长图，支持烧录字幕与台词脚本两种模式。"""

import argparse
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat


FINAL_QA_PATH = Path(__file__).with_name("final_render_qa.py")
FINAL_QA_SPEC = importlib.util.spec_from_file_location("final_render_qa", FINAL_QA_PATH)
FINAL_QA = importlib.util.module_from_spec(FINAL_QA_SPEC)
FINAL_QA_SPEC.loader.exec_module(FINAL_QA)

try:
    import imageio_ffmpeg

    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    FFMPEG = shutil.which("ffmpeg")
    if not FFMPEG:
        sys.exit("找不到 ffmpeg；请安装 ffmpeg 或 imageio-ffmpeg")


def ffmpeg(args, context="FFmpeg 处理失败"):
    try:
        return subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        raise SystemExit(f"找不到 FFmpeg 可执行文件: {FFMPEG}") from None
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        detail = "\n".join(stderr.splitlines()[-4:]) or "没有返回错误详情"
        raise SystemExit(f"{context}:\n{detail}") from None


def input_file(value, label):
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"{label}不存在或不是文件: {path}")
    return path


def video_metadata(path):
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
    )
    stderr = proc.stderr or ""
    size_match = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})[\s,]", stderr)
    duration_match = re.search(
        r"Duration:\s*(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)", stderr
    )
    if not size_match or not duration_match:
        raise SystemExit(f"无法读取视频尺寸或时长: {path}")
    hours, minutes, seconds = duration_match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return int(size_match.group(1)), int(size_match.group(2)), duration


def validate_time(value, label="时间点"):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise SystemExit(f"{label}必须是数字: {value!r}") from None
    if not math.isfinite(seconds) or seconds < 0:
        raise SystemExit(f"{label}必须是大于等于 0 的有限数字: {value!r}")
    return seconds


def grab_frame(path, seconds):
    """先快速跳转，再精确解码 3 秒，避免长 GOP 视频错帧。"""
    seconds = validate_time(seconds)
    preseek = max(0.0, seconds - 3.0)
    offset = seconds - preseek
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        ffmpeg(
            [
                "-ss",
                f"{preseek:.3f}",
                "-i",
                str(path),
                "-ss",
                f"{offset:.3f}",
                "-frames:v",
                "1",
                tmp,
            ],
            context=f"取帧失败 @ {seconds:.2f}s",
        )
        if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            raise SystemExit(f"取帧失败 @ {seconds:.2f}s")
        with Image.open(tmp) as opened:
            image = opened.convert("RGB")
            image.load()
        return image
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def parse_aspect(value):
    try:
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError
        width, height = (float(x) for x in parts)
    except Exception as exc:
        raise argparse.ArgumentTypeError("比例必须写成 3:4 这样的格式") from exc
    if not math.isfinite(width) or not math.isfinite(height):
        raise argparse.ArgumentTypeError("比例必须是有限数字")
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("比例必须为正数")
    return width, height


def safe_title(value):
    cleaned = re.sub(r"[\\/:*?\"<>|\n\r]+", "_", str(value)).strip(" ._")
    return cleaned or "未命名"


FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def contains_cjk(text):
    ranges = (
        ("\u1100", "\u11ff"),  # 谚文字母
        ("\u3040", "\u30ff"),  # 平假名与片假名
        ("\u3130", "\u318f"),  # 谚文兼容字母
        ("\u3400", "\u9fff"),  # CJK 统一表意文字
        ("\uac00", "\ud7af"),  # 谚文音节
        ("\uf900", "\ufaff"),  # CJK 兼容表意文字
        ("\uff66", "\uff9d"),  # 半角片假名
    )
    return any(start <= char <= end for char in text for start, end in ranges)


def load_subtitle_font(path, size, text):
    candidates = [path] if path else []
    candidates.extend(FONT_CANDIDATES)
    if not contains_cjk(text):
        candidates.append("DejaVuSans.ttf")
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    if contains_cjk(text):
        raise SystemExit(
            "找不到可用的中文字体；请安装中文字体或使用 --font 指定字体文件"
        )
    return ImageFont.load_default()


def draw_scripted_subtitle(image, text, y_center, font_path, font_size, max_width):
    draw = ImageDraw.Draw(image)
    minimum = max(12, round(font_size * 0.55))
    chosen = None
    box = None
    for size in range(font_size, minimum - 1, -2):
        font = load_subtitle_font(font_path, size, text)
        stroke = max(2, size // 14)
        candidate_box = draw.textbbox(
            (0, 0), text, font=font, stroke_width=stroke
        )
        if candidate_box[2] - candidate_box[0] <= max_width:
            chosen = (font, stroke)
            box = candidate_box
            break
    if chosen is None:
        raise SystemExit(
            f"台词过长，缩小到可读下限后仍放不下: {text!r}；请拆句或删减"
        )
    font, stroke = chosen
    text_width = box[2] - box[0]
    text_height = box[3] - box[1]
    x = (image.width - text_width) // 2 - box[0]
    y = y_center - text_height // 2 - box[1]
    draw.text(
        (x, y),
        text,
        font=font,
        fill="white",
        stroke_width=stroke,
        stroke_fill="black",
    )


def normalize_script_lines(data, duration):
    lines = data.get("lines") if isinstance(data, dict) else None
    if not isinstance(lines, list) or len(lines) < 2:
        raise SystemExit("台词脚本必须包含至少 2 项的 lines 数组")
    if len(lines) > 7:
        raise SystemExit("台词脚本最多支持 7 个时间点；请拆成多张图")
    normalized = []
    previous = -1.0
    for index, item in enumerate(lines):
        if not isinstance(item, dict):
            raise SystemExit(f"lines[{index}] 必须是对象")
        seconds = validate_time(item.get("t"), f"lines[{index}].t")
        if seconds <= previous:
            raise SystemExit("台词时间点必须严格递增")
        if seconds >= duration:
            raise SystemExit(
                f"lines[{index}].t={seconds:.2f}s 必须小于视频时长 {duration:.2f}s"
            )
        raw_text = item.get("text")
        if not isinstance(raw_text, str):
            raise SystemExit(f"lines[{index}].text 必须是字符串")
        text = raw_text.strip()
        if not text:
            raise SystemExit(f"lines[{index}].text 不能为空")
        if "\n" in text or "\r" in text:
            raise SystemExit(f"lines[{index}].text 必须是单行台词")
        normalized.append({"t": seconds, "text": text})
        previous = seconds
    return normalized


def normalize_times(values, label="时间点"):
    if not isinstance(values, list) or len(values) < 2:
        raise SystemExit(f"{label}必须是至少包含 2 项的数组")
    times = [
        validate_time(value, f"{label}[{index}]")
        for index, value in enumerate(values)
    ]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise SystemExit(f"{label}必须严格递增: {values}")
    return times


def crop_band(frame, top, bottom):
    if not 0 <= top < bottom <= 1:
        raise SystemExit("字幕区域必须满足 0 <= top < bottom <= 1")
    y0, y1 = int(frame.height * top), int(frame.height * bottom)
    if y1 <= y0:
        raise SystemExit("--band-bottom 必须大于 --band-top")
    return frame.crop((0, y0, frame.width, y1)), y0, y1


def fit_lower(image, size, vertical=0.72):
    return ImageOps.fit(
        image,
        size,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, vertical),
    )


def choose_hero_fraction(strip_count, requested=None):
    """保持字幕条紧凑；台词较少时把多余高度留给主画面。"""
    if strip_count <= 0:
        raise SystemExit("至少需要 1 个字幕条")
    if requested is not None:
        return requested
    return min(0.82, max(0.48, 1.0 - strip_count * 0.075))


def classify_native_layout(top, bottom, low_visual=False):
    """根据字幕带位置和视觉价值选择原生字幕主图布局。"""
    if low_visual:
        return "low-visual-fallback"
    return "bottom-band" if (top + bottom) / 2 >= 0.74 else "centered-band"


def visual_assessment(frame):
    """用轻量、可解释的图像统计拒绝黑场、空镜和低信息量画面。"""
    sample = frame.convert("RGB").resize((160, 90), Image.Resampling.BILINEAR)
    gray = sample.convert("L")
    gray_stat = ImageStat.Stat(gray)
    edges = gray.filter(ImageFilter.FIND_EDGES)
    histogram = gray.histogram()
    dark_ratio = sum(histogram[:32]) / sum(histogram)
    luminance_mean = gray_stat.mean[0]
    entropy = gray.entropy()
    luminance_std = gray_stat.stddev[0]
    edge_mean = ImageStat.Stat(edges).mean[0]
    saturation_mean = ImageStat.Stat(sample.convert("HSV").getchannel("S")).mean[0]
    raw_rgb = sample.tobytes()
    skin_mask = [
        red > 95
        and green > 40
        and blue > 20
        and max(red, green, blue) - min(red, green, blue) > 15
        and abs(red - green) > 15
        and red > green
        and red > blue
        for red, green, blue in zip(raw_rgb[::3], raw_rgb[1::3], raw_rgb[2::3])
    ]
    skin_tone_ratio = sum(skin_mask) / (sample.width * sample.height)
    subject_center_x = largest_interior_component_center(
        skin_mask, sample.width, sample.height, max_y=round(sample.height * 0.60)
    )
    score = (
        min(1.0, max(0.0, (entropy - 4.5) / 2.5)) * 0.30
        + min(1.0, max(0.0, (luminance_std - 18.0) / 45.0)) * 0.25
        + min(1.0, max(0.0, (edge_mean - 15.0) / 25.0)) * 0.20
        + min(1.0, max(0.0, (0.90 - dark_ratio) / 0.80)) * 0.25
    )
    reasons = []
    if dark_ratio >= 0.82:
        reasons.append("dark_or_empty")
    if entropy < 5.5:
        reasons.append("low_entropy")
    if luminance_std < 25.0:
        reasons.append("low_luminance_variation")
    if score < 0.34:
        reasons.append("low_visual_score")
    document_like = (
        luminance_mean >= 155
        and saturation_mean < 55
        and edge_mean >= 20
        and dark_ratio < 0.08
        and subject_center_x is None
    )
    if document_like:
        reasons.append("document_or_slide_low_visual")
    passed = not reasons
    signature = sample.resize((48, 27), Image.Resampling.BILINEAR)
    return {
        "passed": passed,
        "score": round(score, 4),
        "entropy": round(entropy, 4),
        "luminance_std": round(luminance_std, 4),
        "luminance_mean": round(luminance_mean, 4),
        "edge_mean": round(edge_mean, 4),
        "saturation_mean": round(saturation_mean, 4),
        "skin_tone_ratio": round(skin_tone_ratio, 4),
        "subject_center_x": (
            round(subject_center_x, 4) if subject_center_x is not None else None
        ),
        "dark_ratio": round(dark_ratio, 4),
        "document_like": document_like,
        "reasons": reasons,
        "signature": signature,
    }


def largest_interior_component_center(mask, width, height, max_y=None):
    """找上部画面内不接触左右边缘的最大连通区域，返回横向中心比例。"""
    max_y = height if max_y is None else min(height, max(1, max_y))
    visited = set()
    best = None
    for y in range(max_y):
        for x in range(width):
            start = y * width + x
            if not mask[start] or start in visited:
                continue
            stack = [start]
            visited.add(start)
            points = []
            touches_side = False
            while stack:
                current = stack.pop()
                points.append(current)
                current_y, current_x = divmod(current, width)
                touches_side = touches_side or current_x in {0, width - 1}
                for next_x, next_y in (
                    (current_x - 1, current_y),
                    (current_x + 1, current_y),
                    (current_x, current_y - 1),
                    (current_x, current_y + 1),
                ):
                    if not (0 <= next_x < width and 0 <= next_y < max_y):
                        continue
                    neighbor = next_y * width + next_x
                    if mask[neighbor] and neighbor not in visited:
                        visited.add(neighbor)
                        stack.append(neighbor)
            if touches_side or len(points) < 20:
                continue
            if best is None or len(points) > len(best):
                best = points
    if best is None:
        return None
    return sum(point % width for point in best) / len(best) / max(1, width - 1)


def subject_aware_centering(source, target_size, subject_center_x):
    """把估计主体放进 portrait crop，返回 Pillow ImageOps.fit 的 centering。"""
    if subject_center_x is None:
        return 0.5
    target_width, target_height = target_size
    crop_width = source.height * target_width / target_height
    extra_width = source.width - crop_width
    if extra_width <= 0:
        return 0.5
    desired_left = subject_center_x * source.width - crop_width / 2
    return min(1.0, max(0.0, desired_left / extra_width))


def visual_distance(left, right):
    """返回 0–1 的缩略图平均绝对差，用于阻止 final 画面近重复。"""
    difference = ImageChops.difference(left.convert("RGB"), right.convert("RGB"))
    mean = ImageStat.Stat(difference).mean
    return sum(mean) / (len(mean) * 255.0)


def bounded_window(value, fallback, duration, label):
    """校验 manifest 里的语义时间窗口。"""
    raw = fallback if value is None else value
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise SystemExit(f"{label}必须是 [start, end]")
    start = validate_time(raw[0], f"{label}[0]")
    end = validate_time(raw[1], f"{label}[1]")
    if end <= start:
        raise SystemExit(f"{label}必须满足 start < end")
    latest = max(0.0, duration - min(0.5, duration / 10))
    if start > latest or end > duration:
        raise SystemExit(f"{label}必须位于视频时长内")
    return [round(start, 3), round(min(end, latest), 3)]


def semantic_windows(times, duration, theme_window=None, speaking_window=None):
    """构造可机器证明的时间范围；显式 manifest 窗口优先。"""
    quote_start, quote_end = min(times), max(times)
    theme = bounded_window(
        theme_window,
        [max(0.0, quote_start - 30.0), min(duration, quote_end + 30.0)],
        duration,
        "theme_window",
    )
    speaking = bounded_window(
        speaking_window,
        [max(0.0, quote_start - 90.0), min(duration, quote_end + 90.0)],
        duration,
        "speaking_window",
    )
    if speaking[0] > theme[0] or speaking[1] < theme[1]:
        raise SystemExit("speaking_window 必须完整包含 theme_window")
    return {"theme": theme, "speaking": speaking}


def candidate_groups(
    original,
    explicit,
    duration,
    quote_times=None,
    theme_window=None,
    speaking_window=None,
):
    latest = max(0.0, duration - min(0.5, duration / 10))
    quote_times = quote_times or [original]
    windows = semantic_windows(
        quote_times, duration, theme_window=theme_window, speaking_window=speaking_window
    )

    def valid(values):
        seen = set()
        result = []
        for value in values:
            value = round(max(0.0, min(latest, float(value))), 3)
            if value not in seen:
                seen.add(value)
                result.append(value)
        return result

    theme_values = valid(
        [
            *explicit,
            *quote_times[1:],
            original - 4,
            original + 4,
            original - 10,
            original + 10,
            original - 20,
            original + 20,
            windows["theme"][0],
            windows["theme"][1],
        ]
    )
    theme_values = [
        value
        for value in theme_values
        if windows["theme"][0] <= value <= windows["theme"][1]
    ]
    speaking_values = valid(
        [
            windows["speaking"][0] + offset
            for offset in range(
                0,
                max(1, int(windows["speaking"][1] - windows["speaking"][0]) + 1),
                15,
            )
        ]
        + [windows["speaking"][1]]
    )
    speaking_values = [
        value
        for value in speaking_values
        if not windows["theme"][0] <= value <= windows["theme"][1]
    ]
    return [
        ("original", valid([original])),
        (
            "same-line-nearby",
            valid([original - 0.8, original + 0.8, original - 1.5, original + 1.5]),
        ),
        (
            "same-theme-window",
            theme_values,
        ),
        ("same-speaking-shot", speaking_values),
    ]


def global_candidate_times(duration, count):
    if count <= 0:
        raise SystemExit("--global-candidates 必须为正整数")
    latest = max(0.0, duration - min(0.5, duration / 10))
    return [round(latest * (index + 0.5) / count, 3) for index in range(count)]


def public_assessment(assessment):
    return {key: value for key, value in assessment.items() if key != "signature"}


def choose_visual_hero(
    video,
    original,
    explicit,
    duration,
    used_signatures,
    global_times,
    emit,
    global_cache=None,
    minimum_distance=0.035,
    quote_times=None,
    theme_window=None,
    speaking_window=None,
    allow_source_wide=False,
    speaking_window_verified=False,
):
    """先约束语义时窗，再在窗口内执行 visual gate 与修复。"""
    quote_times = quote_times or [original]
    windows = semantic_windows(
        quote_times, duration, theme_window=theme_window, speaking_window=speaking_window
    )
    groups = candidate_groups(
        original,
        explicit,
        duration,
        quote_times=quote_times,
        theme_window=windows["theme"],
        speaking_window=windows["speaking"],
    )
    if not speaking_window_verified:
        groups = [group for group in groups if group[0] != "same-speaking-shot"]
        emit(
            "semantic_gate",
            phase="same-speaking-shot",
            accepted=False,
            reason="unverified_speaking_window_not_declared",
            semantic_window=windows["speaking"],
            semantic_alignment="FAIL",
        )
    if allow_source_wide:
        groups.append(("source-wide-fallback", global_times))
    attempted = set()

    def semantic_basis_for(phase):
        return (
            "explicit_source_wide_compatibility_fallback"
            if phase == "source-wide-fallback"
            else "exact_quote_timestamp"
            if phase == "original"
            else "same_line_nearby_window"
            if phase == "same-line-nearby"
            else "bounded_theme_window"
            if phase == "same-theme-window"
            else "manifest_declared_speaking_window"
        )

    for seconds in explicit:
        if not windows["theme"][0] <= seconds <= windows["theme"][1]:
            emit(
                "semantic_gate",
                phase="same-theme-window",
                time=seconds,
                accepted=False,
                reason="outside_theme_window",
                semantic_window=windows["theme"],
                semantic_alignment="FAIL",
            )

    def evaluate(phase, seconds):
        if seconds in attempted:
            return None
        attempted.add(seconds)
        cached = global_cache.get(seconds) if global_cache is not None else None
        if phase == "source-wide-fallback" and cached is not None:
            frame, assessment = cached
        else:
            frame = grab_frame(video, seconds)
            assessment = visual_assessment(frame)
            if phase == "source-wide-fallback" and global_cache is not None:
                global_cache[seconds] = (frame, assessment)
        distances = [
            visual_distance(assessment["signature"], previous)
            for previous in used_signatures
        ]
        nearest = min(distances) if distances else 1.0
        diverse = nearest >= minimum_distance
        accepted = assessment["passed"] and diverse
        reasons = list(assessment["reasons"])
        if assessment["passed"] and not diverse:
            reasons.append("near_duplicate_final")
        semantic_basis = semantic_basis_for(phase)
        semantic_window = (
            None
            if phase == "source-wide-fallback"
            else [round(original, 3), round(original, 3)]
            if phase == "original"
            else [
                round(max(0.0, original - 1.5), 3),
                round(min(duration, original + 1.5), 3),
            ]
            if phase == "same-line-nearby"
            else windows["theme"]
            if phase == "same-theme-window"
            else windows["speaking"]
        )
        emit(
            "visual_gate",
            phase=phase,
            time=seconds,
            accepted=accepted,
            nearest_final_distance=round(nearest, 4),
            reasons=reasons,
            metrics=public_assessment(assessment),
            semantic_window=semantic_window,
            semantic_alignment=(
                "PARTIAL_PASS" if phase == "source-wide-fallback" else "PASS"
            ),
            semantic_basis=semantic_basis,
        )
        if not accepted:
            return None
        return (assessment["score"], nearest, seconds, frame, assessment)

    for phase, values in groups:
        passing = []
        for seconds in values:
            candidate = evaluate(phase, seconds)
            if candidate is not None:
                passing.append(candidate)
        if phase == "source-wide-fallback" and passing:
            latest = max(0.0, duration - min(0.5, duration / 10))
            anchors = [
                item[2]
                for item in passing
                if item[4]["skin_tone_ratio"] >= 0.10
            ]
            for anchor in anchors:
                for offset in (-6.0, -3.0, 3.0, 6.0):
                    seconds = round(max(0.0, min(latest, anchor + offset)), 3)
                    candidate = evaluate(phase, seconds)
                    if candidate is not None:
                        passing.append(candidate)
        if passing:
            if phase == "source-wide-fallback":
                rank = lambda item: (
                    item[4]["skin_tone_ratio"] >= 0.10,
                    item[4]["skin_tone_ratio"],
                    item[0],
                    item[1],
                )
            else:
                rank = lambda item: (item[0], item[1])
            _, _, seconds, frame, assessment = max(passing, key=rank)
            if phase != "original":
                emit(
                    "auto_repair",
                    action=phase,
                    original_time=original,
                    replacement_time=seconds,
                    reason="original_visual_gate_failed",
                )
            return {
                "time": seconds,
                "frame": frame,
                "assessment": assessment,
                "phase": phase,
                "semantic_alignment": (
                    "PARTIAL_PASS" if phase == "source-wide-fallback" else "PASS"
                ),
                "semantic_basis": semantic_basis_for(phase),
            }
    emit(
        "semantic_window_exhausted",
        phase="semantic-window",
        accepted=False,
        reason="no_visual_candidate_passed_within_semantic_windows",
        theme_window=windows["theme"],
        speaking_window=windows["speaking"],
        semantic_alignment="FAIL",
    )
    return None


def detect_native_quote_crop(band):
    """根据底色自适应查找亮色或暗色原生字幕像素。"""
    gray = band.convert("L")
    luminance_mean = ImageStat.Stat(gray).mean[0]
    polarity = "dark_on_light" if luminance_mean >= 155 else "bright_on_dark"

    def is_text_pixel(value):
        return value <= 92 if polarity == "dark_on_light" else value >= 180

    row_floor = max(12, round(gray.width * 0.015))
    row_counts = [
        sum(is_text_pixel(gray.getpixel((x, y))) for x in range(gray.width))
        for y in range(gray.height)
    ]
    rows = [index for index, count in enumerate(row_counts) if count >= row_floor]
    if not rows:
        return band.copy(), {
            "detected": False,
            "reason": "no_dense_text_rows",
            "crop": [0, 0, band.width, band.height],
            "polarity": polarity,
        }
    y0 = max(0, min(rows) - max(6, round(band.height * 0.06)))
    y1 = min(band.height, max(rows) + 1 + max(6, round(band.height * 0.06)))
    columns = []
    for x in range(gray.width):
        count = sum(
            is_text_pixel(gray.getpixel((x, y))) for y in range(y0, y1)
        )
        if count >= 5:
            columns.append(x)
    if not columns:
        return band.crop((0, y0, band.width, y1)), {
            "detected": False,
            "reason": "text_rows_found_but_columns_ambiguous",
            "crop": [0, y0, band.width, y1],
            "polarity": polarity,
        }
    pad_x = max(12, round(band.width * 0.03))
    x0 = max(0, min(columns) - pad_x)
    x1 = min(band.width, max(columns) + 1 + pad_x)
    return band.crop((x0, y0, x1, y1)), {
        "detected": True,
        "reason": "dense_native_subtitle_pixels",
        "crop": [x0, y0, x1, y1],
        "source_retention": round((x1 - x0) / band.width, 4),
        "polarity": polarity,
    }


def render_one(
    video,
    times,
    out_path,
    aspect,
    out_width,
    top,
    bottom,
    hero_fraction,
    hero_frame=None,
    native_layout="legacy",
    strip_bands=None,
):
    times = normalize_times(times)
    aw, ah = aspect
    out_height = round(out_width * ah / aw)
    strip_count = len(times) - 1
    hero_fraction = choose_hero_fraction(strip_count, hero_fraction)
    hero_height = round(out_height * hero_fraction)
    remaining = out_height - hero_height
    base_strip = remaining // strip_count
    strip_heights = [base_strip] * strip_count
    strip_heights[-1] += remaining - sum(strip_heights)
    if strip_bands is None:
        strip_bands = [[top, bottom] for _ in range(strip_count)]
    if len(strip_bands) != strip_count:
        raise ValueError("strip_bands must align one-to-one with final strips")
    normalized_strip_bands = []
    for index, value in enumerate(strip_bands):
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(f"strip_bands[{index}] must be [top, bottom]")
        strip_top, strip_bottom = (float(item) for item in value)
        if not 0 <= strip_top < strip_bottom <= 1:
            raise ValueError(f"strip_bands[{index}] must satisfy 0 <= top < bottom <= 1")
        normalized_strip_bands.append([strip_top, strip_bottom])

    first = grab_frame(video, times[0])
    band, _, subtitle_bottom = crop_band(first, top, bottom)
    quote_detection = None
    contained_frame_box = None
    hero_source_kind = "first_frame"
    hero_content_box = [0, 0, out_width, hero_height]
    hero_source_crop_box = [0.0, 0.0, float(first.width), float(first.height)]
    if native_layout == "quote-first":
        background = ImageOps.fit(
            first,
            (out_width, hero_height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        ).filter(ImageFilter.GaussianBlur(radius=max(8, out_width // 70)))
        hero = Image.blend(
            background,
            Image.new("RGB", background.size, "#05070d"),
            0.68,
        )
        quote_crop, quote_detection = detect_native_quote_crop(band)
        quote = ImageOps.contain(
            quote_crop,
            (round(out_width * 0.90), round(hero_height * 0.58)),
            method=Image.Resampling.LANCZOS,
        )
        padding_x = max(24, round(out_width * 0.035))
        padding_y = max(20, round(out_width * 0.025))
        panel = Image.new(
            "RGB",
            (min(out_width, quote.width + padding_x * 2), quote.height + padding_y * 2),
            "#090d16",
        )
        panel.paste(quote, ((panel.width - quote.width) // 2, padding_y))
        hero.paste(
            panel,
            ((out_width - panel.width) // 2, (hero_height - panel.height) // 2),
        )
        band_height = quote.height
        subtitle_horizontal_retention = 1.0
        horizontal = 0.5
    elif native_layout == "centered-band":
        # 居中烧录字幕已经进入主画面；竖版裁宽会直接截断文字。
        background_source = hero_frame or first
        background = ImageOps.fit(
            background_source,
            (out_width, hero_height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        ).filter(ImageFilter.GaussianBlur(radius=max(8, out_width // 70)))
        hero = Image.blend(
            background,
            Image.new("RGB", background.size, "#05070d"),
            0.58,
        )
        contained = ImageOps.contain(
            first,
            (out_width, hero_height),
            method=Image.Resampling.LANCZOS,
        )
        contained_x = (out_width - contained.width) // 2
        contained_y = (hero_height - contained.height) // 2
        hero.paste(contained, (contained_x, contained_y))
        contained_frame_box = [
            contained_x,
            contained_y,
            contained_x + contained.width,
            contained_y + contained.height,
        ]
        band_height = contained.height
        subtitle_horizontal_retention = 1.0
        horizontal = 0.5
    elif native_layout == "legacy":
        wanted_hero_source_h = min(
            subtitle_bottom,
            max(1, round(first.width * hero_height / out_width)),
        )
        hero_source = first.crop(
            (0, subtitle_bottom - wanted_hero_source_h, first.width, subtitle_bottom)
        )
        fitted_crop = FINAL_QA.fit_crop_box(
            hero_source.size, (out_width, hero_height), (0.5, 0.75)
        )
        hero_source_crop_box = [
            fitted_crop[0],
            fitted_crop[1] + subtitle_bottom - wanted_hero_source_h,
            fitted_crop[2],
            fitted_crop[3] + subtitle_bottom - wanted_hero_source_h,
        ]
        hero = fit_lower(hero_source, (out_width, hero_height), vertical=0.75)
        visible_source_width = min(
            first.width, round(wanted_hero_source_h * out_width / hero_height)
        )
        subtitle_horizontal_retention = visible_source_width / first.width
        band_height = None
    else:
        visual_source = hero_frame or first
        hero_source_kind = "selected_hero_frame" if hero_frame is not None else "first_frame"
        subject_center_x = None
        visual_origin_y = 0
        if native_layout == "low-visual-fallback":
            subject_center_x = visual_assessment(visual_source)["subject_center_x"]
            visual_source = visual_source.crop(
                (0, 0, visual_source.width, round(visual_source.height * 0.60))
            )
        natural_band_height = max(1, round(band.height * out_width / band.width))
        maximum_band_height = max(1, round(hero_height * 0.32))
        band_height = min(natural_band_height, maximum_band_height)
        visual_height = hero_height - band_height
        if visual_height < round(hero_height * 0.55):
            raise ValueError("字幕带过高，无法保留足够的主画面空间")
        vertical = 0.56 if native_layout == "bottom-band" else 0.48
        horizontal = subject_aware_centering(
            visual_source, (out_width, visual_height), subject_center_x
        )
        visual = ImageOps.fit(
            visual_source,
            (out_width, visual_height),
            method=Image.Resampling.LANCZOS,
            centering=(horizontal, vertical),
        )
        hero_source_crop_box = FINAL_QA.fit_crop_box(
            visual_source.size,
            (out_width, visual_height),
            (horizontal, vertical),
        )
        hero_source_crop_box[1] += visual_origin_y
        hero_source_crop_box[3] += visual_origin_y
        hero_content_box = [0, 0, out_width, visual_height]
        preserved_band = band.resize(
            (out_width, band_height), Image.Resampling.LANCZOS
        )
        hero = Image.new("RGB", (out_width, hero_height), "black")
        hero.paste(visual, (0, 0))
        hero.paste(preserved_band, (0, visual_height))
        subtitle_horizontal_retention = 1.0

    strips = []
    subtitle_strip_strategy = (
        "contain-full-band"
        if native_layout in {"centered-band", "quote-first"}
        else "full-width-band"
    )
    for seconds, height, strip_band in zip(
        times[1:], strip_heights, normalized_strip_bands
    ):
        frame = grab_frame(video, seconds)
        band, _, _ = crop_band(frame, strip_band[0], strip_band[1])
        if native_layout in {"centered-band", "quote-first"}:
            contained = ImageOps.contain(
                band,
                (out_width, height),
                method=Image.Resampling.LANCZOS,
            )
            strip = Image.new("RGB", (out_width, height), "black")
            strip.paste(
                contained,
                ((out_width - contained.width) // 2, (height - contained.height) // 2),
            )
            strips.append(strip)
        else:
            strips.append(fit_lower(band, (out_width, height), vertical=0.72))

    canvas = Image.new("RGB", (out_width, out_height), "black")
    canvas.paste(hero, (0, 0))
    y = hero_height
    for strip in strips:
        canvas.paste(strip, (0, y))
        y += strip.height
    canvas.save(out_path, quality=93, subsampling=0)
    print(f"完成: {out_path} ({out_width}x{out_height})")
    print(
        f"布局: {native_layout}；主画面 {hero_fraction:.1%}，"
        f"字幕条 {strip_count} 个，条间距 0，"
        f"主字幕横向保留 {subtitle_horizontal_retention:.1%}"
    )
    return {
        "layout": native_layout,
        "hero_fraction": hero_fraction,
        "hero_height": hero_height,
        "band_height": band_height,
        "subtitle_horizontal_retention": round(subtitle_horizontal_retention, 4),
        "visual_centering_x": (
            round(horizontal, 4) if native_layout != "legacy" else 0.5
        ),
        "quote_detection": quote_detection,
        "contained_frame_box": contained_frame_box,
        "subtitle_strip_strategy": subtitle_strip_strategy,
        "strip_heights": strip_heights,
        "strip_bands": [
            [round(value[0], 4), round(value[1], 4)]
            for value in normalized_strip_bands
        ],
        "hero_source_kind": hero_source_kind,
        "hero_source_crop_box": [round(value, 4) for value in hero_source_crop_box],
        "hero_content_box": hero_content_box,
    }


def scripted_render_one(
    video,
    lines,
    out_path,
    aspect,
    out_width,
    band_center,
    hero_fraction,
    font_path,
    font_size,
    hero_layout="fit",
):
    aw, ah = aspect
    out_height = round(out_width * ah / aw)
    strip_count = len(lines) - 1
    hero_fraction = choose_hero_fraction(strip_count, hero_fraction)
    hero_height = round(out_height * hero_fraction)
    remaining = out_height - hero_height
    base_strip = remaining // strip_count
    strip_heights = [base_strip] * strip_count
    strip_heights[-1] += remaining - sum(strip_heights)
    base_font = font_size or max(24, round(out_width / 18))

    first_frame = grab_frame(video, lines[0]["t"])
    if hero_layout == "contain":
        background = ImageOps.fit(
            first_frame,
            (out_width, hero_height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        ).filter(ImageFilter.GaussianBlur(radius=max(8, out_width // 70)))
        hero = Image.blend(
            background,
            Image.new("RGB", background.size, "#05070d"),
            0.42,
        )
        contained = ImageOps.contain(
            first_frame,
            (out_width, hero_height),
            method=Image.Resampling.LANCZOS,
        )
        hero.paste(
            contained,
            ((out_width - contained.width) // 2, (hero_height - contained.height) // 2),
        )
        source_crop_box = [0.0, 0.0, float(first_frame.width), float(first_frame.height)]
    elif hero_layout == "fit":
        hero = ImageOps.fit(
            first_frame,
            (out_width, hero_height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        source_crop_box = FINAL_QA.fit_crop_box(
            first_frame.size, (out_width, hero_height)
        )
    else:
        raise ValueError("hero_layout must be fit or contain")
    first_strip_height = strip_heights[0]
    draw_scripted_subtitle(
        hero,
        lines[0]["text"],
        hero.height - first_strip_height // 2 - max(4, out_height // 150),
        font_path,
        min(base_font, max(16, round(first_strip_height * 0.62))),
        round(out_width * 0.92),
    )

    strips = []
    for line, strip_height in zip(lines[1:], strip_heights):
        frame = grab_frame(video, line["t"])
        source_height = max(
            1, round(frame.width * strip_height / out_width)
        )
        source_height = min(source_height, frame.height)
        center_y = round(frame.height * band_center)
        y0 = max(0, min(frame.height - source_height, center_y - source_height // 2))
        band = frame.crop((0, y0, frame.width, y0 + source_height))
        strip = ImageOps.fit(
            band,
            (out_width, strip_height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        draw_scripted_subtitle(
            strip,
            line["text"],
            strip.height // 2,
            font_path,
            min(base_font, max(16, round(strip.height * 0.62))),
            round(out_width * 0.92),
        )
        strips.append(strip)

    canvas = Image.new("RGB", (out_width, out_height), "black")
    canvas.paste(hero, (0, 0))
    y = hero_height
    for strip in strips:
        canvas.paste(strip, (0, y))
        y += strip.height
    canvas.save(out_path, quality=93, subsampling=0)
    print(f"完成: {out_path} ({out_width}x{out_height})")
    print(
        f"脚本模式: 主画面 {hero_fraction:.1%}，"
        f"字幕条 {strip_count} 个，条间距 0"
    )
    return {
        "layout": hero_layout,
        "hero_height": hero_height,
        "strip_heights": strip_heights,
        "source_crop_box": source_crop_box,
        "hero_box": [0, 0, out_width, hero_height],
    }


def contact_sheet(paths, out_path, columns=4):
    if not paths:
        return
    columns = min(columns, len(paths))
    with Image.open(paths[0]) as first:
        thumb_w = 360
        thumb_h = max(1, round(thumb_w * first.height / first.width))
    rows = (len(paths) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * thumb_w, rows * thumb_h), "#111111")
    for index, path in enumerate(paths):
        with Image.open(path) as opened:
            image = opened.convert("RGB")
            thumb = ImageOps.fit(image, (thumb_w, thumb_h), Image.Resampling.LANCZOS)
        sheet.paste(thumb, ((index % columns) * thumb_w, (index // columns) * thumb_h))
    sheet.save(out_path, quality=92, subsampling=0)


def format_timestamp(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    remainder = seconds % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remainder:04.1f}"
    return f"{minutes:02d}:{remainder:04.1f}"


def build_sample_times(start, end, interval, max_frames):
    start = validate_time(start, "--start")
    end = validate_time(end, "--end")
    if end <= start:
        raise SystemExit("--end 必须大于 --start")
    if max_frames <= 0:
        raise SystemExit("--max-frames 必须为正整数")
    if interval is None:
        interval = max(0.25, (end - start) / 23)
    interval = validate_time(interval, "--interval")
    if interval == 0:
        raise SystemExit("--interval 必须大于 0")
    count = int(math.floor((end - start) / interval)) + 1
    if count > max_frames:
        suggested = (end - start) / max(1, max_frames - 1)
        raise SystemExit(
            f"候选帧数量为 {count}，超过上限 {max_frames}；"
            f"请把 --interval 调整为至少 {suggested:.2f} 秒"
        )
    return [start + index * interval for index in range(count)]


def build_focus_times(values, around, duration, max_frames):
    """围绕文字稿给出的时间点生成前、中、后三帧候选。"""
    if not values:
        raise SystemExit("--time 至少需要一个时间点")
    around = validate_time(around, "--around")
    if around == 0:
        raise SystemExit("--around 必须大于 0")
    decode_margin = min(0.5, duration / 10)
    latest_decodable = max(0.0, duration - decode_margin)
    times = []
    for index, value in enumerate(values):
        center = validate_time(value, f"--time[{index}]")
        if center > latest_decodable:
            raise SystemExit(
                f"--time[{index}]={center:.2f}s 超出可取帧范围 "
                f"0–{latest_decodable:.2f}s"
            )
        times.extend(
            max(0.0, min(latest_decodable, center + offset))
            for offset in (-around, 0.0, around)
        )
    unique = sorted({round(value, 3) for value in times})
    if len(unique) > max_frames:
        raise SystemExit(
            f"候选帧数量为 {len(unique)}，超过上限 {max_frames}；"
            "请减少 --time 数量或提高 --max-frames"
        )
    return unique


def sample_contact_sheet(video, times, out_path, video_size, columns, thumb_width):
    frame_width, frame_height = video_size
    thumb_height = max(1, round(thumb_width * frame_height / frame_width))
    label_height = 28
    columns = min(columns, len(times))
    rows = (len(times) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (columns * thumb_width, rows * (thumb_height + label_height)),
        "#111111",
    )
    for index, seconds in enumerate(times):
        frame = grab_frame(video, seconds)
        thumb = ImageOps.contain(
            frame, (thumb_width, thumb_height), Image.Resampling.LANCZOS
        )
        tile = Image.new("RGB", (thumb_width, thumb_height + label_height), "#111111")
        tile.paste(thumb, ((thumb_width - thumb.width) // 2, 0))
        draw = ImageDraw.Draw(tile)
        draw.text((8, thumb_height + 7), format_timestamp(seconds), fill="white")
        x = (index % columns) * thumb_width
        y = (index // columns) * (thumb_height + label_height)
        sheet.paste(tile, (x, y))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=90, subsampling=0)


def refuse_existing(paths, overwrite):
    existing = [path for path in paths if path.exists()]
    if existing and not overwrite:
        listed = "\n".join(f"- {path}" for path in existing[:8])
        raise SystemExit(
            "以下输出已存在；请使用新的输出路径，或明确添加 --overwrite:\n" + listed
        )


def command_band(args):
    video = input_file(args.video, "视频")
    _, _, duration = video_metadata(video)
    timestamp = validate_time(args.time, "--time")
    if timestamp >= duration:
        raise SystemExit(f"--time 必须小于视频时长 {duration:.2f}s")
    out_path = Path(args.out).expanduser().resolve()
    refuse_existing([out_path], args.overwrite)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame = grab_frame(video, timestamp)
    _, y0, y1 = crop_band(frame, args.band_top, args.band_bottom)
    draw = ImageDraw.Draw(frame)
    line_width = max(3, frame.height // 300)
    draw.line((0, y0, frame.width, y0), fill="red", width=line_width)
    draw.line((0, y1, frame.width, y1), fill="red", width=line_width)
    frame.save(out_path, quality=93)
    print(f"字幕区域预览: {out_path} (y={y0}-{y1})")


def command_sample(args):
    video = input_file(args.video, "视频")
    width, height, duration = video_metadata(video)
    decode_margin = min(0.5, duration / 10)
    latest_decodable = max(0.0, duration - decode_margin)
    if args.times:
        if args.start != 0.0 or args.end is not None or args.interval is not None:
            raise SystemExit(
                "使用 --time 时不要同时传 --start、--end 或 --interval"
            )
        times = build_focus_times(
            args.times, args.around, duration, args.max_frames
        )
    else:
        end = latest_decodable if args.end is None else validate_time(args.end, "--end")
        if end > duration + 0.05:
            raise SystemExit(f"--end 超出视频时长 {duration:.2f}s")
        end = min(end, latest_decodable)
        times = build_sample_times(args.start, end, args.interval, args.max_frames)
    if args.columns <= 0:
        raise SystemExit("--columns 必须为正整数")
    if args.thumb_width < 120:
        raise SystemExit("--thumb-width 不能小于 120")
    out_path = Path(args.out).expanduser().resolve()
    refuse_existing([out_path], args.overwrite)
    sample_contact_sheet(
        video, times, out_path, (width, height), args.columns, args.thumb_width
    )
    print(f"候选帧总览: {out_path}")
    print("时间点: " + ", ".join(f"{value:.2f}" for value in times))


def command_render(args):
    video = input_file(args.video, "视频")
    manifest_path = input_file(args.manifest, "manifest")
    try:
        with manifest_path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"manifest JSON 格式错误（第 {exc.lineno} 行第 {exc.colno} 列）: {exc.msg}"
        ) from None

    items = data.get("images") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise SystemExit("manifest 必须包含非空 images 数组")

    _, _, duration = video_metadata(video)
    jobs = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise SystemExit(f"images 第 {index} 项必须是对象")
        title = safe_title(item.get("title", f"图片{index}"))
        times = normalize_times(item.get("times"), f"images[{index}].times")
        if times[-1] >= duration:
            raise SystemExit(
                f"images[{index}].times 的最后时间点 {times[-1]:.2f}s "
                f"必须小于视频时长 {duration:.2f}s"
            )
        raw_cue_windows = item.get("cue_windows")
        cue_windows = None
        if raw_cue_windows is not None:
            if not isinstance(raw_cue_windows, list) or len(raw_cue_windows) != len(times):
                raise SystemExit(
                    f"images[{index}].cue_windows 必须与 times 一一对应"
                )
            cue_windows = []
            for cue_index, raw_window in enumerate(raw_cue_windows):
                if isinstance(raw_window, dict):
                    raw_window = [raw_window.get("start"), raw_window.get("end")]
                window = bounded_window(
                    raw_window,
                    raw_window,
                    duration,
                    f"images[{index}].cue_windows[{cue_index}]",
                )
                if not window[0] <= times[cue_index] <= window[1]:
                    raise SystemExit(
                        f"images[{index}].times[{cue_index}] 必须位于对应 cue_window 内"
                    )
                cue_windows.append(window)
        raw_candidates = item.get("hero_candidates", [])
        if not isinstance(raw_candidates, list):
            raise SystemExit(f"images[{index}].hero_candidates 必须是数组")
        hero_candidates = [
            validate_time(value, f"images[{index}].hero_candidates[{candidate_index}]")
            for candidate_index, value in enumerate(raw_candidates)
        ]
        if any(value >= duration for value in hero_candidates):
            raise SystemExit(
                f"images[{index}].hero_candidates 必须全部小于视频时长 {duration:.2f}s"
            )
        if "theme_window" in item and "semantic_window" in item:
            raise SystemExit(
                f"images[{index}] 不要同时传 theme_window 和 semantic_window"
            )
        windows = semantic_windows(
            times,
            duration,
            theme_window=item.get("theme_window", item.get("semantic_window")),
            speaking_window=item.get("speaking_window"),
        )
        jobs.append(
            {
                "index": index,
                "title": title,
                "times": times,
                "hero_candidates": hero_candidates,
                "theme_window": windows["theme"],
                "speaking_window": windows["speaking"],
                "speaking_window_verified": "speaking_window" in item,
                "cue_windows": cue_windows,
            }
        )

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = [
        out_dir / f"{job['index']:02d}_{job['title']}.jpg" for job in jobs
    ]
    manifest_target = out_dir / "原生字幕时间点.json"
    contact_target = out_dir / "final_contact_sheet.jpg"
    qa_target = out_dir / "qa-results.json"
    decisions_target = out_dir / "render-decisions.jsonl"
    guarded = [*outputs, contact_target, qa_target, decisions_target]
    if manifest_path != manifest_target:
        guarded.append(manifest_target)
    refuse_existing(guarded, args.overwrite)

    events = []
    qa_items = []
    accepted_outputs = []
    used_signatures = []
    global_times = (
        global_candidate_times(duration, args.global_candidates)
        if args.allow_source_wide_fallback
        else []
    )
    global_cache = {}

    def emit(event, **details):
        payload = {"event": event, **details}
        events.append(payload)
        summary = ", ".join(
            f"{key}={value}"
            for key, value in details.items()
            if key in {"index", "phase", "time", "accepted", "action", "layout", "result", "reason"}
        )
        print(f"[{event}] {summary}")

    for job, out_path in zip(jobs, outputs):
        index = job["index"]
        title = job["title"]
        times = job["times"]
        emit("item_start", index=index, title=title, time=times[0])
        if args.native_layout == "quote-first":
            hero_frame = grab_frame(video, times[0])
            assessment = visual_assessment(hero_frame)
            selection = {
                "time": times[0],
                "frame": hero_frame,
                "assessment": assessment,
                "phase": "quote-first-forced",
                "semantic_alignment": "PASS",
                "semantic_basis": "exact_quote_timestamp_quote_first",
            }
            emit(
                "quote_first_switch",
                index=index,
                layout="quote-first",
                reason="explicit_native_layout",
                original_time=times[0],
                semantic_alignment="PASS",
            )
        elif args.visual_gate == "off":
            hero_frame = grab_frame(video, times[0])
            assessment = visual_assessment(hero_frame)
            selection = {
                "time": times[0],
                "frame": hero_frame,
                "assessment": assessment,
                "phase": "visual-gate-off",
                "semantic_alignment": "PASS",
                "semantic_basis": "exact_quote_timestamp_visual_gate_off",
            }
            emit(
                "visual_gate",
                index=index,
                phase="visual-gate-off",
                time=times[0],
                accepted=True,
                metrics=public_assessment(assessment),
            )
        else:
            selection = choose_visual_hero(
                video,
                times[0],
                job["hero_candidates"],
                duration,
                used_signatures,
                global_times,
                lambda event, **details: emit(event, index=index, **details),
                global_cache=global_cache,
                minimum_distance=args.minimum_visual_distance,
                quote_times=times,
                theme_window=job["theme_window"],
                speaking_window=job["speaking_window"],
                allow_source_wide=args.allow_source_wide_fallback,
                speaking_window_verified=job["speaking_window_verified"],
            )
        if selection is None:
            if args.native_layout in {"auto", "quote-first"}:
                hero_frame = grab_frame(video, times[0])
                assessment = visual_assessment(hero_frame)
                selection = {
                    "time": times[0],
                    "frame": hero_frame,
                    "assessment": assessment,
                    "phase": "quote-first-fallback",
                    "semantic_alignment": "PASS",
                    "semantic_basis": "exact_quote_timestamp_quote_first",
                }
                emit(
                    "quote_first_switch",
                    index=index,
                    action="layout-degrade",
                    layout="quote-first",
                    reason="semantic_window_exhausted_low_visual_value",
                    original_time=times[0],
                    semantic_alignment="PASS",
                )
            else:
                reason = "语义窗口内没有候选通过 visual gate，且已强制非 quote-first 布局"
                emit("qa", index=index, result="FAIL", reason=reason)
                qa_items.append(
                    {
                        "index": index,
                        "title": title,
                        "file": None,
                        "result": "FAIL",
                        "reason": reason,
                        "original_time": times[0],
                        "semantic_alignment": "FAIL",
                        "semantic_alignment_reason": "no_eligible_semantic_hero",
                    }
                )
                continue

        phase = selection["phase"]
        if args.native_layout == "auto":
            layout = (
                "quote-first"
                if phase in {"quote-first-fallback", "quote-first-forced"}
                else classify_native_layout(
                    args.band_top,
                    args.band_bottom,
                    low_visual=phase not in {"original", "visual-gate-off"},
                )
            )
        elif args.native_layout == "bottom":
            layout = "bottom-band"
        elif args.native_layout == "centered":
            layout = "centered-band"
        elif args.native_layout == "preserve":
            layout = "preserve-band"
        elif args.native_layout == "quote-first":
            layout = "quote-first"
        else:
            layout = "legacy"
        emit(
            "layout",
            index=index,
            layout=layout,
            reason=(
                "semantic_window_exhausted_quote_first"
                if phase == "quote-first-fallback"
                else "explicit_quote_first"
                if phase == "quote-first-forced"
                else "low_visual_candidate_replaced"
                if phase not in {"original", "visual-gate-off"}
                else "subtitle_band_position"
            ),
        )

        render_times = list(times)
        render_report = render_one(
            video,
            render_times,
            out_path,
            args.aspect,
            args.width,
            args.band_top,
            args.band_bottom,
            args.hero_fraction,
            hero_frame=selection["frame"],
            native_layout=layout,
        )
        expected_height = round(args.width * args.aspect[1] / args.aspect[0])
        with Image.open(out_path) as rendered:
            dimensions_ok = rendered.size == (args.width, expected_height)
            rendered_copy = rendered.convert("RGB")
        strip_presence = FINAL_QA.assess_native_strips(
            rendered_copy,
            hero_height=render_report["hero_height"],
            strip_heights=render_report["strip_heights"],
        )
        temporal_evidence = [None] * len(render_report["strip_heights"])
        failed_strips = [
            item["index"] - 1
            for item in strip_presence["items"]
            if not item["passed"]
        ]
        if failed_strips and job["cue_windows"] is not None:
            repaired_bands = [list(value) for value in render_report["strip_bands"]]
            repaired_any = False
            for strip_index in failed_strips:
                time_index = strip_index + 1
                cue_start, _cue_end = job["cue_windows"][time_index]
                active_frame = grab_frame(video, render_times[time_index])
                pre_cue_frame = grab_frame(video, max(0.0, cue_start - 0.18))
                evidence = FINAL_QA.detect_temporal_subtitle_band(
                    active_frame,
                    pre_cue_frame,
                    configured_band=repaired_bands[strip_index],
                )
                temporal_evidence[strip_index] = evidence
                if evidence["detected"]:
                    repaired_bands[strip_index] = [
                        evidence["top"], evidence["bottom"]
                    ]
                    evidence["final_band_overlap"] = 1.0
                    repaired_any = True
                    emit(
                        "auto_repair",
                        index=index,
                        action="dynamic-strip-band",
                        phase="layout_crop",
                        strip_index=strip_index + 1,
                        reason="final_native_strip_presence_failed",
                        original_band=render_report["strip_bands"][strip_index],
                        repaired_band=repaired_bands[strip_index],
                        temporal_evidence=evidence,
                    )
                else:
                    evidence["final_band_overlap"] = 0.0
            if repaired_any:
                render_report = render_one(
                    video,
                    render_times,
                    out_path,
                    args.aspect,
                    args.width,
                    args.band_top,
                    args.band_bottom,
                    args.hero_fraction,
                    hero_frame=selection["frame"],
                    native_layout=layout,
                    strip_bands=repaired_bands,
                )
                with Image.open(out_path) as rendered:
                    rendered_copy = rendered.convert("RGB")
                strip_presence = FINAL_QA.assess_native_strips(
                    rendered_copy,
                    hero_height=render_report["hero_height"],
                    strip_heights=render_report["strip_heights"],
                    temporal_evidence=temporal_evidence,
                )
        # If the planned midpoint itself has no subtitle pixels, probe a fixed
        # set of times inside that cue only.  This is the semantic-window
        # repair stage: it cannot borrow text from another cue or source.
        remaining_failed = [
            item["index"] - 1
            for item in strip_presence["items"]
            if not item["passed"]
        ]
        cue_probe_attempted = False
        if remaining_failed and job["cue_windows"] is not None:
            working_bands = [list(value) for value in render_report["strip_bands"]]
            for strip_index in remaining_failed:
                time_index = strip_index + 1
                cue_start, cue_end = job["cue_windows"][time_index]
                span = cue_end - cue_start
                probe_times = []
                for fraction in (0.15, 0.30, 0.50, 0.70, 0.85):
                    value = round(cue_start + span * fraction, 3)
                    if (
                        cue_start <= value <= cue_end
                        and abs(value - render_times[time_index]) >= 0.02
                        and value not in probe_times
                    ):
                        probe_times.append(value)
                accepted_probe = None
                for probe_time in probe_times:
                    cue_probe_attempted = True
                    active_frame = grab_frame(video, probe_time)
                    pre_cue_frame = grab_frame(video, max(0.0, cue_start - 0.18))
                    evidence = FINAL_QA.detect_temporal_subtitle_band(
                        active_frame,
                        pre_cue_frame,
                        configured_band=working_bands[strip_index],
                    )
                    trial_bands = [list(value) for value in working_bands]
                    if evidence["detected"]:
                        trial_bands[strip_index] = [evidence["top"], evidence["bottom"]]
                        evidence["final_band_overlap"] = 1.0
                    else:
                        evidence["final_band_overlap"] = 0.0
                    trial_times = list(render_times)
                    trial_times[time_index] = probe_time
                    trial_report = render_one(
                        video,
                        trial_times,
                        out_path,
                        args.aspect,
                        args.width,
                        args.band_top,
                        args.band_bottom,
                        args.hero_fraction,
                        hero_frame=selection["frame"],
                        native_layout=layout,
                        strip_bands=trial_bands,
                    )
                    with Image.open(out_path) as rendered:
                        trial_rendered = rendered.convert("RGB")
                    trial_evidence = list(temporal_evidence)
                    trial_evidence[strip_index] = evidence
                    trial_presence = FINAL_QA.assess_native_strips(
                        trial_rendered,
                        hero_height=trial_report["hero_height"],
                        strip_heights=trial_report["strip_heights"],
                        temporal_evidence=trial_evidence,
                    )
                    if trial_presence["items"][strip_index]["passed"]:
                        accepted_probe = (
                            probe_time,
                            trial_times,
                            trial_bands,
                            trial_evidence,
                            trial_report,
                            trial_rendered,
                            trial_presence,
                        )
                        break
                if accepted_probe is not None:
                    (
                        probe_time,
                        render_times,
                        working_bands,
                        temporal_evidence,
                        render_report,
                        rendered_copy,
                        strip_presence,
                    ) = accepted_probe
                    emit(
                        "auto_repair",
                        index=index,
                        action="same-cue-window-time",
                        phase="semantic_window",
                        strip_index=strip_index + 1,
                        original_time=times[time_index],
                        repaired_time=probe_time,
                        cue_window=[cue_start, cue_end],
                        reason="planned_cue_time_missing_final_subtitle_evidence",
                    )
            if cue_probe_attempted:
                # The last rejected probe also wrote the output.  Re-render
                # the accumulated accepted state before final QA.
                render_report = render_one(
                    video,
                    render_times,
                    out_path,
                    args.aspect,
                    args.width,
                    args.band_top,
                    args.band_bottom,
                    args.hero_fraction,
                    hero_frame=selection["frame"],
                    native_layout=layout,
                    strip_bands=working_bands,
                )
                with Image.open(out_path) as rendered:
                    rendered_copy = rendered.convert("RGB")
                strip_presence = FINAL_QA.assess_native_strips(
                    rendered_copy,
                    hero_height=render_report["hero_height"],
                    strip_heights=render_report["strip_heights"],
                    temporal_evidence=temporal_evidence,
                )
        horizontal_ok = render_report["subtitle_horizontal_retention"] >= 0.98
        if not horizontal_ok and layout != "preserve-band":
            emit(
                "auto_repair",
                index=index,
                action="layout-degrade",
                reason="subtitle_horizontal_retention_below_98_percent",
            )
            render_report = render_one(
                video,
                render_times,
                out_path,
                args.aspect,
                args.width,
                args.band_top,
                args.band_bottom,
                args.hero_fraction,
                hero_frame=selection["frame"],
                native_layout="preserve-band",
                strip_bands=render_report["strip_bands"],
            )
            layout = "preserve-band"
            with Image.open(out_path) as rendered:
                rendered_copy = rendered.convert("RGB")
            strip_presence = FINAL_QA.assess_native_strips(
                rendered_copy,
                hero_height=render_report["hero_height"],
                strip_heights=render_report["strip_heights"],
                temporal_evidence=temporal_evidence,
            )
            horizontal_ok = render_report["subtitle_horizontal_retention"] >= 0.98
        subtitle_ok = horizontal_ok and strip_presence["all_pass"]

        qa_source = (
            selection["frame"]
            if render_report["hero_source_kind"] == "selected_hero_frame"
            else grab_frame(video, times[0])
        )
        final_renderability = FINAL_QA.assess_final_renderability(
            qa_source,
            rendered_copy,
            source_crop_box=render_report["hero_source_crop_box"],
            hero_box=render_report["hero_content_box"],
            source_visual=(
                None
                if args.visual_gate == "off"
                else public_assessment(visual_assessment(qa_source))
            ),
            layout_mode=(
                "quote_first"
                if layout == "quote-first"
                else "contain"
                if layout == "centered-band"
                else "fit"
            ),
        )

        visual_ok = (
            final_renderability["final_renderability"]["passed"]
            and (
                layout == "quote-first"
                or selection["assessment"]["passed"]
                or args.visual_gate == "off"
            )
        )
        semantic_alignment = selection.get("semantic_alignment", "PASS")
        passed = (
            dimensions_ok
            and subtitle_ok
            and visual_ok
            and semantic_alignment != "FAIL"
        )
        if not passed:
            result = "FAIL"
        elif semantic_alignment == "PARTIAL_PASS":
            result = "PARTIAL_PASS"
        else:
            result = "PASS"
        emit(
            "qa",
            index=index,
            result=result,
            layout=layout,
            semantic_alignment=semantic_alignment,
            semantic_basis=selection.get("semantic_basis"),
        )
        qa_items.append(
            {
                "index": index,
                "title": title,
                "file": out_path.name,
                "result": result,
                "original_time": times[0],
                "planned_times": times,
                "render_times": render_times,
                "hero_time": selection["time"],
                "repair_phase": phase,
                "layout": layout,
                "semantic_alignment": semantic_alignment,
                "semantic_alignment_reason": selection.get("semantic_basis"),
                "theme_window": job["theme_window"],
                "speaking_window": job["speaking_window"],
                "speaking_window_verified": job["speaking_window_verified"],
                "dimensions_ok": dimensions_ok,
                "subtitle_horizontal_retention": render_report[
                    "subtitle_horizontal_retention"
                ],
                "subtitle_integrity": "PASS" if subtitle_ok else "FAIL",
                "native_subtitle_presence": strip_presence,
                "strip_bands": render_report["strip_bands"],
                "subtitle_strip_strategy": render_report[
                    "subtitle_strip_strategy"
                ],
                "visual_centering_x": render_report["visual_centering_x"],
                "hero_visual": public_assessment(selection["assessment"]),
                "visual_strategy": (
                    "native_quote_pixels"
                    if layout == "quote-first"
                    else "selected_video_frame"
                ),
                "visual_strategy_ok": visual_ok,
                "final_renderability": final_renderability,
                "quote_detection": render_report["quote_detection"],
                "note": (
                    "同源全片视觉 fallback；字幕仍来自原时间点，主题对应需人工确认"
                    if phase == "source-wide-fallback"
                    else "语义窗口内均为低视觉价值画面；已改用原时间点的原生字幕像素作为主视觉"
                    if phase == "quote-first-fallback"
                    else None
                ),
            }
        )
        if passed:
            accepted_outputs.append(out_path)
            if not phase.startswith("quote-first"):
                used_signatures.append(selection["assessment"]["signature"])

    if manifest_path != manifest_target:
        shutil.copyfile(manifest_path, manifest_target)
    if accepted_outputs:
        contact_sheet(accepted_outputs, contact_target)
        print(f"总览图: {contact_target}")
    overall = (
        "FAIL"
        if any(item["result"] == "FAIL" for item in qa_items)
        else "PARTIAL_PASS"
        if any(item["result"] == "PARTIAL_PASS" for item in qa_items)
        else "PASS"
    )
    qa_payload = {
        "overall": overall,
        "semantic_alignment": (
            "FAIL"
            if any(item.get("semantic_alignment") == "FAIL" for item in qa_items)
            else "PARTIAL_PASS"
            if any(
                item.get("semantic_alignment") == "PARTIAL_PASS" for item in qa_items
            )
            else "PASS"
        ),
        "mode": "native",
        "expected_images": len(jobs),
        "accepted_images": len(accepted_outputs),
        "visual_gate": args.visual_gate,
        "allow_source_wide_fallback": args.allow_source_wide_fallback,
        "items": qa_items,
    }
    qa_target.write_text(
        json.dumps(qa_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    decisions_target.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )
    print(f"逐图 QA: {qa_target} ({overall})")
    print(f"决策日志: {decisions_target}")
    if overall == "FAIL":
        raise SystemExit("visual QA 最终仍失败；详情见 qa-results.json")


def command_render_script(args):
    video = input_file(args.video, "视频")
    script_path = input_file(args.script, "台词脚本")
    _, _, duration = video_metadata(video)
    try:
        data = json.loads(script_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"台词脚本 JSON 格式错误（第 {exc.lineno} 行第 {exc.colno} 列）: "
            f"{exc.msg}"
        ) from None
    lines = normalize_script_lines(data, duration)
    out_path = Path(args.out).expanduser().resolve()
    qa_path = out_path.with_name(out_path.stem + ".qa-results.json")
    decisions_path = out_path.with_name(out_path.stem + ".render-decisions.jsonl")
    refuse_existing([out_path, qa_path, decisions_path], args.overwrite)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    font_path = None
    if args.font:
        font_path = str(input_file(args.font, "字体"))
    events = []

    def emit(event, **details):
        events.append({"sequence": len(events) + 1, "event": event, **details})

    first_frame = grab_frame(video, lines[0]["t"])
    source_visual = visual_assessment(first_frame)
    report = scripted_render_one(
        video,
        lines,
        out_path,
        args.aspect,
        args.width,
        args.band_center,
        args.hero_fraction,
        font_path,
        args.font_size,
        hero_layout="fit",
    )
    with Image.open(out_path) as opened:
        rendered = opened.convert("RGB")
    initial_qa = FINAL_QA.assess_final_renderability(
        first_frame,
        rendered,
        source_crop_box=report["source_crop_box"],
        hero_box=report["hero_box"],
        source_visual=public_assessment(source_visual),
        layout_mode="fit",
    )
    portrait_crop = (
        first_frame.width / first_frame.height
        > args.width / report["hero_height"] + 0.03
    )
    unknown_subject_in_crop = (
        portrait_crop and source_visual.get("subject_center_x") is None
    )
    initial_passed = (
        initial_qa["final_renderability"]["passed"]
        and not unknown_subject_in_crop
    )
    emit(
        "qa_result",
        stage="initial",
        layout="fit",
        passed=initial_passed,
        unknown_subject_in_portrait_crop=unknown_subject_in_crop,
        final_renderability=initial_qa,
        source_visual=public_assessment(source_visual),
    )
    repaired = False
    final_qa = initial_qa
    if not initial_passed:
        repaired = True
        emit(
            "auto_repair",
            stage="layout_crop",
            action="contain-blur-hero",
            reason="fit_layout_final_renderability_failed",
        )
        report = scripted_render_one(
            video,
            lines,
            out_path,
            args.aspect,
            args.width,
            args.band_center,
            args.hero_fraction,
            font_path,
            args.font_size,
            hero_layout="contain",
        )
        with Image.open(out_path) as opened:
            rendered = opened.convert("RGB")
        final_qa = FINAL_QA.assess_final_renderability(
            first_frame,
            rendered,
            source_crop_box=report["source_crop_box"],
            hero_box=report["hero_box"],
            source_visual=public_assessment(source_visual),
            layout_mode="contain",
        )
        emit(
            "qa_result",
            stage="layout_crop",
            layout="contain",
            passed=final_qa["final_renderability"]["passed"],
            final_renderability=final_qa,
        )
    qa_payload = {
        "overall": (
            "PASS" if final_qa["final_renderability"]["passed"] else "FAIL"
        ),
        "mode": "script",
        "file": out_path.name,
        "layout": report["layout"],
        "layout_repaired": repaired,
        "source_visual": public_assessment(source_visual),
        "initial_final_renderability": initial_qa,
        "final_renderability": final_qa,
    }
    qa_path.write_text(
        json.dumps(qa_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    decisions_path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )
    print(f"逐图 QA: {qa_path} ({qa_payload['overall']})")
    print(f"决策日志: {decisions_path}")
    if qa_payload["overall"] == "FAIL":
        raise SystemExit("final renderability QA 失败；详情见 QA sidecar")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sample = sub.add_parser("sample", help="生成带时间点的候选帧总览")
    sample.add_argument("video")
    sample.add_argument("--start", type=float, default=0.0)
    sample.add_argument("--end", type=float)
    sample.add_argument("--interval", type=float)
    sample.add_argument(
        "-t",
        "--time",
        dest="times",
        action="append",
        type=float,
        help="文字稿候选时间点，可重复传入；每个时间点生成前、中、后三帧",
    )
    sample.add_argument(
        "--around",
        type=float,
        default=0.8,
        help="配合 --time 使用的前后偏移秒数（默认 0.8）",
    )
    sample.add_argument("--max-frames", type=int, default=48)
    sample.add_argument("--columns", type=int, default=4)
    sample.add_argument("--thumb-width", type=int, default=320)
    sample.add_argument("--out", default="candidate-contact-sheet.jpg")
    sample.add_argument("--overwrite", action="store_true")
    sample.set_defaults(func=command_sample)

    band = sub.add_parser("band", help="预览字幕裁切区域")
    band.add_argument("video")
    band.add_argument("-t", "--time", type=float, required=True)
    band.add_argument("--band-top", type=float, default=0.78)
    band.add_argument("--band-bottom", type=float, default=0.96)
    band.add_argument("--out", default="band-preview.jpg")
    band.add_argument("--overwrite", action="store_true")
    band.set_defaults(func=command_band)

    render = sub.add_parser("render", help="按 manifest 渲染整套拼图")
    render.add_argument("video")
    render.add_argument("--manifest", required=True)
    render.add_argument("--out-dir", required=True)
    render.add_argument("--aspect", type=parse_aspect, default=parse_aspect("3:4"))
    render.add_argument("--width", type=int, default=1440)
    render.add_argument("--band-top", type=float, default=0.78)
    render.add_argument("--band-bottom", type=float, default=0.96)
    render.add_argument(
        "--hero-fraction",
        type=float,
        help="主画面高度比例；默认按字幕条数量自动保持紧凑密度",
    )
    render.add_argument(
        "--native-layout",
        choices=("auto", "bottom", "centered", "preserve", "quote-first", "legacy"),
        default="auto",
        help="原生字幕主图布局；auto 会在语义候选耗尽后改用 quote-first",
    )
    render.add_argument(
        "--visual-gate",
        choices=("auto", "off"),
        default="auto",
        help="在 final 入选前检查空镜、低信息量和近重复画面（默认 auto）",
    )
    render.add_argument(
        "--global-candidates",
        type=int,
        default=32,
        help="显式开启全片兼容 fallback 时的候选数（默认 32）",
    )
    render.add_argument(
        "--allow-source-wide-fallback",
        action="store_true",
        help="兼容 Phase 1 的全片换主图；结果仅能是 PARTIAL_PASS",
    )
    render.add_argument(
        "--minimum-visual-distance",
        type=float,
        default=0.035,
        help="final 主图之间的最小平均像素差（0–1，默认 0.035）",
    )
    render.add_argument("--overwrite", action="store_true")
    render.set_defaults(func=command_render)

    scripted = sub.add_parser(
        "render-script",
        help="按时间点和台词 JSON 绘制紧凑字幕拼图",
    )
    scripted.add_argument("video")
    scripted.add_argument("--script", required=True)
    scripted.add_argument("--out", required=True)
    scripted.add_argument("--aspect", type=parse_aspect, default=parse_aspect("3:4"))
    scripted.add_argument("--width", type=int, default=1440)
    scripted.add_argument(
        "--band-center",
        type=float,
        default=0.88,
        help="字幕条在源画面中的垂直中心比例（默认 0.88）",
    )
    scripted.add_argument(
        "--hero-fraction",
        type=float,
        help="主画面高度比例；默认按台词数量自动保持紧凑密度",
    )
    scripted.add_argument("--font", help="中文字体文件；未指定时尝试系统字体")
    scripted.add_argument("--font-size", type=int, help="基础字号，过长台词仍会自动缩小")
    scripted.add_argument("--overwrite", action="store_true")
    scripted.set_defaults(func=command_render_script)

    args = parser.parse_args()
    if hasattr(args, "band_top") and not 0 <= args.band_top < args.band_bottom <= 1:
        raise SystemExit("字幕区域必须满足 0 <= top < bottom <= 1")
    if getattr(args, "width", 1) <= 0:
        raise SystemExit("--width 必须为正数")
    if hasattr(args, "band_center") and not 0.1 <= args.band_center <= 0.98:
        raise SystemExit("--band-center 必须在 0.10–0.98 之间")
    if getattr(args, "font_size", None) is not None and args.font_size < 12:
        raise SystemExit("--font-size 不能小于 12")
    if getattr(args, "global_candidates", 1) <= 0:
        raise SystemExit("--global-candidates 必须为正整数")
    if not 0 <= getattr(args, "minimum_visual_distance", 0.0) <= 1:
        raise SystemExit("--minimum-visual-distance 必须在 0–1 之间")
    if (
        hasattr(args, "hero_fraction")
        and args.hero_fraction is not None
        and not 0.25 <= args.hero_fraction <= 0.85
    ):
        raise SystemExit("--hero-fraction 必须在 0.25–0.85 之间")
    args.func(args)


if __name__ == "__main__":
    main()
