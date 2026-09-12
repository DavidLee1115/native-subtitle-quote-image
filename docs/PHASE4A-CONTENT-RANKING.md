# Phase 4A: content ranking contract

This layer consumes the complete normalized transcript, creates 20–30 semantic candidates through an agent, reads a Phase 3 visual signal for **every** candidate window, and deterministically selects up to five diverse candidates. It never renders, changes layout or visual QA, or produces publishing copy. It must report insufficient eligibility/diversity instead of padding to five.

## Executable workflow

1. Run `python3 scripts/prepare_ranking_transcript.py --vtt source.vtt --video-id SOURCE_ID --output prepared` to create `transcript.json` and `transcript-readable.txt` from a complete manual subtitle track, with original-file provenance and stable cue ids. Rolling auto captions need a separately audited normalization policy; detected rolling caption markup is rejected. Do not assume an auto-caption track is normalized merely because it parses. Normalize the full source transcript as `{"video_id":"...","cues":[{"id":0,"start":0.0,"end":8.0,"text":"exact source text"}]}`. Preserve provenance separately. Cues must be ordered, nonempty, with unique ids and valid times; overlapping subtitles are allowed.
2. Read **all** cues in order, including the end of the video. If the transcript does not fit in one agent context, partition into contiguous time windows, produce evidence-bound segment notes, then perform a global synthesis with complete coverage. Do not nominate solely from search snippets or opening minutes. Save coverage intervals, total source duration, agent/model provenance, and segment notes in the assessment. Human editorial selection is not required.
3. Run the nomination prompt below using the transcript and selected account profile. Write `assessment.json`; this is a recorded model judgment, not measured audience performance. Freeze the account/weights/configuration before opening benchmark outcomes. Do not tune on benchmark results.
4. Obtain the existing visual layer's read-only signal for each candidate's exact semantic window. Save `visual.json`. Missing measurements become `unknown`, which cannot pass. A quote-first fallback must have explicit source evidence and a measured score; it is not an automatic rescue after final selection.
5. Run `python3 scripts/content_ranking.py --transcript transcript.json --assessment assessment.json --visual visual.json --profile cold --output results`.
6. Inspect `ranking-candidates.json`, `final-selection.json`, and `scoring-table.md`. Retain the full input files alongside outputs. Stop after the benchmark; do not write titles, body copy, hashtags, schedules, or upstream PRs.

## Reusable nomination prompt

> You are the semantic assessment stage of a source-faithful quote ranking pipeline. Read the complete attached transcript, not a sample. Account positioning: cognition, personal growth, and learning-oriented knowledge excerpts for unfamiliar adult readers. Profile: {cold|growth|mature}. Account fit means relevance to that audience; it does not mean agreeing with the speaker. Identify 20–30 promising candidates distributed according to actual merit across the full transcript. Never invent or paraphrase the quote: select one or more contiguous complete cue ids, with quote equal to those exact cue texts joined by a space. Set a broader semantic_window where needed to retain antecedents, caveats, and counterarguments. Output only the assessment schema below.
>
> For each candidate provide topic, semantic cluster, distinct cognitive angle, source speaker attribution, claim_status, and eight dimension scores from 0 to 5 with concise source-specific reasons. Score hook (immediate reason to keep reading), standalone (understandable without missing antecedents), conflict (meaningful tension, not invented controversy), emotion (authentic emotional resonance), novelty (fresh perspective for this audience, not a truth claim), specificity (concrete mechanisms/examples), shareability (reason someone would send/save), account_fit (relevance to positioning). Use 0 for absent, 1 weak, 2 limited, 3 solid, 4 strong, 5 exceptional. Explain high scores with the actual quote/context. Retain qualifications and negations. context_integrity.passed is true only when the extracted words preserve the speaker's intended meaning and attribution. Evidence integrity means faithful quotation/context; it does not establish factual truth. Label claim_status as personal_view, self_report, attributed_report, or requires_verification; preserve speaker in every candidate. Spiritual/metaphysical assertions must remain attributed viewpoints, never be relabeled as established science.
>
> Cluster paraphrases of the same assertion together. Distinguish actual new mechanism, application, tradeoff, counterpoint, or consequence from cosmetic wording. Provide redundancy_pairs across clusters when two candidates still repeat substantially the same information, with similarity 0–1 and reason. Record full_transcript_reviewed and coverage evidence truthfully. All scores and clusters are model judgments, not click-through or retention measurements. Do not optimize against named benchmark videos or reference selections. Do not generate publishing copy.

## Schemas and hashes

`assessment.json`:

```json
{
  "video_id": "source-id",
  "full_transcript_reviewed": true,
  "transcript_sha256": "canonical-json-sha256",
  "coverage": {"reviewed_intervals": [[0, 3600]], "total_duration": 3600},
  "assessment_provenance": {"method": "complete transcript agent judgment", "model": "record actual model"},
  "candidates": [{
    "id": "c01",
    "quote_cue_ids": [10, 11],
    "quote": "Exact cue 10 text Exact cue 11 text",
    "semantic_window": {"start": 70, "end": 110},
    "topic": "A precise topic",
    "cluster": "mechanism-A",
    "angle": "A distinct causal mechanism",
    "speaker": "Source-attributed speaker or unknown",
    "claim_status": "personal_view",
    "context_integrity": {"passed": true, "reason": "Antecedent and caveat included"},
    "scores": {
      "hook": {"score": 4, "reason": "Source-specific judgment"},
      "standalone": {"score": 4, "reason": "Source-specific judgment"},
      "conflict": {"score": 4, "reason": "Source-specific judgment"},
      "emotion": {"score": 3, "reason": "Source-specific judgment"},
      "novelty": {"score": 4, "reason": "Source-specific judgment"},
      "specificity": {"score": 4, "reason": "Source-specific judgment"},
      "shareability": {"score": 4, "reason": "Source-specific judgment"},
      "account_fit": {"score": 4, "reason": "Source-specific judgment"}
    }
  }],
  "redundancy_pairs": [{"candidate_ids": ["c01", "c02"], "similarity": 0.9, "reason": "Same claim despite different labels"}]
}
```

The example shows one candidate for brevity; execution requires 20–30. Quote whitespace is normalized; wording, punctuation, cue order, and continuity remain exact. Partial-cue extracts are deliberately unsupported by this first contract. Semantic integrity remains an explicit agent review; deterministic checking cannot prove human meaning.

`visual.json` is `{ "candidates": [{ "candidate_id": "c01", "semantic_window": {"start":70,"end":110}, "status":"viable", "score":4, "reason":"Observed window suitability", "evidence":["path/to/read-only-signal.json#window"] }] }`. Status is `viable`, `quote_first`, `unusable`, or `unknown`. Read-only signal adapters must describe how their existing metric maps to 0–5. The engine validates the binding and presence, not the pixels or referenced artifact contents; artifact verification is the adapter/operator's responsibility.

All hashes use `sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())`, not file bytes. Output contains source/assessment/visual/configuration hashes. Candidate extension fields are retained. The assessment itself must be archived for full top-level coverage/provenance.

## Frozen scoring and selection

| Dimension | cold | growth | mature |
| --- | ---: | ---: | ---: |
| Hook | .18 | .13 | .08 |
| Standalone | .16 | .13 | .12 |
| Conflict | .15 | .10 | .07 |
| Emotion | .06 | .08 | .08 |
| Novelty | .15 | .10 | .08 |
| Specificity | .07 | .10 | .13 |
| Shareability | .07 | .12 | .12 |
| Account fit | .06 | .14 | .22 |
| Visual viability | .10 | .10 | .10 |
| Evidence integrity | Hard gate | Hard gate | Hard gate |

Weighted score is on 0–5. Evidence integrity is 5/pass or 0/fail with a reason and cannot be offset by any score. All ten dimensions retain structured scores and reasons. Greedy selection subtracts `.65 * max redundancy with selected candidates`; redundancy is the maximum of agent semantic pair similarity and lexical Jaccard overlap. Lexical overlap is a conservative supplemental guard, not semantic understanding. A selected cluster excludes every other candidate in that cluster; similarity at least `.80` also excludes a candidate. Within `.15` adjusted score of the best available candidate, stronger visual viability wins, then viable status, then score/id. Final selection reports pairwise redundancy, clusters, angles, all rejected candidates, and rejected top-ten candidates. Five distinct cluster names alone cannot validate a sound semantic taxonomy; read the recorded angles and pair reasons during acceptance.

The frozen constants and their SHA-256 are emitted with every result. Changes require a new configuration version and a new benchmark; never silently tune on a benchmark's known selection.

## Selection trace

`final-selection.json.selection_rounds` records every considered candidate’s adjusted score, redundancy penalty and visual score/status at each round, the best score, near-quality cutoff and membership, and the chosen candidate. Rejected remaining candidates report their final-round score and winner comparison; cluster/redundancy exclusions retain their specific competing candidate. This trace adds audit evidence only and does not change scores, configuration or selection.
