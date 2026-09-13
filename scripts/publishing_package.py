#!/usr/bin/env python3
"""Build an evidence-bound Douyin cold-start publishing handoff package.

This module consumes frozen ranking/render/QA outputs.  It never ranks content,
renders media, or changes an artifact.  Only publishing-copy alternatives are
ranked by the independent publishing scorer.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Iterable


HOOK_TYPES = ("contradiction", "curiosity", "pain-point", "identity", "strong-claim")
DAYPARTS = {"morning", "lunch", "afternoon", "evening"}
ABSOLUTE_PATTERNS = ("所有人都", "人人都", "百分之百", "100%", "一定会", "绝对会")
COLD_CONTEXT_PATTERNS = ("老粉都知道", "一直关注我的人", "大家都知道我", "之前跟你们说过")


class PublishingInputError(ValueError):
    """The frozen upstream handoff is incomplete or internally inconsistent."""


def _resolve_path(value: str | Path, base_dir: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    candidates = []
    if base_dir is not None:
        candidates.append(base_dir / path)
    candidates.extend((Path.cwd() / path, Path(__file__).resolve().parents[1] / path))
    return next((candidate.resolve() for candidate in candidates if candidate.exists()), candidates[0].resolve())


def _read_json_reference(data: dict[str, Any], target: str, path_keys: tuple[str, ...], base_dir: Path) -> Any:
    if target in data:
        return data[target]
    raw = _first(data, *path_keys)
    if raw is None:
        return None
    path = _resolve_path(raw, base_dir)
    if not path.is_file():
        raise PublishingInputError(f"referenced input does not exist: {path}")
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PublishingInputError(f"cannot read referenced JSON input: {path}") from exc


def load_input_document(input_path: Path) -> dict[str, Any]:
    """Read a direct contract or assemble it from frozen-output path references."""
    input_path = input_path.resolve()
    data = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PublishingInputError("publishing input must be a JSON object")
    base = input_path.parent
    refs = {}
    refs["source_video_metadata"] = _read_json_reference(
        data, "source_video_metadata", ("source_video_metadata_path", "source_metadata_path"), base)
    refs["source_transcript"] = _read_json_reference(
        data, "source_transcript", ("source_transcript_path", "transcript_path", "subtitle_timeline_path"), base)
    ranked_doc = _read_json_reference(
        data, "ranked_candidate_metadata", ("ranked_candidate_metadata_path", "ranked_candidates_path", "ranking_path"), base)
    refs["ranked_candidate_metadata"] = ranked_doc.get("candidates") if isinstance(ranked_doc, dict) and "candidates" in ranked_doc else ranked_doc
    selection_doc = _read_json_reference(
        data, "final_selected_candidates", ("final_selected_candidates_path", "final_selection_path"), base)
    selected = selection_doc.get("selected") if isinstance(selection_doc, dict) and "selected" in selection_doc else selection_doc
    qa_doc = _read_json_reference(data, "final_qa_results", ("final_qa_results_path", "final_qa_path"), base)
    render_doc = _read_json_reference(data, "render_manifest", ("render_manifest_path",), base)
    if isinstance(selected, list):
        qa_items = {
            str(_first(item, "candidate_id", "title")): item
            for item in (qa_doc.get("items", []) if isinstance(qa_doc, dict) else [])
            if isinstance(item, dict) and _first(item, "candidate_id", "title") is not None
        }
        render_items = {
            str(_first(item, "candidate_id", "title")): item
            for item in (render_doc.get("images", []) if isinstance(render_doc, dict) else [])
            if isinstance(item, dict) and _first(item, "candidate_id", "title") is not None
        }
        artifact_dir = _resolve_path(data.get("artifact_dir", base), base)
        enriched = []
        for raw in selected:
            item = dict(raw)
            cid = _candidate_id(item)
            qa_item, render_item = qa_items.get(cid, {}), render_items.get(cid, {})
            if "timestamp" not in item:
                item["timestamp"] = _first(qa_item, "hero_time", "original_time", default=(render_item.get("times") or [None])[0])
            if "layout_type" not in item:
                item["layout_type"] = _first(qa_item, "layout", default=render_item.get("layout"))
            if "qa_result" not in item:
                item["qa_result"] = _first(qa_item, "result", "status")
            if "artifact_path" not in item and qa_item.get("file"):
                item["artifact_path"] = str(artifact_dir / qa_item["file"])
            if qa_item:
                item["final_qa_evidence"] = qa_item
            if "render_times" not in item:
                item["render_times"] = _first(qa_item, "render_times", default=render_item.get("times"))
            item.setdefault("semantic_topic", item.get("topic"))
            enriched.append(item)
        selected = enriched
    refs["final_selected_candidates"] = selected
    result = dict(data)
    for key, value in refs.items():
        if value is not None:
            result[key] = value
    result["_input_base_dir"] = str(base)
    if isinstance(qa_doc, dict):
        result["final_qa_results"] = qa_doc
    return result


def _load_scoring_module():
    path = Path(__file__).with_name("publishing_scoring.py")
    if not path.exists():
        raise RuntimeError(f"missing independent publishing scorer: {path}")
    spec = importlib.util.spec_from_file_location("publishing_scoring", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _norm(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value).lower())


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _clean_body(value: Any) -> str:
    paragraphs = [" ".join(part.split()) for part in re.split(r"\n\s*\n", str(value or ""))]
    return "\n\n".join(part for part in paragraphs if part)


def _first(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _candidate_id(candidate: dict[str, Any]) -> str:
    value = _first(candidate, "candidate_id", "id")
    if value is None or not _clean(value):
        raise PublishingInputError("every final candidate needs candidate_id/id")
    return str(value)


def _qa_passed(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.upper() == "PASS"
    if isinstance(value, dict):
        status = _first(value, "status", "result", "overall_status")
        return status is not None and str(status).upper() == "PASS"
    return False


def _timestamp(candidate: dict[str, Any]) -> Any:
    value = _first(candidate, "timestamp", "time", "start")
    if value is None:
        window = candidate.get("semantic_window")
        if isinstance(window, dict):
            value = window.get("start")
    if value is None:
        raise PublishingInputError(f"{_candidate_id(candidate)}: missing timestamp")
    return value


def _seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            parts = [float(part) for part in value.split(":")]
        except ValueError:
            return None
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


def _transcript_cues(data: dict[str, Any]) -> list[dict[str, Any]]:
    transcript = _first(data, "source_transcript", "transcript", "subtitle_timeline")
    if isinstance(transcript, dict):
        cues = _first(transcript, "cues", "segments", "subtitles")
    else:
        cues = transcript
    if not isinstance(cues, list) or not cues:
        raise PublishingInputError("source transcript/subtitle timeline must contain cues")
    result = []
    for index, cue in enumerate(cues):
        if not isinstance(cue, dict) or not _clean(cue.get("text")):
            raise PublishingInputError("every transcript cue needs non-empty text")
        result.append({
            "id": str(cue.get("id", index)),
            "start": _first(cue, "start", "timestamp", "time"),
            "end": cue.get("end"),
            "text": _clean(cue["text"]),
        })
    return result


def _quote_evidence(candidate: dict[str, Any], cues: list[dict[str, Any]]) -> dict[str, Any]:
    cid = _candidate_id(candidate)
    quote = _clean(candidate.get("quote"))
    if not quote:
        raise PublishingInputError(f"{cid}: missing quote")
    cue_by_id = {cue["id"]: cue for cue in cues}
    cue_ids = [str(value) for value in candidate.get("quote_cue_ids", [])]
    matches = [cue_by_id[value] for value in cue_ids if value in cue_by_id]
    if cue_ids and len(matches) != len(cue_ids):
        raise PublishingInputError(f"{cid}: quote_cue_ids do not exist in transcript")

    if not matches:
        stamp = _seconds(_timestamp(candidate))
        if stamp is not None:
            for cue in cues:
                start, end = _seconds(cue["start"]), _seconds(cue["end"])
                if start is not None and end is not None and start - 0.75 <= stamp <= end + 0.75:
                    matches.append(cue)
        if not matches:
            matches = [cue for cue in cues if _norm(quote) in _norm(cue["text"]) or _norm(cue["text"]) in _norm(quote)]

    transcript_quote = " ".join(cue["text"] for cue in matches)
    if not transcript_quote or not (_norm(quote) in _norm(transcript_quote) or _norm(transcript_quote) in _norm(quote)):
        # A ranked quote can span cues while its selected frame is inside one cue.
        full_text = " ".join(cue["text"] for cue in cues)
        if _norm(quote) not in _norm(full_text):
            raise PublishingInputError(f"{cid}: final quote is not supported by source transcript")
        transcript_quote = quote
    quote_timestamp: Any = _timestamp(candidate)
    if matches:
        quote_timestamp = {"start": matches[0]["start"], "end": matches[-1]["end"]}
    return {
        "candidate_id": cid,
        "timestamp": quote_timestamp,
        "transcript_quote": transcript_quote,
        "quote": quote,
        "cue_ids": [cue["id"] for cue in matches],
        "speaker": candidate.get("speaker"),
        "source_claim_status": candidate.get("claim_status"),
    }


def validate_input(data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Validate the publishing handoff without mutating any frozen-engine output."""
    if data.get("platform") != "douyin":
        raise PublishingInputError("Phase 5A requires platform=douyin")
    if _first(data, "account_stage", "stage") != "cold":
        raise PublishingInputError("Phase 5A requires account_stage=cold")
    source = _first(data, "source_video_metadata", "source_video", "source_metadata")
    if not isinstance(source, dict) or not source:
        raise PublishingInputError("source video metadata is required")
    ranked = _first(data, "ranked_candidate_metadata", "ranked_candidates")
    final = _first(data, "final_selected_candidates", "final_candidates", "selected_candidates")
    if not isinstance(ranked, list) or not ranked:
        raise PublishingInputError("ranked candidate metadata is required")
    if not isinstance(final, list) or len(final) != 5:
        raise PublishingInputError("exactly 5 final selected candidates are required")
    ranked_ids = {_candidate_id(candidate) for candidate in ranked}
    cues = _transcript_cues(data)
    final_qa = data.get("final_qa_results")
    if isinstance(final_qa, dict) and not _qa_passed(_first(final_qa, "overall", "status", "result")):
        raise PublishingInputError("referenced aggregate final QA must be PASS")
    base_dir = Path(data["_input_base_dir"]) if data.get("_input_base_dir") else None
    expected_artifact_hashes = data.get("input_provenance", {}).get("artifact_sha256", {})
    if expected_artifact_hashes and not isinstance(expected_artifact_hashes, dict):
        raise PublishingInputError("input_provenance.artifact_sha256 must be an object")
    evidence: dict[str, dict[str, Any]] = {}
    canonical = []
    for candidate in final:
        cid = _candidate_id(candidate)
        if cid in evidence:
            raise PublishingInputError("final candidate IDs must be unique")
        if cid not in ranked_ids:
            raise PublishingInputError(f"{cid}: final candidate is absent from ranked metadata")
        required = {
            "semantic topic": _first(candidate, "semantic_topic", "topic"),
            "artifact path": _first(candidate, "artifact_path", "path"),
            "layout type": _first(candidate, "layout_type", "layout"),
        }
        for label, value in required.items():
            if value is None or not _clean(value):
                raise PublishingInputError(f"{cid}: missing {label}")
        qa = _first(candidate, "qa_result", "final_qa", "qa")
        if not _qa_passed(qa):
            raise PublishingInputError(f"{cid}: final artifact QA must be PASS")
        artifact_path = _resolve_path(str(required["artifact path"]), base_dir)
        if not artifact_path.is_file():
            raise PublishingInputError(f"{cid}: final artifact does not exist: {artifact_path}")
        artifact_bytes = artifact_path.read_bytes()
        if not artifact_bytes:
            raise PublishingInputError(f"{cid}: final artifact is empty")
        artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
        expected_sha256 = expected_artifact_hashes.get(cid)
        if expected_sha256 and artifact_sha256 != expected_sha256:
            raise PublishingInputError(f"{cid}: final artifact SHA-256 does not match frozen provenance")
        evidence[cid] = _quote_evidence(candidate, cues)
        canonical.append({
            "candidate_id": cid,
            "quote": evidence[cid]["quote"],
            "timestamp": evidence[cid]["timestamp"],
            "artifact_hero_time": _timestamp(candidate),
            "render_times": candidate.get("render_times"),
            "semantic_window": candidate.get("semantic_window"),
            "semantic_topic": _clean(required["semantic topic"]),
            "speaker": candidate.get("speaker"),
            "source_claim_status": candidate.get("claim_status"),
            "artifact_path": str(required["artifact path"]),
            "artifact_verified_path": str(artifact_path),
            "artifact_size_bytes": len(artifact_bytes),
            "artifact_sha256": artifact_sha256,
            "layout_type": _clean(required["layout type"]),
            "qa_result": "PASS",
            "final_qa_evidence": candidate.get("final_qa_evidence"),
        })
    return canonical, evidence


def _short(text: str, length: int = 22) -> str:
    text = _clean(text).strip("“”\"")
    return text if len(text) <= length else text[: length - 1].rstrip("，，, ") + "…"


def _ids(value: Any, fallback: Iterable[str] = ()) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return list(fallback)


def _candidate_evidence(ids: Iterable[str], evidence: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [evidence[cid] for cid in ids if cid in evidence]


def _claim(
    claim_id: str,
    section: str,
    text: str,
    candidate_ids: Iterable[str],
    evidence: dict[str, dict[str, Any]],
    declared_status: str | None = None,
) -> dict[str, Any]:
    ids = list(dict.fromkeys(str(cid) for cid in candidate_ids))
    valid = _candidate_evidence(ids, evidence)
    if declared_status == "unsupported":
        status = "unsupported"
    elif declared_status == "inferred":
        status = "inferred"
    else:
        status = "supported" if ids and len(valid) == len(ids) else "unsupported"
    return {"claim_id": claim_id, "section": section, "claim": _clean(text), "status": status, "evidence": valid}


def _value_and_ids(value: Any, fallback_text: str, fallback_ids: list[str]) -> tuple[str, list[str], str | None]:
    if isinstance(value, dict):
        return (
            _clean(_first(value, "text", "value", default=fallback_text)),
            _ids(_first(value, "supporting_candidate_ids", "candidate_ids"), fallback_ids),
            value.get("status"),
        )
    return _clean(value) if value else fallback_text, fallback_ids, None


def _positioning(data: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    supplied = data.get("account_positioning")
    if supplied:
        if isinstance(supplied, dict):
            value = _clean(_first(supplied, "value", "text"))
        else:
            value = _clean(supplied)
        if not value:
            raise PublishingInputError("account_positioning cannot be empty")
        return {"value": value, "status": "confirmed", "source": "user_input"}
    topics = list(dict.fromkeys(candidate["semantic_topic"] for candidate in candidates))
    proposed = data.get("inferred_positioning")
    if proposed:
        value = _clean(_first(proposed, "value", "text")) if isinstance(proposed, dict) else _clean(proposed)
        if not value:
            raise PublishingInputError("inferred_positioning cannot be empty")
        return {
            "value": value,
            "status": "inferred",
            "source": "publishing_layer_from_current_material",
            "basis": topics[:3],
        }
    return {
        "value": f"用原视频关键片段拆解{topics[0]}等话题",
        "status": "inferred",
        "source": "current_material_only",
        "basis": topics[:3],
    }


def _public_source_metadata(data: dict[str, Any]) -> dict[str, Any]:
    source = _first(data, "source_video_metadata", "source_video", "source_metadata")
    fields = ("id", "video_id", "title", "uploader", "channel", "duration", "upload_date", "webpage_url", "availability")
    return {field: source[field] for field in fields if field in source and source[field] is not None}


def _default_hooks(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    a, b, c, d, e = candidates
    return [
        {"id": "hook-contradiction", "text": f"别急着下结论：这 5 句话正在讲{_short(a['semantic_topic'], 10)}", "hook_type": "contradiction", "supporting_candidate_ids": [a["candidate_id"]]},
        {"id": "hook-curiosity", "text": f"“{_short(b['quote'], 23)}？”", "hook_type": "curiosity", "supporting_candidate_ids": [b["candidate_id"]]},
        {"id": "hook-pain", "text": f"被{_short(c['semantic_topic'], 10)}困住时，先看这 5 句话", "hook_type": "pain-point", "supporting_candidate_ids": [c["candidate_id"]]},
        {"id": "hook-identity", "text": f"关心{_short(d['semantic_topic'], 10)}的人，值得看完这组图", "hook_type": "identity", "supporting_candidate_ids": [d["candidate_id"]]},
        {"id": "hook-claim", "text": _short(e["quote"], 30), "hook_type": "strong-claim", "supporting_candidate_ids": [e["candidate_id"]]},
    ]


def _default_titles(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    topics = [candidate["semantic_topic"] for candidate in candidates]
    ids = [candidate["candidate_id"] for candidate in candidates]
    return [
        {"id": "title-1", "text": f"从{_short(topics[0], 10)}到{_short(topics[1], 10)}：5 个关键片段", "supporting_candidate_ids": ids[:2], "rationale": "具体点出两个议题，让陌生用户知道会看到什么。"},
        {"id": "title-2", "text": f"谈{_short(topics[0], 12)}，别错过这 5 句话", "supporting_candidate_ids": ids, "rationale": "用主题加有限数量建立明确阅读预期。"},
        {"id": "title-3", "text": f"{_short(topics[2], 12)}背后，还有这 4 层意思", "supporting_candidate_ids": ids, "rationale": "以中段议题为入口，引出五图的递进关系。"},
        {"id": "title-4", "text": f"如果你也在想{_short(topics[3], 12)}，这 5 张图可以连起来看", "supporting_candidate_ids": ids[1:], "rationale": "从用户正在思考的问题切入，不预设结论。"},
        {"id": "title-5", "text": f"5 个片段，看懂{_short(topics[4], 14)}的完整上下文", "supporting_candidate_ids": ids, "rationale": "强调上下文和有限范围，避免把视频说成一句空泛结论。"},
    ]


def _prepare_copy_candidate(raw: dict[str, Any], index: int, kind: str, evidence: dict[str, dict[str, Any]], fallback_ids: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    item = dict(raw)
    item["id"] = str(item.get("id") or f"{kind}-{index + 1}")
    item["text"] = _clean(item.get("text"))
    ids = _ids(_first(item, "supporting_candidate_ids", "supporting_candidate_id", "candidate_ids"), fallback_ids)
    declared = item.get("evidence_status") or item.get("status")
    claim = _claim(f"{kind}-{item['id']}", kind, item["text"], ids, evidence, declared)
    item["supporting_candidate_ids"] = ids
    item["supporting_evidence"] = claim["evidence"]
    item["evidence_status"] = claim["status"]
    if claim["status"] == "unsupported":
        item["unsupported_claims"] = list(item.get("unsupported_claims", [])) + [item["text"]]
    return item, claim


def _body(draft: dict[str, Any], candidates: list[dict[str, Any]], evidence: dict[str, dict[str, Any]]) -> tuple[str, list[dict[str, Any]], list[str]]:
    body = _clean_body(draft.get("body_copy"))
    raw_claims = draft.get("body_claims")
    ids = [candidate["candidate_id"] for candidate in candidates]
    topics = [candidate["semantic_topic"] for candidate in candidates]
    if not body:
        body = (
            f"这组图想快速建立一个问题：{topics[0]}与{topics[1]}之间，到底如何连在一起？\n\n"
            f"可以沿着三个线索读：先看{topics[0]}，再看{topics[2]}，最后回到{topics[4]}。"
            "五张图各自保留原话和时间点，组合在一起才能看出这条逻辑。\n\n"
            "你更关心哪一个线索？"
        )
        raw_claims = [
            {"text": f"这组图讨论{topics[0]}与{topics[1]}的关系", "supporting_candidate_ids": ids[:2]},
            {"text": f"其中包含{topics[2]}这一线索", "supporting_candidate_ids": [ids[2]]},
            {"text": f"最后一张图涉及{topics[4]}", "supporting_candidate_ids": [ids[4]]},
        ]
    if not isinstance(raw_claims, list) or not raw_claims:
        fallback = _ids(draft.get("body_supporting_candidate_ids"))
        raw_claims = [{"text": body, "supporting_candidate_ids": fallback}]
    claims = []
    viewpoints = []
    for index, raw in enumerate(raw_claims):
        if not isinstance(raw, dict) or not _clean(_first(raw, "text", "claim")):
            raise PublishingInputError("body_claims entries need text/claim")
        text = _clean(_first(raw, "text", "claim"))
        viewpoints.append(text)
        claims.append(_claim(
            f"body-{index + 1}", "body_copy", text,
            _ids(_first(raw, "supporting_candidate_ids", "candidate_ids")), evidence,
            raw.get("status") or raw.get("evidence_status"),
        ))
    return body.replace("\\n", "\n"), claims, viewpoints


def _tags(draft: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, list[str]]:
    supplied = draft.get("hashtags")
    if isinstance(supplied, dict):
        groups = {name: supplied.get(name, []) for name in ("core_tags", "niche_tags", "optional_discovery_tags")}
    else:
        topics = [re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]", "", candidate["semantic_topic"]) for candidate in candidates]
        groups = {
            "core_tags": topics[:2],
            "niche_tags": topics[2:4],
            "optional_discovery_tags": [topics[4]],
        }
    result = {}
    for name, values in groups.items():
        if not isinstance(values, list):
            raise PublishingInputError(f"hashtags.{name} must be a list")
        result[name] = ["#" + _clean(value).lstrip("#") for value in values if _clean(value)]
    if not result["core_tags"]:
        raise PublishingInputError("at least one core hashtag is required")
    return result


def _posting(draft: dict[str, Any]) -> dict[str, Any]:
    raw = draft.get("posting_recommendation", {})
    daypart = raw.get("recommended_daypart", "evening")
    if daypart not in DAYPARTS:
        raise PublishingInputError("recommended_daypart must be a broad daypart, not a precise time")
    confidence = raw.get("confidence", 0.35)
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        raise PublishingInputError("posting confidence must be between 0 and 1")
    reason = _clean(raw.get("reason") or "无真实账号历史数据；晚间仅作为冷启动人工试发时段，后续应以账号数据校正。")
    if re.search(r"\b\d{1,2}:\d{2}\b", reason):
        raise PublishingInputError("posting recommendation cannot claim minute-level precision")
    return {"recommended_daypart": daypart, "reason": reason, "confidence": confidence, "basis": "heuristic_without_account_history"}


def _qa(package: dict[str, Any], claims: list[dict[str, Any]], candidates: list[dict[str, Any]], scorer: Any, profile: dict[str, Any]) -> dict[str, Any]:
    hook = package.get("selected_hook") or {}
    title = package.get("selected_title") or {}
    body = package.get("body_copy", "")
    unsupported = [claim["claim_id"] for claim in claims if claim["status"] == "unsupported"]
    evidence_ok = bool(claims) and not unsupported
    hook_ok = bool(hook) and hook.get("evidence_status") == "supported" and hook.get("eligible", True) and len(hook.get("text", "")) <= 40 and not hook.get("flags", {}).get("exaggerated", False)
    title_ok = bool(title) and title.get("evidence_status") == "supported" and title.get("eligible", True) and not title.get("flags", {}).get("exaggerated", False)
    body_claims = [claim for claim in claims if claim["section"] == "body_copy"]
    body_ok = bool(body.strip()) and 2 <= len(body_claims) <= 4 and all(pattern not in body for pattern in ABSOLUTE_PATTERNS)
    verbatim_quotes = [candidate["quote"] for candidate in candidates if len(candidate["quote"]) >= 8 and candidate["quote"] in body]
    threshold = profile["thresholds"]["diversity_similarity"]
    similarities = {
        "hook_title": scorer.text_similarity(hook.get("text", ""), title.get("text", "")),
        "hook_body": scorer.text_similarity(hook.get("text", ""), body),
        "title_body": scorer.text_similarity(title.get("text", ""), body),
    }
    redundant = max(similarities.values()) >= threshold or bool(verbatim_quotes)
    cold_ok = not any(pattern in " ".join((hook.get("text", ""), title.get("text", ""), body)) for pattern in COLD_CONTEXT_PATTERNS)
    checks = {
        "evidence_qa": {"status": "PASS" if evidence_ok else "FAIL", "unsupported_claim_ids": unsupported},
        "hook_qa": {"status": "PASS" if hook_ok else "FAIL", "reason": "selected hook is standalone, supported, concise, and not exaggerated" if hook_ok else "selected hook failed support, eligibility, length, or exaggeration checks"},
        "title_qa": {"status": "PASS" if title_ok else "FAIL", "reason": "selected title is supported and eligible" if title_ok else "selected title failed support or eligibility checks"},
        "body_qa": {"status": "PASS" if body_ok else "FAIL", "reason": "body has 2-4 sourced viewpoints and no absolute claim" if body_ok else "body needs 2-4 sourced viewpoints without absolute claims"},
        "redundancy_qa": {"status": "FAIL" if redundant else "PASS", "similarities": similarities, "threshold": threshold, "verbatim_image_quotes_in_body": verbatim_quotes},
        "cold_start_qa": {"status": "PASS" if cold_ok else "FAIL", "reason": "copy does not depend on existing follower context" if cold_ok else "copy depends on prior follower context"},
    }
    checks["status"] = "PASS" if all(value.get("status") == "PASS" for key, value in checks.items() if key != "status") else "FAIL"
    return checks


def generate_package(data: dict[str, Any], config_path: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(publishing package, claim evidence map)`` deterministically."""
    candidates, evidence = validate_input(data)
    scorer = _load_scoring_module()
    profile = scorer.load_profile(platform="douyin", account_stage="cold", config_path=config_path)
    draft = data.get("editorial_draft") or {}
    if not isinstance(draft, dict):
        raise PublishingInputError("editorial_draft must be an object")
    all_ids = [candidate["candidate_id"] for candidate in candidates]
    claims: list[dict[str, Any]] = []

    angle_raw = draft.get("content_angle") if isinstance(draft.get("content_angle"), dict) else {}
    topics = [candidate["semantic_topic"] for candidate in candidates]
    angle_defaults = {
        "primary_angle": f"把 5 个片段包装成一条从{topics[0]}走向{topics[-1]}的逻辑线",
        "audience_promise": f"让陌生用户用 5 张图理解{topics[0]}、{topics[2]}与{topics[-1]}的关系",
        "core_conflict": f"{topics[0]}与{topics[-1]}之间的张力",
        "why_this_angle": "五张图来自不同时间点但可沿同一问题阅读；这个角度提供可跟随的逻辑，而不是把原视频缩写成简单概括。",
    }
    content_angle = {}
    for index, field in enumerate(("primary_angle", "audience_promise", "core_conflict", "why_this_angle")):
        raw = angle_raw.get(field, draft.get(field))
        text, ids, declared = _value_and_ids(raw, angle_defaults[field], all_ids)
        content_angle[field] = text
        claims.append(_claim(f"angle-{index + 1}", "content_angle", text, ids, evidence, declared))

    raw_hooks = draft.get("hook_candidates") or _default_hooks(candidates)
    if not isinstance(raw_hooks, list) or len(raw_hooks) < 5:
        raise PublishingInputError("at least 5 hook candidates are required")
    hooks, hook_claims = [], []
    for index, raw in enumerate(raw_hooks):
        if not isinstance(raw, dict):
            raise PublishingInputError("hook candidates must be objects")
        item, claim = _prepare_copy_candidate(raw, index, "hook", evidence, [all_ids[index % 5]])
        if item.get("hook_type") not in HOOK_TYPES:
            raise PublishingInputError(f"unknown hook_type: {item.get('hook_type')}")
        hooks.append(item); hook_claims.append(claim)
    missing_types = set(HOOK_TYPES) - {hook["hook_type"] for hook in hooks}
    if missing_types:
        raise PublishingInputError(f"hook candidates missing types: {sorted(missing_types)}")
    hook_ranking = scorer.rank_candidates(hooks, candidate_type="hook", profile=profile)
    for item in hook_ranking.get("ranked", []):
        item["confidence"] = round(item.get("final_score", 0) / 5, 4)
        item["confidence_basis"] = "publishing final_score normalized to 0-1; model/rule judgment, not audience-performance data"
    selected_hook = hook_ranking.get("selected")
    if isinstance(selected_hook, list):
        selected_hook = selected_hook[0] if selected_hook else None
    claims.extend(hook_claims)

    raw_titles = draft.get("title_candidates") or _default_titles(candidates)
    if not isinstance(raw_titles, list) or len(raw_titles) < 5:
        raise PublishingInputError("at least 5 title candidates are required")
    titles, title_claims = [], []
    for index, raw in enumerate(raw_titles):
        if not isinstance(raw, dict):
            raise PublishingInputError("title candidates must be objects")
        item, claim = _prepare_copy_candidate(raw, index, "title", evidence, all_ids)
        item["rationale"] = _clean(item.get("rationale") or "以可追溯的五图主题建立独立可理解的标题。")
        titles.append(item); title_claims.append(claim)
    title_ranking = scorer.rank_candidates(titles, candidate_type="title", profile=profile, comparison_texts=[selected_hook["text"]] if selected_hook else [])
    for item in title_ranking.get("ranked", []):
        item["confidence"] = round(item.get("final_score", 0) / 5, 4)
        item["confidence_basis"] = "publishing final_score normalized to 0-1; model/rule judgment, not audience-performance data"
    selected_title = title_ranking.get("selected")
    if isinstance(selected_title, list):
        selected_title = selected_title[0] if selected_title else None
    claims.extend(title_claims)

    body, body_claims, viewpoints = _body(draft, candidates, evidence)
    claims.extend(body_claims)
    positioning = _positioning(data, candidates)
    positioning_claim = _claim("positioning-1", "account_positioning", positioning["value"], all_ids, evidence, positioning["status"])
    claims.append(positioning_claim)
    package = {
        "schema_version": "phase5a-publishing-package-v1",
        "phase": "5A",
        "platform": "douyin",
        "account_stage": "cold",
        "status": "PENDING_QA",
        "frozen_baseline": data.get("frozen_baseline"),
        "input_provenance": data.get("input_provenance"),
        "source_video_metadata": _public_source_metadata(data),
        "account_positioning": positioning,
        "target_audience": data.get("target_audience"),
        "tone": data.get("tone"),
        "topic_direction": data.get("topic_direction"),
        "frozen_final_candidates": candidates,
        "content_angle": content_angle,
        "hook_candidates": hook_ranking.get("ranked", []),
        "selected_hook": selected_hook,
        "title_candidates": title_ranking.get("ranked", []),
        "selected_title": selected_title,
        "body_copy": body,
        "body_core_viewpoints": viewpoints,
        "hashtags": _tags(draft, candidates),
        "posting_recommendation": _posting(draft),
        "publishing_scoring": {
            "profile": {"platform": profile.get("platform"), "account_stage": profile.get("account_stage"), "diversity_similarity_threshold": profile["thresholds"]["diversity_similarity"]},
            "configuration_sha256": hook_ranking.get("configuration_sha256"),
            "hook_ranking": hook_ranking,
            "title_ranking": title_ranking,
        },
    }
    package["publishing_qa"] = _qa(package, claims, candidates, scorer, profile)
    package["status"] = package["publishing_qa"]["status"]
    claim_map = {
        "schema_version": "phase5a-claim-evidence-map-v1",
        "phase": "5A",
        "platform": "douyin",
        "account_stage": "cold",
        "frozen_baseline": data.get("frozen_baseline"),
        "source_video_id": _first(_public_source_metadata(data), "id", "video_id"),
        "status": "PASS" if not any(claim["status"] == "unsupported" for claim in claims) else "FAIL",
        "claims": claims,
        "unsupported_claim_ids": [claim["claim_id"] for claim in claims if claim["status"] == "unsupported"],
    }
    if package["status"] == "FAIL":
        # A failed audit record deliberately carries no publishable copy.
        failed = {
            "phase": "5A", "platform": "douyin", "account_stage": "cold", "status": "FAIL",
            "publishable": False, "publishing_qa": package["publishing_qa"],
            "errors": ["publishing QA failed; no publishable draft emitted"],
        }
        return failed, claim_map
    package["publishable"] = True
    return package, claim_map


def package_markdown(package: dict[str, Any]) -> str:
    if package.get("status") != "PASS":
        return "# Phase 5A Douyin Cold-start Publishing Package\n\n**FAIL** — publishing QA failed; no publishable draft was emitted.\n"
    angle = package["content_angle"]
    tags = package["hashtags"]
    lines = [
        "# Phase 5A Douyin Cold-start Publishing Package", "",
        f"**{package['status']}** | platform: `douyin` | account stage: `cold`", "",
        "## Account positioning", "",
        f"{package['account_positioning']['value']}  ",
        f"Status: `{package['account_positioning']['status']}`", "",
        "## Content angle", "",
        f"- Primary angle: {angle['primary_angle']}",
        f"- Audience promise: {angle['audience_promise']}",
        f"- Core conflict: {angle['core_conflict']}",
        f"- Why this angle: {angle['why_this_angle']}", "",
        "## Selected cover hook", "", f"**{package['selected_hook']['text']}**  ",
        f"Type: `{package['selected_hook']['hook_type']}` | confidence: `{package['selected_hook']['confidence']}`", "",
        "Confidence is the normalized publishing score, not a traffic or conversion forecast.", "",
        "## Hook candidate ranking", "",
    ]
    for hook in package["hook_candidates"]:
        support = ", ".join(hook.get("supporting_candidate_ids", []))
        lines.append(f"{hook.get('rank', '-')}. [{hook.get('hook_type')}] {hook['text']} — score {hook.get('final_score', 'n/a')}; confidence {hook.get('confidence', 'n/a')}; evidence {support}")
    lines += ["", "## Selected Douyin title", "", f"**{package['selected_title']['text']}**  ", f"{package['selected_title'].get('rationale', '')}", "", "## Title candidate ranking", ""]
    for title in package["title_candidates"]:
        support = ", ".join(title.get("supporting_candidate_ids", []))
        lines.append(f"{title.get('rank', '-')}. {title['text']} — score {title.get('final_score', 'n/a')}; evidence {support}; {title.get('rationale', '')}")
    lines += ["", "## Body copy", "", package["body_copy"], "", "## Hashtags", ""]
    for name in ("core_tags", "niche_tags", "optional_discovery_tags"):
        lines.append(f"- {name}: {' '.join(tags[name])}")
    posting = package["posting_recommendation"]
    lines += ["", "## Posting recommendation", "", f"- Daypart: `{posting['recommended_daypart']}`", f"- Reason: {posting['reason']}", f"- Confidence: {posting['confidence']}", "", "## Publishing QA", "", f"**{package['publishing_qa']['status']}**", ""]
    for name, result in package["publishing_qa"].items():
        if name != "status":
            lines.append(f"- {name}: `{result['status']}`")
    return "\n".join(lines) + "\n"


def write_package(data: dict[str, Any], output_dir: Path, config_path: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    package, claim_map = generate_package(data, config_path=config_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "publishing-package.json").write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "publishing-package.md").write_text(package_markdown(package), encoding="utf-8")
    (output_dir / "claim-evidence-map.json").write_text(json.dumps(claim_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "publishing-qa-result.json").write_text(json.dumps(package["publishing_qa"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return package, claim_map


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", "--output", dest="output_dir", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    package, claim_map = write_package(load_input_document(args.input), args.output_dir, args.config)
    print(json.dumps({"status": package["status"], "unsupported_claim_ids": claim_map["unsupported_claim_ids"]}, ensure_ascii=False))
    return 0 if package["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
