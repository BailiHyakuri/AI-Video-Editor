# Edit plan schema 1.0

Phase 2 produces a proposed editing plan only. No cuts, subtitles, rendering or
export are executed. The canonical machine-readable shape is
[edit-plan.schema.json](edit-plan.schema.json). Timing/source correspondence,
confidence gating and overlap constraints are checked by core Python validators;
JSON Schema alone cannot establish these relationships.

## Top-level fields

| Field | Meaning |
| --- | --- |
| schema_version | Edit plan version `"1.0"`, independent of transcript version |
| source_transcript_schema_version | Phase 1 `"1.1"`; legacy `"1.0"` is also readable |
| source_transcript | Relative file reference, path_base (`project` or `input_root`), SHA-256 of the exact input bytes |
| project | Original project name, clip_count and full duration |
| dry_run | Always true |
| analysis_mode | `deterministic` or `llm` |
| edit_preset | `conservative`, `normal` (default), or `aggressive` |
| provider | null in no-AI mode; otherwise provider name and configured model |
| policy | Resolved preset/overrides and pause thresholds used for this plan |
| candidates | Detected review candidates, including conservative KEEP-only hints |
| actions | One final decision for each candidate, including KEEP and suppressed decisions |
| summary | Action counts and estimated removed duration after overlap resolution |

There is no generation timestamp: stable input/config/provider decisions produce
stable plans. Candidate IDs and action IDs are scoped to that analysis run.
`source_transcript.file` is relative to the folder containing `edit_plan.json`;
`input_root` means bind the original transcript parent folder explicitly if no
portable project-relative link exists. The analyzer never changes that transcript
or opens/copies source media. Original source path semantics remain governed by
the referenced Phase 1 transcript. Legacy 1.0 absolute paths are not rewritten.

## Candidates

Each candidate has `candidate_id`, `candidate_type`, `ranges`, `original_text`,
limited adjacent `context`, deterministic `evidence`, `allowed_actions` and
`group_id`. Supported types are `pause`, `filler_speech`, `repeated_phrase`,
`false_start`, `self_correction`, `abandoned_sentence`, `cross_clip_repetition`
and `long_hesitation`.

Detection describes structural hints, not guaranteed mistakes. Fillers require
standalone lexical markers; meaningful phrases like “问题就是出在这里” are not
filler matches. Unfinished sentences require explicit ellipsis/dash hints.
Repetition uses adjacent clauses/segments, Unicode normalization and similarity;
small introductory clauses may be skipped for cross-clip review. Near matches,
prefix extensions, quoted correction markers and possible emphasis require
semantic/human review. Distant/nonadjacent sentences are not globally deduplicated.

A phrase inside a segment is DELETE-eligible only when existing complete word or
token alignment supports its exact edges. If unavailable, it receives the full
segment as a **review envelope** and `allowed_actions: ["KEEP"]`. Neither the LLM
nor future code should interpret this envelope as permission to delete surrounding
content. Full-segment candidates can use existing segment bounds.

Semantic groups link overlapping candidates and shared selected speech segments.
Requests pack groups together when possible; oversized groups are split into
bounded batches retaining each candidate's adjacent context.

## Actions and ranges

Each action contains:

```json
{
  "action_id": "edit_009",
  "candidate_id": "candidate_009",
  "action": "SHORTEN_PAUSE",
  "candidate_type": "pause",
  "confidence": 1.0,
  "reason": "Transcript gap exceeds preset threshold; proposed shortening requires human review.",
  "ranges": [
    {
      "clip_id": "clip_002",
      "source_start": 49.0,
      "source_end": 51.435,
      "project_start": 87.618333,
      "project_end": 90.053333,
      "timing_level": "gap"
    }
  ],
  "original_text": "",
  "original_duration": 2.435,
  "target_duration": 0.3,
  "decision_source": "deterministic",
  "suppressed_by": null
}
```

- `KEEP`: preserve the entire candidate, target_duration is null.
- `DELETE`: propose deleting all listed ranges, target_duration is null.
- `SHORTEN_PAUSE`: preserve a configurable positive **total** target_duration
  shorter than the summed original_duration. It applies to the logical pause,
  including one crossing clip boundaries; allocation across clips is deferred
  until a future rendering design. This phase does not invent per-clip allocations.
- `decision_source`: deterministic rule, llm semantic choice, or hybrid when a
  deterministic confidence/overlap gate changes an LLM proposal.
- `confidence`: null for unresolved deterministic hints, 1.0 for deterministic
  verbatim matching/gap rules (certainty of the structural test, **not** semantic
  correctness), otherwise the LLM's uncalibrated self-reported score. These are
  never copied from or invented as ASR confidence.
- `suppressed_by`: winning candidate ID if an overlapping edit becomes KEEP.

Every range contains a known clip_id, source_start/source_end, project_start/
project_end and timing_level (`segment`, `word`, `token`, `gap`). Times are seconds.
Project time = clip project_start + source time. Source endpoints come from
measured transcript unit/segment times or clip boundaries; they are never
interpolated from text positions or supplied by an LLM. Nested word/token timings
remain source-relative in the original transcript. Token timing can be used when
words are null; otherwise only broader segment timing is available.

One logical action can have multiple ordered, nonoverlapping source ranges,
even across clips. There is no fabricated interval joining unrelated clip clocks.
The correction candidate includes abandoned speech and the correction marker;
the following corrected statement is contextual evidence and is preserved.

## Pause policy and presets

Gaps are derived from complete word/token coverage when it exactly aligns with
segment text and has positive measured times; otherwise use full segment coverage.
Speech intervals are unioned before finding gaps, so overlapping ASR spans do not
create spurious pauses. Clip leading/trailing gaps are included. A gap across a
clip boundary is split into source ranges. These are **transcript gaps**, not
proof of acoustic silence; ASR omissions, music or non-speech sounds may remain.

Default categories: <0.35s ignored; [0.35,0.65) short; [0.65,1.2) medium;
[1.2,2.0] long; >2.0 very long. Four increasing thresholds are configurable.
Short/medium preserved gaps are summarized rather than exhaustively printed in
TXT; every candidate/decision remains available in JSON.

| Preset | Shorten gaps at least | Target | Minimum LLM edit confidence | Minimum repetition characters |
| --- | ---: | ---: | ---: | ---: |
| conservative | 2.0s | 0.35s | 0.95 | 12 |
| normal | 1.2s | 0.30s | 0.90 | 8 |
| aggressive | 0.65s | 0.20s | 0.85 | 6 |

Values live in `core/config/edit_presets.json`; target and shortening threshold
may be overridden. The confidence gate applies to all non-KEEP LLM actions. LLM pause shortening
or deletion is also gated by the resolved minimum gap duration.
No-AI mode only proposes pause shortening and directly adjacent **verbatim**
repetition deletion (Unicode NFKC and outer whitespace normalization). Broad
punctuation/case-insensitive matches are candidates, not automatic deletions.
All ambiguous speech candidates remain KEEP. Empty transcripts are preserved.

## Overlap resolution and validation

1. Fully containing DELETE actions take priority over their smaller contained edits.
2. Remaining conflicts use candidate priority: self_correction, false_start,
   abandoned_sentence, cross_clip_repetition, repeated_phrase, filler_speech,
   long_hesitation, pause.
3. Ties prefer longer total ranges, then stable candidate ID.
4. Lower-priority conflicting actions become KEEP with suppressed_by and a reason;
   ranges are not arbitrarily trimmed or merged. KEEP envelopes may overlap.
5. Touching endpoints are allowed; active edits may not overlap, including partial
   overlaps. Summary duration counts accepted edits only.

Validation rejects unknown clips/IDs, invented endpoints, negative/empty/
out-of-bounds intervals, mismatched project timing, nonfinite numbers, illegal
pause targets, disallowed actions, duplicate/missing LLM decisions and unexpected
LLM fields (including timestamps, paths or executable instructions). Confidence
below the preset threshold safely becomes KEEP; malformed output aborts the run
without saving a plan. Reports never execute model output.

## Optional LLM boundary and privacy

`LLMProvider.generate_structured(StructuredRequest) -> dict` is provider-neutral.
Providers return only candidate_id, action, confidence and reason. Prompts live
in `prompts/candidate_review.md`. Requests contain bounded candidate/adjacent text,
structural evidence, durations, mode and allowed actions. They omit full transcript,
source file paths, exact timestamps, media, ASR token arrays and credentials.
If necessary text is truncated, only KEEP is allowed. No tools/functions for
command execution are provided; quoted transcript instructions are untrusted data.

The OpenAI adapter uses the official Python client, Responses strict structured
outputs, explicit timeout/retries and store=False. `store=False` is not a claim
of zero provider retention; consult the provider's policies. The generic compatible
adapter uses Chat Completions and requires strict JSON-schema support; endpoints
without that capability fail clearly rather than pretending to work.
OpenAI uses OPENAI_API_KEY; compatible gateways use LLM_API_KEY. No keys belong
in configuration. SDK errors are summarized without echoing request bodies/keys.
No-AI mode makes no API call and requires neither the optional SDK nor a key.
