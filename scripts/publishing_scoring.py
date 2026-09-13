#!/usr/bin/env python3
"""Independent, evidence-aware scoring for Phase 5A publishing candidates."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


POSITIVE_DIMENSIONS = (
    "clarity",
    "hook_strength",
    "standalone_value",
    "conflict",
    "specificity",
    "audience_relevance",
    "evidence_support",
)
PENALTY_DIMENSIONS = ("cliche", "exaggeration", "diversity")
CANDIDATE_TYPES = ("hook", "title", "copy")
EVIDENCE_STATUSES = ("supported", "inferred", "unsupported")
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "publishing-profiles.json"


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _clamp(value: float, low: float = 0.0, high: float = 5.0) -> float:
    return max(low, min(high, value))


def _validate_profile(profile: Mapping[str, Any]) -> None:
    weights = profile.get("weights", {})
    if set(weights) != set(POSITIVE_DIMENSIONS):
        raise ValueError("publishing profile must define exactly the publishing score dimensions")
    if any(not _number(value) or value < 0 for value in weights.values()):
        raise ValueError("publishing weights must be finite non-negative numbers")
    if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9):
        raise ValueError("publishing weights must sum to 1")
    penalties = profile.get("penalties", {})
    if set(penalties) != set(PENALTY_DIMENSIONS):
        raise ValueError("publishing profile must define cliche, exaggeration, and diversity penalties")
    if any(not _number(value) or value < 0 for value in penalties.values()):
        raise ValueError("publishing penalties must be finite non-negative numbers")
    for candidate_type in CANDIDATE_TYPES:
        limits = profile.get("ideal_length", {}).get(candidate_type)
        if not isinstance(limits, list) or len(limits) != 2 or not all(isinstance(x, int) for x in limits):
            raise ValueError(f"missing ideal length for {candidate_type}")
        if limits[0] < 1 or limits[0] >= limits[1]:
            raise ValueError(f"invalid ideal length for {candidate_type}")


def load_profile(
    platform: str = "douyin",
    account_stage: str = "cold",
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load one publishing profile without importing Phase 4 ranking configuration."""
    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    document = json.loads(path.read_text(encoding="utf-8"))
    try:
        raw_profile = document["platforms"][platform]["account_stages"][account_stage]
    except KeyError as exc:
        raise ValueError(f"unknown publishing profile: {platform}/{account_stage}") from exc
    profile = copy.deepcopy(raw_profile)
    profile.update(
        platform=platform,
        account_stage=account_stage,
        configuration_version=document.get("version"),
        configuration_sha256=_digest(document),
    )
    _validate_profile(profile)
    return profile


def _normalise_text(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text.lower())


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower()))


def text_similarity(left: str, right: str) -> float:
    """Return deterministic lexical similarity, with exact normalized copies at 1."""
    if _normalise_text(left) == _normalise_text(right):
        return 1.0
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    return len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))


def _pattern_hits(text: str, patterns: Iterable[str]) -> list[str]:
    lowered = text.lower()
    return [pattern for pattern in patterns if pattern.lower() in lowered]


def _length_score(text: str, candidate_type: str, profile: Mapping[str, Any]) -> tuple[float, str]:
    visible_length = len(re.sub(r"\s+", "", text))
    minimum, maximum = profile["ideal_length"][candidate_type]
    if minimum <= visible_length <= maximum:
        return 4.5, f"{visible_length} characters is inside the configured {minimum}-{maximum} range"
    distance = minimum - visible_length if visible_length < minimum else visible_length - maximum
    score = _clamp(4.0 - distance / max(3.0, (maximum - minimum) / 3.0))
    return score, f"{visible_length} characters is outside the configured {minimum}-{maximum} range"


def _heuristic_score(
    dimension: str,
    candidate: Mapping[str, Any],
    candidate_type: str,
    profile: Mapping[str, Any],
) -> tuple[float, str]:
    text = candidate["text"]
    if dimension == "clarity":
        length_score, length_reason = _length_score(text, candidate_type, profile)
        clause_penalty = max(0, text.count("，") + text.count(",") - (3 if candidate_type == "copy" else 1)) * 0.35
        return _clamp(length_score - clause_penalty), length_reason
    if dimension == "hook_strength":
        hook_type = candidate.get("hook_type")
        configured = profile.get("hook_type_strength", {}).get(hook_type)
        score = float(configured) if _number(configured) else (3.2 if candidate_type != "copy" else 2.5)
        if "？" in text or "?" in text or "：" in text:
            score += 0.25
        return _clamp(score), f"deterministic {candidate_type} structure and hook type {hook_type or 'unspecified'}"
    if dimension == "standalone_value":
        hits = _pattern_hits(text, profile.get("standalone_context_patterns", []))
        return (_clamp(4.5 - len(hits) * 0.8), "context-dependent phrases: " + ", ".join(hits)) if hits else (4.5, "no configured context-dependent phrase detected")
    if dimension == "conflict":
        hits = _pattern_hits(text, profile.get("conflict_patterns", []))
        return _clamp(2.4 + min(2.4, len(hits) * 0.8)), ("conflict cues: " + ", ".join(hits)) if hits else "no explicit conflict cue detected"
    if dimension == "specificity":
        hits = _pattern_hits(text, profile.get("specificity_patterns", []))
        numeric = re.findall(r"\d+(?:\.\d+)?", text)
        score = _clamp(2.7 + min(2.1, len(hits) * 0.45 + len(numeric) * 0.7))
        details = hits + numeric
        return score, ("specific cues: " + ", ".join(details)) if details else "no explicit configured detail cue detected"
    if dimension == "audience_relevance":
        audience = str(candidate.get("target_audience", "")).strip()
        hits = _pattern_hits(text, profile.get("audience_patterns", []))
        if audience:
            return 4.4, f"candidate declares target audience: {audience}"
        return (_clamp(3.2 + len(hits) * 0.35), "audience cues: " + ", ".join(hits)) if hits else (3.0, "no explicit audience cue; neutral cold-start relevance")
    if dimension == "evidence_support":
        status = candidate.get("evidence_status", "unsupported")
        evidence = candidate.get("supporting_evidence", [])
        if status == "supported" and isinstance(evidence, list) and evidence:
            return 5.0, f"supported by {len(evidence)} evidence reference(s)"
        if status == "inferred":
            return 3.0, "explicitly marked as inferred"
        return 0.0, "unsupported or missing evidence references"
    raise ValueError(f"unknown publishing score dimension: {dimension}")


def _provided_score(candidate: Mapping[str, Any], dimension: str) -> tuple[float, str] | None:
    raw = candidate.get("scores", {}).get(dimension)
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        value, reason = raw.get("score"), raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{candidate['id']}: {dimension} score requires a reason")
    else:
        value, reason = raw, "provided by publishing candidate assessment"
    if not _number(value) or not 0 <= value <= 5:
        raise ValueError(f"{candidate['id']}: invalid {dimension} score")
    return float(value), reason


def _penalty_assessment(candidate: Mapping[str, Any], profile: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    text = candidate["text"]
    flags = candidate.get("flags", {})
    if flags is None:
        flags = {}
    if not isinstance(flags, Mapping):
        raise ValueError(f"{candidate['id']}: flags must be an object")
    cliche_hits = _pattern_hits(text, profile.get("cliche_patterns", []))
    exaggeration_hits = _pattern_hits(text, profile.get("exaggeration_patterns", []))
    cliche_score = 5.0 if flags.get("cliche") is True else min(5.0, len(cliche_hits) * 3.0)
    exaggeration_score = 5.0 if flags.get("exaggerated") is True else min(5.0, len(exaggeration_hits) * 4.0)
    unsupported_claims = candidate.get("unsupported_claims", [])
    if not isinstance(unsupported_claims, list):
        raise ValueError(f"{candidate['id']}: unsupported_claims must be a list")
    evidence_status = candidate.get("evidence_status", "unsupported")
    reasons = []
    if evidence_status not in EVIDENCE_STATUSES:
        raise ValueError(f"{candidate['id']}: invalid evidence_status")
    if evidence_status == "unsupported" or unsupported_claims:
        reasons.append("unsupported substantive claim")
    if exaggeration_score >= profile["thresholds"]["reject_exaggeration_at"]:
        reasons.append("exaggeration exceeds cold-start publishing threshold")
    return {
        "cliche": {
            "score": cliche_score,
            "hits": cliche_hits,
            "reason": "configured cliche pattern or explicit flag" if cliche_score else "no configured cliche pattern detected",
        },
        "exaggeration": {
            "score": exaggeration_score,
            "hits": exaggeration_hits,
            "reason": "configured exaggeration pattern or explicit flag" if exaggeration_score else "no configured exaggeration pattern detected",
        },
    }, reasons


def score_candidate(
    candidate: Mapping[str, Any],
    candidate_type: str,
    profile: Mapping[str, Any] | None = None,
    comparison_texts: Sequence[str] = (),
) -> dict[str, Any]:
    """Score one hook, title, or copy candidate and retain an audit trail."""
    if candidate_type not in CANDIDATE_TYPES:
        raise ValueError(f"unknown publishing candidate type: {candidate_type}")
    if not isinstance(candidate, Mapping):
        raise ValueError("candidate must be an object")
    candidate_id = candidate.get("id")
    text = candidate.get("text")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("candidate id must be a non-empty string")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{candidate_id}: text must be a non-empty string")
    active_profile = copy.deepcopy(dict(profile)) if profile is not None else load_profile()
    _validate_profile(active_profile)
    scores = {}
    for dimension in POSITIVE_DIMENSIONS:
        # Evidence support is derived from provenance fields and cannot be raised by
        # a self-reported publishing assessment.
        provided = None if dimension == "evidence_support" else _provided_score(candidate, dimension)
        if provided is None:
            value, reason = _heuristic_score(dimension, candidate, candidate_type, active_profile)
            source = "heuristic"
        else:
            value, reason = provided
            source = "provided_assessment"
        scores[dimension] = {"score": round(value, 6), "reason": reason, "source": source}
    penalties, rejection_reasons = _penalty_assessment(candidate, active_profile)
    similarities = [text_similarity(text, other) for other in comparison_texts if isinstance(other, str) and other.strip()]
    max_similarity = max(similarities, default=0.0)
    diversity_score = max_similarity if max_similarity >= active_profile["thresholds"]["diversity_similarity"] else 0.0
    penalties["diversity"] = {
        "score": round(diversity_score, 6),
        "max_similarity": round(max_similarity, 6),
        "threshold": active_profile["thresholds"]["diversity_similarity"],
        "reason": "similar to an earlier hook/title/copy" if diversity_score else "no material lexical duplication detected",
    }
    base_score = sum(active_profile["weights"][name] * scores[name]["score"] for name in POSITIVE_DIMENSIONS)
    penalty_total = (
        active_profile["penalties"]["cliche"] * penalties["cliche"]["score"]
        + active_profile["penalties"]["exaggeration"] * penalties["exaggeration"]["score"]
        + active_profile["penalties"]["diversity"] * penalties["diversity"]["score"]
    )
    final_score = _clamp(base_score - penalty_total)
    if final_score < active_profile["thresholds"]["minimum_final_score"]:
        rejection_reasons.append("final score below cold-start publishing threshold")
    return {
        **copy.deepcopy(dict(candidate)),
        "candidate_type": candidate_type,
        "platform": active_profile.get("platform", "douyin"),
        "account_stage": active_profile.get("account_stage", "cold"),
        "scores": scores,
        "penalties": penalties,
        "base_score": round(base_score, 6),
        "penalty_total": round(penalty_total, 6),
        "final_score": round(final_score, 6),
        "eligible": not rejection_reasons,
        "rejected": bool(rejection_reasons),
        "rejection_reasons": list(dict.fromkeys(rejection_reasons)),
        "configuration_sha256": active_profile.get("configuration_sha256"),
    }


def rank_candidates(
    candidates: Sequence[Mapping[str, Any]],
    candidate_type: str,
    profile: Mapping[str, Any] | None = None,
    comparison_texts: Sequence[str] = (),
) -> dict[str, Any]:
    """Rank candidates, penalizing later near-duplicates against stronger candidates."""
    active_profile = copy.deepcopy(dict(profile)) if profile is not None else load_profile()
    _validate_profile(active_profile)
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)) or not candidates:
        raise ValueError("candidates must be a non-empty sequence")
    ids = [candidate.get("id") for candidate in candidates if isinstance(candidate, Mapping)]
    if len(ids) != len(candidates) or len(set(ids)) != len(ids):
        raise ValueError("publishing candidate ids must be present and unique")
    base = [score_candidate(candidate, candidate_type, active_profile) for candidate in candidates]
    base.sort(key=lambda item: (-item["base_score"], item["id"]))
    ranked = []
    prior_texts = list(comparison_texts)
    for item in base:
        rescored = score_candidate(item, candidate_type, active_profile, prior_texts)
        ranked.append(rescored)
        prior_texts.append(item["text"])
    ranked.sort(key=lambda item: (not item["eligible"], -item["final_score"], item["id"]))
    for index, item in enumerate(ranked, 1):
        item["rank"] = index
    selected = next((item for item in ranked if item["eligible"]), None)
    return {
        "platform": active_profile.get("platform", "douyin"),
        "account_stage": active_profile.get("account_stage", "cold"),
        "candidate_type": candidate_type,
        "configuration_version": active_profile.get("configuration_version"),
        "configuration_sha256": active_profile.get("configuration_sha256"),
        "ranked": ranked,
        "selected": selected,
    }


__all__ = [
    "CANDIDATE_TYPES",
    "DEFAULT_CONFIG_PATH",
    "EVIDENCE_STATUSES",
    "PENALTY_DIMENSIONS",
    "POSITIVE_DIMENSIONS",
    "load_profile",
    "rank_candidates",
    "score_candidate",
    "text_similarity",
]
