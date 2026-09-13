#!/usr/bin/env python3
"""Deterministic final-layout QA and selector/render repair orchestration.

This module deliberately has no dependency on ``content_ranking.py``.  Ranking
outputs enter as an ordered selected list plus an ordered reserve list; this
layer only decides whether a candidate is renderable and when the next ranked
reserve may be promoted.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from PIL import Image, ImageChops, ImageFilter, ImageStat


REPAIR_STAGE_ORDER = ("layout_crop", "semantic_window", "legal_fallback")


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(upper, max(lower, value))


def _box_area(box: Sequence[float]) -> float:
    left, top, right, bottom = box
    return max(0.0, right - left) * max(0.0, bottom - top)


def _intersection(left: Sequence[float], right: Sequence[float]) -> list[float]:
    return [
        max(left[0], right[0]),
        max(left[1], right[1]),
        min(left[2], right[2]),
        min(left[3], right[3]),
    ]


def fit_crop_box(
    source_size: Sequence[int],
    target_size: Sequence[int],
    centering: Sequence[float] = (0.5, 0.5),
) -> list[float]:
    """Return the exact source rectangle retained by ``ImageOps.fit``.

    Coordinates use the source image coordinate system and may be fractional,
    matching Pillow's scale-then-crop geometry.
    """
    source_width, source_height = source_size
    target_width, target_height = target_size
    if min(source_width, source_height, target_width, target_height) <= 0:
        raise ValueError("source and target dimensions must be positive")
    center_x, center_y = centering
    if not 0 <= center_x <= 1 or not 0 <= center_y <= 1:
        raise ValueError("centering values must be in 0..1")
    scale = max(target_width / source_width, target_height / source_height)
    retained_width = target_width / scale
    retained_height = target_height / scale
    left = (source_width - retained_width) * center_x
    top = (source_height - retained_height) * center_y
    return [
        round(left, 6),
        round(top, 6),
        round(left + retained_width, 6),
        round(top + retained_height, 6),
    ]


def _active_pixel_metrics(image: Image.Image) -> dict[str, float]:
    """Measure non-flat content in the actual rendered pixels."""
    sample = image.convert("RGB")
    maximum = 240
    if sample.width > maximum or sample.height > maximum:
        scale = min(maximum / sample.width, maximum / sample.height)
        sample = sample.resize(
            (max(1, round(sample.width * scale)), max(1, round(sample.height * scale))),
            Image.Resampling.BILINEAR,
        )
    gray = sample.convert("L")
    width, height = gray.size
    histogram = gray.histogram()
    binned = [sum(histogram[index : index + 8]) for index in range(0, 256, 8)]
    peak_bin = max(range(len(binned)), key=binned.__getitem__)
    background = peak_bin * 8 + 3.5
    edges = gray.filter(ImageFilter.FIND_EDGES)
    active = []
    row_ratios = []
    column_counts = [0] * width
    for y in range(height):
        row_count = 0
        for x in range(width):
            pixel = gray.getpixel((x, y))
            edge = edges.getpixel((x, y))
            enabled = abs(pixel - background) >= 28 or edge >= 32
            active.append(enabled)
            if enabled:
                row_count += 1
                column_counts[x] += 1
        row_ratios.append(row_count / width)
    active_ratio = sum(active) / max(1, width * height)

    def edge_blank_run(values: Sequence[float], threshold: float = 0.015) -> int:
        leading = 0
        for value in values:
            if value > threshold:
                break
            leading += 1
        trailing = 0
        for value in reversed(values):
            if value > threshold:
                break
            trailing += 1
        return max(leading, trailing)

    column_ratios = [count / height for count in column_counts]
    largest_edge_blank_ratio = max(
        edge_blank_run(row_ratios) / height,
        edge_blank_run(column_ratios) / width,
    )
    return {
        "active_content_ratio": round(active_ratio, 4),
        "largest_edge_blank_ratio": round(largest_edge_blank_ratio, 4),
        "background_luminance": round(float(background), 4),
    }


def _cropped_text_edge_metrics(image: Image.Image) -> dict[str, Any]:
    """Detect repeated high-contrast text strokes cut by both portrait edges."""
    sample = image.convert("L").resize((360, 336), Image.Resampling.BILINEAR)
    edges = sample.filter(ImageFilter.FIND_EDGES)
    mean = ImageStat.Stat(sample).mean[0]
    text_pixel = (
        (lambda value: value <= 70) if mean >= 155 else (lambda value: value >= 210)
    )
    margin = 8
    side_rows = []
    side_ratios = []
    for side in ("left", "right"):
        rows = []
        count = 0
        for y in range(sample.height):
            row_count = 0
            for offset in range(margin):
                x = offset if side == "left" else sample.width - 1 - offset
                if text_pixel(sample.getpixel((x, y))) and edges.getpixel((x, y)) >= 40:
                    count += 1
                    row_count += 1
            if row_count >= 2:
                rows.append(y)
        clusters = []
        for y in rows:
            if not clusters or y - clusters[-1][-1] > 2:
                clusters.append([y])
            else:
                clusters[-1].append(y)
        compact = [cluster for cluster in clusters if len(cluster) >= 3]
        side_rows.append(len(compact))
        side_ratios.append(count / (margin * sample.height))
    detected = (
        min(side_ratios) >= 0.02
        and min(side_rows) >= 3
        and sum(side_rows) >= 8
    )
    return {
        "detected": detected,
        "left_contact_ratio": round(side_ratios[0], 4),
        "right_contact_ratio": round(side_ratios[1], 4),
        "left_text_row_clusters": side_rows[0],
        "right_text_row_clusters": side_rows[1],
    }


def assess_final_renderability(
    source: Image.Image,
    rendered: Image.Image,
    *,
    source_crop_box: Sequence[float],
    hero_box: Sequence[int] | None = None,
    subject_box: Sequence[float] | None = None,
    source_visual: Mapping[str, Any] | None = None,
    layout_mode: str = "fit",
    minimum_subject_retention: float = 0.85,
    minimum_active_content_ratio: float = 0.24,
    maximum_edge_blank_ratio: float = 0.45,
) -> dict[str, Any]:
    """Assess the actual portrait render and the crop that produced its hero.

    ``subject_box`` should come from a face/person detector when available.  A
    missing box remains explicit instead of treating the entire source frame as
    a person.  Crop geometry is still checked in that case.
    """
    crop = [float(value) for value in source_crop_box]
    if len(crop) != 4 or _box_area(crop) <= 0:
        raise ValueError("source_crop_box must be a non-empty [x0,y0,x1,y1] box")
    source_bounds = [0.0, 0.0, float(source.width), float(source.height)]
    geometry_safe = all(
        abs(value - bounded) <= 1e-4
        for value, bounded in zip(crop, _intersection(crop, source_bounds))
    )

    if hero_box is None:
        hero = rendered.convert("RGB")
        normalized_hero_box = [0, 0, rendered.width, rendered.height]
    else:
        normalized_hero_box = [int(value) for value in hero_box]
        if len(normalized_hero_box) != 4:
            raise ValueError("hero_box must contain four coordinates")
        hero = rendered.crop(normalized_hero_box).convert("RGB")
        if hero.width <= 0 or hero.height <= 0:
            raise ValueError("hero_box must select a non-empty render region")

    pixel_metrics = _active_pixel_metrics(hero)
    active_ratio = pixel_metrics["active_content_ratio"]
    edge_blank_ratio = pixel_metrics["largest_edge_blank_ratio"]
    if layout_mode == "quote_first":
        effective_minimum_active = min(minimum_active_content_ratio, 0.015)
    elif layout_mode == "contain":
        # A contained 16:9 frame intentionally includes blurred/letterboxed
        # support pixels around the full source.  The retained source remains
        # independently protected by crop_safety, so use a slightly lower
        # content-density floor than a destructive fit crop.
        effective_minimum_active = min(minimum_active_content_ratio, 0.16)
    else:
        effective_minimum_active = minimum_active_content_ratio
    effective_maximum_blank = (
        max(maximum_edge_blank_ratio, 0.75)
        if layout_mode == "quote_first"
        else maximum_edge_blank_ratio
    )
    excessive_blank = (
        active_ratio < effective_minimum_active
        or edge_blank_ratio > effective_maximum_blank
    )
    cropped_text = _cropped_text_edge_metrics(hero)

    retention = None
    subject_passed = None
    subject_basis = "subject_box_not_available"
    normalized_subject = None
    if subject_box is not None:
        normalized_subject = [float(value) for value in subject_box]
        if len(normalized_subject) != 4 or _box_area(normalized_subject) <= 0:
            raise ValueError("subject_box must be a non-empty [x0,y0,x1,y1] box")
        retention = _clamp(
            _box_area(_intersection(crop, normalized_subject))
            / _box_area(normalized_subject)
        )
        subject_passed = retention >= minimum_subject_retention
        subject_basis = "provided_subject_box_intersection"

    source_low_visual = bool(
        source_visual is not None
        and (
            source_visual.get("passed") is False
            or source_visual.get("document_like") is True
        )
    )
    source_layout_safe = not (layout_mode == "fit" and source_low_visual)
    crop_passed = geometry_safe and subject_passed is not False and source_layout_safe
    active_score = _clamp(active_ratio / effective_minimum_active)
    subject_score = retention if retention is not None else 1.0
    blank_score = 0.0 if excessive_blank else 1.0
    crop_score = 1.0 if crop_passed else 0.0
    score = (
        active_score * 0.35
        + subject_score * 0.30
        + blank_score * 0.20
        + crop_score * 0.15
    )
    passed = (
        crop_passed
        and not excessive_blank
        and not cropped_text["detected"]
        and active_ratio >= effective_minimum_active
    )
    reasons = []
    if active_ratio < effective_minimum_active:
        reasons.append("insufficient_active_content")
    if edge_blank_ratio > effective_maximum_blank:
        reasons.append("excessive_edge_blank_area")
    if not geometry_safe:
        reasons.append("crop_outside_source")
    if subject_passed is False:
        reasons.append("subject_severely_cropped")
    if not source_layout_safe:
        reasons.append("low_visual_or_document_source_requires_contain_layout")
    if cropped_text["detected"]:
        reasons.append("slide_text_crosses_portrait_crop_edges")
    return {
        "final_renderability": {
            "score": round(score, 4),
            "passed": passed,
            "reasons": reasons,
        },
        "subject_retention": {
            "score": round(retention, 4) if retention is not None else None,
            "passed": subject_passed,
            "basis": subject_basis,
            "subject_box": normalized_subject,
        },
        "active_content_ratio": active_ratio,
        "excessive_blank_area": {
            "detected": excessive_blank,
            "largest_edge_blank_ratio": edge_blank_ratio,
            "maximum_allowed_edge_blank_ratio": effective_maximum_blank,
        },
        "crop_safety": {
            "passed": crop_passed and not cropped_text["detected"],
            "geometry_safe": geometry_safe,
            "source_crop_box": [round(value, 4) for value in crop],
            "minimum_subject_retention": minimum_subject_retention,
            "cropped_text_edges": cropped_text,
            "layout_mode": layout_mode,
            "source_visual_passed": (
                source_visual.get("passed") if source_visual is not None else None
            ),
            "source_document_like": (
                source_visual.get("document_like")
                if source_visual is not None
                else None
            ),
        },
        "hero_box": normalized_hero_box,
        "actual_render_size": list(rendered.size),
        "source_size": list(source.size),
    }


def _percentile(values: Sequence[int], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _connected_components(mask: Image.Image) -> list[dict[str, int]]:
    """Return 8-connected components for a small binary image."""
    width, height = mask.size
    pixels = mask.load()
    unseen = {
        (x, y)
        for y in range(height)
        for x in range(width)
        if pixels[x, y]
    }
    components = []
    while unseen:
        start = unseen.pop()
        stack = [start]
        min_x = max_x = start[0]
        min_y = max_y = start[1]
        area = 0
        while stack:
            x, y = stack.pop()
            area += 1
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            for next_y in range(max(0, y - 1), min(height, y + 2)):
                for next_x in range(max(0, x - 1), min(width, x + 2)):
                    point = (next_x, next_y)
                    if point in unseen:
                        unseen.remove(point)
                        stack.append(point)
        components.append(
            {
                "left": min_x,
                "top": min_y,
                "right": max_x + 1,
                "bottom": max_y + 1,
                "width": max_x - min_x + 1,
                "height": max_y - min_y + 1,
                "area": area,
            }
        )
    return components


def _source_text_like_metrics(image: Image.Image) -> dict[str, Any]:
    """Find compact, row-aligned high-contrast components in source pixels.

    This intentionally differs from ``native_subtitle_presence``: it operates
    on source-only pixels inside the generated glyph clearance box and asks
    whether text-like source components occupy that same visual space.  It
    does not validate native subtitles or generated text presence.
    """
    color = image.convert("RGB")
    if color.width > 480:
        scale = 480 / color.width
        color = color.resize(
            (480, max(1, round(color.height * scale))), Image.Resampling.LANCZOS
        )
    gray = color.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    local_background = gray.filter(ImageFilter.MedianFilter(size=9))
    local_difference = ImageChops.difference(gray, local_background)
    width, height = gray.size

    masks = {}
    for polarity in ("bright", "dark", "local_contrast"):
        mask = Image.new("1", gray.size)
        target = mask.load()
        for y in range(height):
            for x in range(width):
                value = gray.getpixel((x, y))
                edge = edges.getpixel((x, y))
                difference = local_difference.getpixel((x, y))
                if polarity == "bright":
                    enabled = value >= 205 and edge >= 28 and difference >= 14
                elif polarity == "dark":
                    enabled = value <= 50 and edge >= 28 and difference >= 14
                else:
                    enabled = edge >= 42 and difference >= 34
                target[x, y] = 255 if enabled else 0
        masks[polarity] = mask

    assessments = []
    for polarity, mask in masks.items():
        components = []
        maximum_area = max(12, round(width * height * 0.035))
        for component in _connected_components(mask):
            component_width = component["width"]
            component_height = component["height"]
            aspect = component_width / max(1, component_height)
            if (
                2 <= component["area"] <= maximum_area
                and 1 <= component_width <= max(8, round(width * 0.20))
                and 2 <= component_height <= max(5, round(height * 0.72))
                and 0.08 <= aspect <= 12.0
            ):
                components.append(component)

        row_tolerance = max(3, round(height * 0.16))
        rows: list[list[dict[str, int]]] = []
        for component in sorted(
            components,
            key=lambda item: ((item["top"] + item["bottom"]) / 2, item["left"]),
        ):
            center = (component["top"] + component["bottom"]) / 2
            matching = next(
                (
                    row
                    for row in rows
                    if abs(
                        center
                        - sum(
                            (item["top"] + item["bottom"]) / 2 for item in row
                        )
                        / len(row)
                    )
                    <= row_tolerance
                ),
                None,
            )
            if matching is None:
                rows.append([component])
            else:
                matching.append(component)
        row_metrics = []
        for row in rows:
            span = (
                max(item["right"] for item in row)
                - min(item["left"] for item in row)
            ) / max(1, width)
            heights = sorted(item["height"] for item in row)
            median_height = heights[len(heights) // 2]
            row_metrics.append(
                {
                    "component_count": len(row),
                    "horizontal_span": span,
                    "median_component_height": median_height,
                    "median_component_height_ratio": median_height / max(1, height),
                }
            )
        strongest = max(
            row_metrics,
            key=lambda item: (item["component_count"], item["horizontal_span"]),
            default={
                "component_count": 0,
                "horizontal_span": 0.0,
                "median_component_height": 0,
                "median_component_height_ratio": 0.0,
            },
        )
        mask_values = (
            mask.get_flattened_data()
            if hasattr(mask, "get_flattened_data")
            else mask.getdata()
        )
        active_pixels = sum(1 for value in mask_values if value)
        assessments.append(
            {
                "polarity": polarity,
                "compact_component_count": len(components),
                "aligned_component_count": strongest["component_count"],
                "aligned_horizontal_span": round(strongest["horizontal_span"], 4),
                "aligned_median_component_height": strongest[
                    "median_component_height"
                ],
                "aligned_median_component_height_ratio": round(
                    strongest["median_component_height_ratio"], 4
                ),
                "active_pixel_ratio": round(active_pixels / max(1, width * height), 4),
            }
        )

    def text_like(item):
        return (
            item["compact_component_count"] >= 4
            and item["aligned_component_count"] >= 4
            and item["aligned_horizontal_span"] >= 0.10
            and item["aligned_median_component_height"] >= 4
            and item["aligned_median_component_height_ratio"] >= 0.06
            and 0.0025 <= item["active_pixel_ratio"] <= 0.18
        )

    detected_assessments = [item for item in assessments if text_like(item)]
    best = max(
        detected_assessments or assessments,
        key=lambda item: (
            item["aligned_component_count"],
            item["aligned_horizontal_span"],
            item["compact_component_count"],
        ),
    )
    detected = bool(detected_assessments)
    return {
        "detected": detected,
        "best_polarity": best["polarity"],
        "compact_component_count": best["compact_component_count"],
        "aligned_component_count": best["aligned_component_count"],
        "aligned_horizontal_span": best["aligned_horizontal_span"],
        "aligned_median_component_height": best[
            "aligned_median_component_height"
        ],
        "aligned_median_component_height_ratio": best[
            "aligned_median_component_height_ratio"
        ],
        "active_pixel_ratio": best["active_pixel_ratio"],
        "polarity_assessments": assessments,
        "sample_size": list(gray.size),
    }


def assess_script_source_text_collision(
    source_only: Image.Image,
    *,
    generated_text_box: Sequence[int],
    line_index: int | None = None,
    region_kind: str = "strip",
) -> dict[str, Any]:
    """Detect source visual text occupying a generated script-text region."""
    if len(generated_text_box) != 4:
        raise ValueError("generated_text_box must contain four coordinates")
    left, top, right, bottom = [int(value) for value in generated_text_box]
    if right <= left or bottom <= top:
        raise ValueError("generated_text_box must select a non-empty region")
    text_height = bottom - top
    clearance_x = max(3, round(text_height * 0.16))
    clearance_y = max(3, round(text_height * 0.22))
    collision_box = [
        max(0, left - clearance_x),
        max(0, top - clearance_y),
        min(source_only.width, right + clearance_x),
        min(source_only.height, bottom + clearance_y),
    ]
    if collision_box[2] <= collision_box[0] or collision_box[3] <= collision_box[1]:
        raise ValueError("generated_text_box does not intersect source pixels")
    metrics = _source_text_like_metrics(source_only.crop(collision_box))
    collision = metrics["detected"]
    return {
        "passed": not collision,
        "collision_detected": collision,
        "reason": (
            "source_text_overlaps_generated_script_clearance"
            if collision
            else None
        ),
        "line_index": line_index,
        "region_kind": region_kind,
        "generated_text_box": [left, top, right, bottom],
        "collision_box": collision_box,
        "metrics": metrics,
        "method": "source_only_text_components_vs_generated_bbox_v1",
    }


def assess_script_collisions(
    items: Sequence[Mapping[str, Any]],
    *,
    expected_line_count: int | None = None,
) -> dict[str, Any]:
    """Aggregate already measured script/source collision items."""
    normalized = [dict(item) for item in items]
    expected = len(normalized) if expected_line_count is None else expected_line_count
    if expected < 1:
        coverage_passed = False
    else:
        line_indexes = [item.get("line_index") for item in normalized]
        coverage_passed = (
            len(normalized) == expected
            and all(isinstance(index, int) for index in line_indexes)
            and sorted(line_indexes) == list(range(1, expected + 1))
        )
    return {
        "all_pass": coverage_passed
        and all(item.get("passed") is True for item in normalized),
        "coverage_passed": coverage_passed,
        "required_line_count": expected,
        "measured_line_count": len(normalized),
        "passed_line_count": sum(item.get("passed") is True for item in normalized),
        "collision_line_indexes": [
            item.get("line_index")
            for item in normalized
            if item.get("collision_detected") is True
        ],
        "items": normalized,
        "policy": "source_visual_text_must_clear_generated_script_text",
    }


def detect_temporal_subtitle_band(
    active_frame: Image.Image,
    pre_cue_frame: Image.Image,
    *,
    configured_band: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Locate a changing burned-in subtitle band from cue temporal evidence."""
    if active_frame.size != pre_cue_frame.size:
        raise ValueError("temporal evidence frames must have identical dimensions")
    sample_width = 320
    sample_height = max(1, round(active_frame.height * sample_width / active_frame.width))
    size = (sample_width, sample_height)
    active = active_frame.convert("L").resize(size, Image.Resampling.BILINEAR)
    before = pre_cue_frame.convert("L").resize(size, Image.Resampling.BILINEAR)
    difference = ImageChops.difference(active, before)
    row_scores = [
        ImageStat.Stat(difference.crop((0, y, sample_width, y + 1))).mean[0]
        for y in range(sample_height)
    ]
    smoothed = []
    for y in range(sample_height):
        values = row_scores[max(0, y - 2) : min(sample_height, y + 3)]
        smoothed.append(sum(values) / len(values))
    baseline = _percentile([round(value) for value in smoothed], 0.50)
    peak = max(smoothed, default=0.0)
    threshold = baseline + max(2.5, (peak - baseline) * 0.42)
    rows = [y for y, score in enumerate(smoothed) if score >= threshold]
    clusters = []
    for y in rows:
        if not clusters or y - clusters[-1][-1] > 3:
            clusters.append([y])
        else:
            clusters[-1].append(y)
    cluster = max(
        clusters,
        key=lambda values: sum(smoothed[y] for y in values),
        default=None,
    )
    detected = cluster is not None and peak >= baseline + 3.0
    if detected:
        pad = max(5, round(sample_height * 0.035))
        top = max(0.0, (min(cluster) - pad) / sample_height)
        bottom = min(1.0, (max(cluster) + 1 + pad) / sample_height)
    else:
        top, bottom = (configured_band or (0.0, 1.0))
    overlap = None
    if configured_band is not None and detected:
        configured_top, configured_bottom = configured_band
        overlap = max(
            0.0,
            min(bottom, configured_bottom) - max(top, configured_top),
        ) / max(1e-9, bottom - top)
    return {
        "detected": detected,
        "top": round(top, 4),
        "bottom": round(bottom, 4),
        "peak_row": smoothed.index(peak) if smoothed else None,
        "sample_height": sample_height,
        "peak_score": round(peak, 4),
        "baseline_score": round(baseline, 4),
        "configured_band_overlap": round(overlap, 4) if overlap is not None else None,
        "method": "cue_start_minus_0_18_temporal_row_difference_v1",
    }


def native_subtitle_presence(strip: Image.Image) -> dict[str, Any]:
    """Return a deterministic presence score for one final rendered strip.

    This is a pixel-presence gate, not OCR and not a transcript correctness
    claim.  It looks for compact, high-contrast, horizontally distributed edge
    structure in the final strip pixels.
    """
    color = strip.convert("RGB")
    # `contain-full-band` can add uniform black side padding around a light
    # subtitle card.  Remove only edge-connected columns that are virtually
    # all black; otherwise those padding pixels hide dark-on-light text from
    # the row-polarity estimator.
    original_width = color.width

    def dark_padding_column(x: int) -> bool:
        dark = sum(max(color.getpixel((x, y))) <= 18 for y in range(color.height))
        return dark / max(1, color.height) >= 0.96

    left = 0
    while left < color.width and dark_padding_column(left):
        left += 1
    right = color.width
    while right > left and dark_padding_column(right - 1):
        right -= 1
    retained = color.crop((left, 0, right, color.height))
    symmetric_padding = (
        left >= original_width * 0.05
        and original_width - right >= original_width * 0.05
    )
    retained_is_light_card = ImageStat.Stat(retained.convert("L")).mean[0] >= 110
    if (
        right - left >= max(24, round(original_width * 0.30))
        and symmetric_padding
        and retained_is_light_card
    ):
        color = retained
    if color.width > 480:
        scale = 480 / color.width
        color = color.resize(
            (480, max(1, round(color.height * scale))), Image.Resampling.LANCZOS
        )
    gray = color.convert("L")
    width, height = gray.size
    histogram = gray.histogram()
    pixels = [value for value, count in enumerate(histogram) for _ in range(count)]
    low = _percentile(pixels, 0.02)
    high = _percentile(pixels, 0.98)
    contrast_range = high - low
    edges = gray.filter(ImageFilter.FIND_EDGES)

    bright_counts = [
        sum(gray.getpixel((x, y)) >= 225 for x in range(width))
        for y in range(height)
    ]
    dark_counts = [
        sum(gray.getpixel((x, y)) <= 35 for x in range(width))
        for y in range(height)
    ]
    colorful_counts = [
        sum(
            max(color.getpixel((x, y))) - min(color.getpixel((x, y))) >= 80
            and max(color.getpixel((x, y))) >= 130
            for x in range(width)
        )
        for y in range(height)
    ]

    def polarity_candidate(name: str, counts: Sequence[int]):
        median = sorted(counts)[len(counts) // 2]
        peak = max(counts, default=0)
        valid_background = median <= width * 0.18
        return (peak - median if valid_background else -1, name, counts, median, peak)

    peak_excess, polarity, counts, median_count, peak_count = max(
        polarity_candidate("bright_on_dark", bright_counts),
        polarity_candidate("dark_on_light", dark_counts),
        polarity_candidate("saturated_color", colorful_counts),
    )

    def text_pixel(x: int, y: int) -> bool:
        if polarity == "bright_on_dark":
            return gray.getpixel((x, y)) >= 225
        if polarity == "dark_on_light":
            return gray.getpixel((x, y)) <= 35
        red, green, blue = color.getpixel((x, y))
        return max(red, green, blue) - min(red, green, blue) >= 80 and max(
            red, green, blue
        ) >= 130
    row_floor = max(
        5,
        round(median_count * 1.6),
        round(median_count + width * 0.015),
    )
    edge_floor = max(4, round(width * 0.008))
    rows = []
    foreground_by_row: dict[int, list[int]] = {}
    for y in range(height):
        foreground = [
            x
            for x in range(width)
            if text_pixel(x, y)
        ]
        edge_count = sum(edges.getpixel((x, y)) >= 34 for x in range(width))
        if counts[y] >= row_floor and edge_count >= edge_floor:
            rows.append(y)
            foreground_by_row[y] = foreground
    columns = [x for row in rows for x in foreground_by_row[row]]
    horizontal_span = (
        (max(columns) - min(columns) + 1) / width if columns else 0.0
    )
    row_ratio = len(rows) / max(1, height)
    edge_mean = ImageStat.Stat(edges).mean[0]

    active_points = {
        (x, y)
        for y in rows
        for x in foreground_by_row[y]
    }
    compact_components = 0
    while active_points:
        start = active_points.pop()
        stack = [start]
        points = [start]
        while stack:
            x, y = stack.pop()
            for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if neighbor in active_points:
                    active_points.remove(neighbor)
                    stack.append(neighbor)
                    points.append(neighbor)
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        component_width = max(xs) - min(xs) + 1
        component_height = max(ys) - min(ys) + 1
        if (
            len(points) >= 2
            and component_width <= width * 0.30
            and component_height <= height * 0.75
        ):
            compact_components += 1

    score = (
        _clamp(row_ratio / 0.16) * 0.30
        + _clamp(horizontal_span / 0.55) * 0.30
        + _clamp(contrast_range / 90.0) * 0.25
        + _clamp(peak_excess / max(1, width * 0.10)) * 0.10
        + _clamp(compact_components / 12.0) * 0.05
    )
    passed = (
        len(rows) >= max(2, round(height * 0.035))
        and row_ratio <= 0.75
        and horizontal_span >= 0.12
        and contrast_range >= 48
        and peak_excess >= width * 0.05
        and compact_components >= 3
        and score >= 0.50
        and polarity != "saturated_color"
    )
    reasons = []
    if not rows:
        reasons.append("no_dense_contrast_rows")
    if row_ratio > 0.75:
        reasons.append("diffuse_texture_not_subtitle_structure")
    if horizontal_span < 0.12:
        reasons.append("insufficient_horizontal_text_span")
    if contrast_range < 48:
        reasons.append("insufficient_contrast")
    if peak_excess < width * 0.05:
        reasons.append("no_compact_text_row_peak")
    if compact_components < 3:
        reasons.append("insufficient_text_like_components")
    if score < 0.50:
        reasons.append("presence_score_below_threshold")
    if polarity == "saturated_color":
        reasons.append("colored_text_requires_temporal_evidence")
    return {
        "score": round(score, 4),
        "passed": passed,
        "reasons": reasons,
        "metrics": {
            "candidate_row_ratio": round(row_ratio, 4),
            "horizontal_span": round(horizontal_span, 4),
            "contrast_range": round(contrast_range, 4),
            "edge_mean": round(edge_mean, 4),
            "polarity": polarity,
            "row_peak_excess": round(float(peak_excess), 4),
            "compact_component_count": compact_components,
        },
        "method": "final_strip_contrast_edges_v1",
    }


def assess_native_strips(
    rendered: Image.Image,
    *,
    hero_height: int,
    strip_heights: Sequence[int],
    temporal_evidence: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assess every final native strip; the aggregate passes only when all do."""
    if hero_height < 0 or hero_height >= rendered.height:
        raise ValueError("hero_height must leave room for subtitle strips")
    if not strip_heights or any(height <= 0 for height in strip_heights):
        raise ValueError("strip_heights must contain positive values")
    if hero_height + sum(strip_heights) != rendered.height:
        raise ValueError("hero_height and strip_heights must cover the render exactly")
    if temporal_evidence is not None and len(temporal_evidence) != len(strip_heights):
        raise ValueError("temporal_evidence must align one-to-one with final strips")
    items = []
    y = hero_height
    for index, height in enumerate(strip_heights, 1):
        box = [0, y, rendered.width, y + height]
        result = native_subtitle_presence(rendered.crop(box))
        pixel_presence_passed = result["passed"]
        temporal = temporal_evidence[index - 1] if temporal_evidence is not None else None
        temporal_passed = (
            True
            if temporal is None
            else temporal.get("detected") is True
            and (
                temporal.get("final_band_overlap") is None
                or temporal.get("final_band_overlap") >= 0.75
            )
        )
        # Large subtitle glyphs can merge into a few connected components
        # after a 144 px strip is downsampled.  Accept that one narrow failure
        # only when the final pixels still show strong localized text rows and
        # the cue-before/cue-active temporal detector independently confirms
        # that the retained band is the changing subtitle band.
        metrics = result["metrics"]
        component_merge_case = (
            result["reasons"] == ["insufficient_text_like_components"]
            and result["score"] >= 0.80
            and metrics["horizontal_span"] >= 0.30
            and metrics["row_peak_excess"] >= 100
        )
        colored_text_case = (
            result["reasons"] == ["colored_text_requires_temporal_evidence"]
            and result["score"] >= 0.50
            and metrics["horizontal_span"] >= 0.20
            and metrics["compact_component_count"] >= 3
        )
        strong_temporal_band_case = (
            temporal is not None
            and float(temporal.get("peak_score", 0.0))
            - float(temporal.get("baseline_score", 0.0))
            >= 20.0
            and metrics["contrast_range"] >= 48
            and metrics["edge_mean"] >= 10
        )
        temporal_corroborated = (
            temporal_passed
            and temporal is not None
            and (
                (
                    0.035 <= metrics["candidate_row_ratio"] <= 0.75
                    and metrics["contrast_range"] >= 90
                    and metrics["edge_mean"] >= 15
                    and (component_merge_case or colored_text_case)
                )
                or strong_temporal_band_case
            )
        )
        if temporal_corroborated:
            result["passed"] = True
            result["reasons"] = []
            result["method"] = "final_strip_contrast_edges_plus_temporal_v1"
        result["temporal_corroborated"] = temporal_corroborated
        result["pixel_presence_passed"] = pixel_presence_passed
        result["temporal_band_passed"] = temporal_passed
        result["passed"] = result["passed"] and temporal_passed
        if not temporal_passed:
            result["reasons"].append("temporal_subtitle_band_not_retained")
        result["temporal_evidence"] = dict(temporal) if temporal is not None else None
        items.append({"index": index, "box": box, **result})
        y += height
    return {
        "required_strip_count": len(strip_heights),
        "passed_strip_count": sum(item["passed"] for item in items),
        "all_pass": all(item["passed"] for item in items),
        "items": items,
        "policy": "all_final_native_strips_must_pass",
    }


def validate_repair_plan(repairs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize and enforce layout/crop -> semantic -> fallback ordering."""
    normalized = []
    previous = -1
    for index, repair in enumerate(repairs):
        if not isinstance(repair, Mapping):
            raise ValueError(f"repair {index} must be a mapping")
        stage = repair.get("stage")
        if stage not in REPAIR_STAGE_ORDER:
            raise ValueError(f"repair {index} has unknown stage: {stage!r}")
        order = REPAIR_STAGE_ORDER.index(stage)
        if order < previous:
            raise ValueError(
                "repair order must be layout_crop, semantic_window, legal_fallback"
            )
        previous = order
        item = dict(repair)
        item.setdefault("name", stage)
        normalized.append(item)
    return normalized


class AuditTrail:
    """Append-only in-memory and optional JSONL audit trail."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else None
        self.events: list[dict[str, Any]] = []
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def emit(self, event: str, **details: Any) -> dict[str, Any]:
        payload = {"sequence": len(self.events) + 1, "event": event, **details}
        self.events.append(payload)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload


def _candidate_id(candidate: Mapping[str, Any]) -> str:
    candidate_id = candidate.get("id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("every candidate must have a non-empty string id")
    return candidate_id


def _audit_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _audit_value(item)
            for key, item in value.items()
            if key not in {"image", "frame", "rendered"}
        }
    if isinstance(value, (list, tuple)):
        return [_audit_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def run_finalization_loop(
    selected: Sequence[Mapping[str, Any]],
    ranked_reserves: Sequence[Mapping[str, Any]],
    *,
    repairs_for: Callable[[Mapping[str, Any]], Iterable[Mapping[str, Any]]],
    render: Callable[[Mapping[str, Any], Mapping[str, Any] | None], Any],
    qa: Callable[[Mapping[str, Any], Any, Mapping[str, Any] | None], Mapping[str, Any]],
    promotion_validate: Callable[
        [Mapping[str, Any], Sequence[Mapping[str, Any]], int], Mapping[str, Any]
    ]
    | None = None,
    audit_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run selector -> render -> QA -> repair -> ranked reserve promotion.

    A reserve is considered only after every ordered repair for the current
    candidate fails and the candidate is recorded as ``UNRENDERABLE``.
    """
    selected_items = [dict(candidate) for candidate in selected]
    reserve_items = [dict(candidate) for candidate in ranked_reserves]
    target_count = len(selected_items)
    seen_ids: set[str] = set()
    for candidate in [*selected_items, *reserve_items]:
        candidate_id = _candidate_id(candidate)
        if candidate_id in seen_ids:
            raise ValueError(f"duplicate candidate id: {candidate_id}")
        seen_ids.add(candidate_id)

    trail = AuditTrail(audit_path)
    trail.emit(
        "selector_output",
        selected_ids=[_candidate_id(item) for item in selected_items],
        ranked_reserve_ids=[_candidate_id(item) for item in reserve_items],
        target_count=target_count,
    )
    accepted: list[dict[str, Any]] = []
    unrenderable: list[dict[str, Any]] = []
    reserve_cursor = 0
    promotion_decisions = []

    for slot, initial in enumerate(selected_items, 1):
        original_candidate_id = _candidate_id(initial)
        candidate = initial
        origin = "selected"
        replacement_context = None
        while candidate is not None:
            candidate_id = _candidate_id(candidate)
            if origin == "reserve":
                trail.emit(
                    "reserve_promoted",
                    slot=slot,
                    candidate_id=candidate_id,
                    reserve_rank=reserve_cursor,
                    original_candidate_id=original_candidate_id,
                    replaced_candidate_id=replacement_context["candidate_id"],
                    failure_reason=replacement_context["failure_reason"],
                    repairs_attempted=replacement_context["repairs_attempted"],
                    promotion_reason=replacement_context["promotion_reason"],
                    promotion_checks=replacement_context["promotion_checks"],
                )
            repairs = validate_repair_plan(repairs_for(candidate))
            attempts: list[tuple[Mapping[str, Any] | None, str]] = [(None, "initial")]
            attempts.extend((repair, repair["stage"]) for repair in repairs)
            accepted_record = None
            last_qa = None
            for attempt_number, (repair, stage) in enumerate(attempts, 1):
                repair_name = repair.get("name") if repair is not None else None
                trail.emit(
                    "render_attempt",
                    slot=slot,
                    candidate_id=candidate_id,
                    origin=origin,
                    attempt=attempt_number,
                    stage=stage,
                    repair=repair_name,
                )
                artifact = render(candidate, repair)
                qa_result = dict(qa(candidate, artifact, repair))
                if not isinstance(qa_result.get("passed"), bool):
                    raise ValueError("qa result must contain boolean passed")
                last_qa = qa_result
                trail.emit(
                    "qa_result",
                    slot=slot,
                    candidate_id=candidate_id,
                    origin=origin,
                    attempt=attempt_number,
                    stage=stage,
                    repair=repair_name,
                    passed=qa_result["passed"],
                    qa=_audit_value(qa_result),
                    artifact=_audit_value(artifact),
                )
                if qa_result["passed"]:
                    accepted_record = {
                        "slot": slot,
                        "candidate": candidate,
                        "candidate_id": candidate_id,
                        "origin": origin,
                        "repair": dict(repair) if repair is not None else None,
                        "artifact": artifact,
                        "qa": qa_result,
                    }
                    accepted.append(accepted_record)
                    trail.emit(
                        "candidate_accepted",
                        slot=slot,
                        candidate_id=candidate_id,
                        origin=origin,
                        stage=stage,
                        repair=repair_name,
                    )
                    break
            if accepted_record is not None:
                break

            exhausted = {
                "slot": slot,
                "candidate": candidate,
                "candidate_id": candidate_id,
                "origin": origin,
                "status": "UNRENDERABLE",
                "last_qa": last_qa,
            }
            unrenderable.append(exhausted)
            trail.emit(
                "repair_exhaustion",
                slot=slot,
                candidate_id=candidate_id,
                origin=origin,
                status="UNRENDERABLE",
                attempts=len(attempts),
            )
            replacement_context = {
                "candidate_id": candidate_id,
                "failure_reason": _audit_value(last_qa),
                "repairs_attempted": [
                    repair.get("name") for repair in repairs
                ],
            }
            candidate = None
            while reserve_cursor < len(reserve_items):
                reserve = reserve_items[reserve_cursor]
                reserve_cursor += 1
                reserve_id = _candidate_id(reserve)
                if promotion_validate is None:
                    validation = {
                        "passed": False,
                        "reason": "promotion_validate callback is required",
                        "checks": {
                            "semantic_alignment": False,
                            "duplicate_cluster": False,
                            "set_diversity": False,
                            "visual_viability": False,
                        },
                    }
                else:
                    raw_validation = dict(
                        promotion_validate(
                            reserve,
                            [item["candidate"] for item in accepted],
                            slot,
                        )
                    )
                    raw_checks = raw_validation.get("checks", {})
                    required = (
                        "semantic_alignment",
                        "duplicate_cluster",
                        "set_diversity",
                        "visual_viability",
                    )
                    checks = {}
                    for name in required:
                        value = raw_checks.get(name)
                        checks[name] = (
                            value.get("passed")
                            if isinstance(value, Mapping)
                            else value
                        )
                    complete = all(isinstance(checks[name], bool) for name in required)
                    passed = complete and all(checks.values())
                    validation = {
                        **raw_validation,
                        "passed": passed,
                        "checks": {name: checks[name] for name in required},
                        "reason": raw_validation.get("reason")
                        or (
                            "all four reserve validation checks passed"
                            if passed
                            else "one or more reserve validation checks failed"
                        ),
                    }
                decision = {
                    "slot": slot,
                    "candidate_id": reserve_id,
                    "reserve_rank": reserve_cursor,
                    "original_candidate_id": original_candidate_id,
                    "replaced_candidate_id": candidate_id,
                    **validation,
                }
                promotion_decisions.append(decision)
                trail.emit("reserve_validation", **_audit_value(decision))
                if validation["passed"]:
                    replacement_context["promotion_reason"] = validation["reason"]
                    replacement_context["promotion_checks"] = validation["checks"]
                    candidate = reserve
                    origin = "reserve"
                    break
                trail.emit(
                    "reserve_rejected",
                    slot=slot,
                    candidate_id=reserve_id,
                    reserve_rank=reserve_cursor,
                    reason=validation["reason"],
                    checks=validation["checks"],
                )
            if candidate is None:
                trail.emit("reserve_exhausted", slot=slot)

    status = "PASS" if len(accepted) == target_count else "EXHAUSTED"
    trail.emit(
        "finalization_complete",
        status=status,
        target_count=target_count,
        accepted_ids=[item["candidate_id"] for item in accepted],
        unrenderable_ids=[item["candidate_id"] for item in unrenderable],
        unused_reserve_ids=[
            _candidate_id(item) for item in reserve_items[reserve_cursor:]
        ],
    )
    return {
        "status": status,
        "target_count": target_count,
        "accepted": accepted,
        "unrenderable": unrenderable,
        "unused_reserves": reserve_items[reserve_cursor:],
        "promotion_decisions": promotion_decisions,
        "audit": trail.events,
        "repair_stage_order": list(REPAIR_STAGE_ORDER),
    }
