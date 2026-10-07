# Transcript schema 1.1

All timestamps are finite seconds; ranges are nonnegative and end >= start.
Unknown optional fields are JSON null; no probabilities are fabricated.
Schema changes that alter field meaning require a new schema_version.

## Per-clip transcript.json

- `schema_version`: `"1.1"`.
- `clip_id`: permanent identifier within the saved project (`clip_001`, etc.).
- `order`: one-based project order, independent of filenames.
- `source.file`: portable relative source path, with forward-slash separators.
- `source.path_base`: `"project"` or `"input_root"`; see path semantics below.
- `source.original_filename`: original basename.
- `source.duration`: full ffprobe container duration, including silence.
- `source.metadata`: duration, container format and original ffprobe audio/video
  stream objects (codec_name, sample_rate, avg_frame_rate, indexes, etc. when supplied).
- `language`: detected language code or null; the requested language is not invented
  as a detected result when the provider does not report it.
- `text`: normalized full text, segments joined with newlines.
- `segments`: ordered list with sequential zero-based `id`, source-relative
  `start`, `end`, `text`, nullable `confidence`, nullable `words`, nullable `tokens`.
- Each word/token has `text`, nullable paired `start`/`end` and nullable `confidence`.
  A provider may return true words; whisper.cpp tokens remain separate.
  Missing/negative upstream token offsets become null. Special engine tokens
  beginning `[_` are excluded. Token `p` is retained as token confidence only;
  it is not used to infer segment or word confidence.

The provider-neutral `NormalizedTranscript` contains only language, text and
segments; the pipeline adds source identity and schema version. Validation rejects
nonfinite numbers, invalid ranges, invalid probability values, out-of-order segments,
and word/token timing outside its segment. No synthesized word timing is used.

## project_transcript.json

- `schema_version`: `"1.1"`.
- `project`: name, clip_count, duration (sum of full source durations).
- `languages`: distinct reported clip languages, in first occurrence order.
- `clips`: ordered identity list with clip_id, source_file, original_filename,
  source_path_base, order, source_duration, project_start and project_end.
- `text`: nonempty clip texts joined with newlines.
- `segments`: original segment ID/text/confidence/words/tokens plus clip_id,
  source_file, source_path_base, source_start/source_end and project_start/project_end. Segment
  `id` is local to its clip; `(clip_id, id)` uniquely identifies a segment.
  Nested word/token times stay source-relative; add the clip's project_start
  to obtain project-relative word/token times when available.

For clip i, offset = sum(duration of all preceding clips).
Project time = offset + source time. Offsets never use the last ASR segment end.
For example, a second clip after a 100-second source with segment [13.2,16.8]
maps to [113.2,116.8]. Sources are never concatenated before transcription.
Project boundaries and mapped times are rounded to six decimals for serialization.

## Identity and portability

Saved projects are immutable in Phase 1. IDs are assigned once and persisted in
both JSON levels; filename changes cannot alter IDs in a saved project. A new CLI
run creates a separate project. Schema 1.1 changes saved source references from
absolute paths to relative paths with an explicit base; schema 1.0 outputs are
not rewritten automatically.

### Source path semantics

`source.file` in clip JSON and `source_file` in project clips/segments refer to
the same original file. Their base is recorded as `source.path_base` in clip JSON
and `source_path_base` in project clips/segments:

- `project`: resolve the forward-slash path from the directory containing
  `project_transcript.json`, even for a per-clip JSON stored deeper in `clips/`.
  Example: `../../TestMedia/multi_basic/001.MOV` from `Output/demo/`.
  Parent (`..`) components are intentional because source media is not copied.
- `input_root`: if source and output are on different drives, or share only a
  filesystem root, no useful relative link exists. Store the original basename
  relative to the original input parent folder. A future consumer must bind that
  folder explicitly per `clip_id`; neither its absolute location nor a machine
  root is saved. Different clips may require different input roots, so bind by
  clip ID rather than assuming a global folder.

Resolve paths with `Path(base).joinpath(*PurePosixPath(source_file).parts)`.
This portable spelling is independent of the host's path separator. Runtime media
processing still uses resolved absolute paths internally; diagnostic logs may
also contain runtime paths, and are not portable project data.

Relocating the bundle containing source media and outputs preserves `project`
references if their relative layout remains the same. Moving only the output
folder requires rebinding/updating references; source media is never embedded or
copied automatically. `input_root` references require explicit folder rebinding
at the destination. Clip IDs, original filenames and timestamps stay unchanged.

## Timing levels

- **Segments** are engine-reported spans of speech with text and source-relative
  start/end seconds. They are broader than individual words or model tokens.
- **Words** are linguistically meaningful word units only when the provider
  actually reports them. `words: null` means unavailable; no boundaries or
  probabilities are inferred from tokens, whitespace or character counts.
- **Tokens** are ASR model vocabulary units, which may be words, subwords,
  punctuation or UTF-8 byte fragments reassembled as described below. They are
  not guaranteed to be linguistic words. `tokens: null` means unavailable;
  individual tokens can also have null timing.

Token timings may be available when word timings are unavailable. Later editing
code must never assume `words` is non-null. If word timing is absent, tokens with
valid reported start/end may serve as the precise timing fallback; if token timing
is absent too, only segment timing is available. Do not invent more precise
boundaries. In project JSON, nested words/tokens/component times remain
source-relative; add the clip's `project_start` to obtain project time.

## UTF-8 compatibility with whisper.cpp v1.8.2

Generated JSON is read as bytes and decoded strictly as UTF-8. This release's
full-JSON exporter can put pieces of one UTF-8 character into separate token
strings. For example, `剪` may be emitted as bytes `e5 89` in one string and
`aa` in the next. The segment text is intact, but the raw JSON is not UTF-8.
Current upstream CLI source merges these fragments before writing JSON:
https://github.com/ggml-org/whisper.cpp/blob/master/examples/cli/cli.cpp

The adapter supports the pinned release without changing the local dependency:
only on a UTF-8 decoding failure, it parses with reversible `surrogateescape`
byte carriers and reassembles adjacent token fragments within each segment.
Only an incomplete trailing UTF-8 sequence may be continued. The assembled text
and every other JSON string must then pass strict UTF-8 validation. Unexpected
bytes in segment text, malformed bytes and incomplete groups are rejected, never
ignored or replaced. No fragments are joined across segments. Valid UTF-8 output
retains its original token structure.

Reassembled tokens have valid Unicode `text`. Their `confidence` is null because
there is no justified probability for the combined group. An optional
`components` array preserves each original `token_id`, `confidence`, nullable
source-relative `start`/`end` in seconds and `text_bytes_hex` (the exact original
UTF-8 byte fragment, losslessly encoded as hexadecimal). When all component
bounds exist, the group's bounds use the first start and last end; otherwise
both are null. This is an additive field in schema 1.1. It describes byte-token
reassembly, not word aggregation.

Console stdout/stderr have a separate policy: the process runner retains raw
bytes, decodes valid UTF-8 text, visibly escapes invalid bytes for display and
logs a warning naming the tool/stream/encoding/byte position. It never uses these
console strings as whisper transcript data. ffprobe structured stdout is decoded
strictly from its raw bytes. Generated transcript failures name the JSON path,
UTF-8 encoding and the offending byte span when available.
