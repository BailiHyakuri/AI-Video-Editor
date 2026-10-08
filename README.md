# AI Video Editor — Phases 1–2

An MIT-licensed project for a cross-platform automatic talking-head video editor.
Phase 1 ingests video and transcribes audio. Phase 2 analyzes the saved transcript
and produces a **dry-run** edit plan for human review, with optional LLM semantics.
No video is cut or modified. Subtitles, effects, rendering, export, GUI, databases,
servers and Phase 3 features are not implemented. The Phase 1 layout is preserved.

## Platform status and prerequisites

Current development/test machine: macOS, Apple Silicon M1, 16 GB RAM.
Python core tests, a synthetic FFmpeg smoke test, and real whisper.cpp small-model
transcription of one recording and three ordered clips have passed here.
Windows and Linux are planned but **not yet validated**. The bootstrap build
has not been independently validated by the automated test suite. Phase 2 no-AI
analysis has been validated against the existing three-clip transcript; cloud SDK
calls are mocked in tests. Live OpenAI/compatible endpoints have not been tested.

For Phase 1 use Python 3.10+ (no Python packages required), FFmpeg and ffprobe on PATH,
and whisper.cpp's `whisper-cli` plus a GGML Whisper model. Building whisper.cpp
also requires Git, CMake and a C++ compiler. On macOS, install Xcode command-line
tools with `xcode-select --install` if needed. Install FFmpeg and CMake using your
preferred package manager; core code does not assume a package-manager path.

## macOS development bootstrap

From the repository root:

```bash
bash scripts/macos/bootstrap_whisper_cpp.sh
bash .local/whisper.cpp/models/download-ggml-model.sh small
```

The script clones pinned whisper.cpp v1.8.2 into ignored `.local/whisper.cpp`
and builds `whisper-cli` with CMake defaults. It does not install system files,
force GPU acceleration, update or reset an existing checkout. Repeated runs
reuse the checkout and build. Models stay inside this ignored local checkout
or at a separately configured path. Download is explicitly separate from build.

Start with the multilingual **small** model for Chinese/English talking-head
recordings on this machine. It is a practical initial quality/speed compromise,
not a performance guarantee. `base` is a smaller smoke-test alternative; use
multilingual names without `.en` for Chinese. See the
[upstream model documentation](https://github.com/ggml-org/whisper.cpp/tree/v1.8.2/models).
No model, binary or third-party source is part of this repository.

## Transcribe

Single file becomes a one-clip project:

```bash
python3 -m core.cli transcribe video.mov --name single-test \
  --model .local/whisper.cpp/models/ggml-small.bin \
  --whisper-binary .local/whisper.cpp/build/bin/whisper-cli
```

Multiple files are processed independently in the exact argument order:

```bash
python3 -m core.cli transcribe 001.mov 002.mov 003.mov --name multi-test \
  --model .local/whisper.cpp/models/ggml-small.bin \
  --whisper-binary .local/whisper.cpp/build/bin/whisper-cli
```

A project directory must contain `clips/`. Files are ordered lexicographically
by filename, so use zero-padded names such as `001.mov`. Hidden files are ignored;
all other regular files are validated, including unsupported files.

```bash
python3 -m core.cli transcribe-project ./MyProject \
  --model .local/whisper.cpp/models/ggml-small.bin \
  --whisper-binary .local/whisper.cpp/build/bin/whisper-cli
python3 -m core.cli --help
python3 -m core.cli transcribe --help
```

Inputs support `.mov` and `.mp4` case-insensitively, and must contain video and
audio with a finite positive container duration. ffprobe supplies container,
codec, sample-rate and frame-rate metadata. The first audio stream is selected.
Audio is normalized independently per clip to mono 16 kHz PCM signed-16 WAV.
Audio stream offsets are preserved with FFmpeg timestamp handling and silence
padding, and extraction is limited to the source container duration.

Use `--output-dir`, `--temporary-dir`, `--language zh` (default `auto`),
`--timeout 3600`, `--ffmpeg`, `--ffprobe`, or `--verbose` as needed. Tool paths
otherwise come from PATH. Timeout is per invocation, not per project.

Copy `config.example.json` to ignored `config.local.json`, fill local tool/model
paths, then pass `--config config.local.json`. CLI arguments override the file.
Relative configuration paths resolve from the current working directory.
Do not add credentials. Optional Phase 2 LLM configuration is separate, below.

## Outputs and identities

```text
Output/multi-test/
  clips/
    clip_001/
      transcript.txt
      transcript.json
    clip_002/
      transcript.txt
      transcript.json
    clip_003/
      transcript.txt
      transcript.json
  project_transcript.txt
  project_transcript.json
  logs/
    pipeline.jsonl
```

Clip IDs are independent of filenames, sequential and permanent within the saved
project. A project identity is its output name plus its immutable clip list.
Existing output projects cannot be overwritten, preventing accidental reassignment
of IDs. Creating another project/reordering inputs creates a new identity scope;
there is no mutable project editor or resume operation in Phase 1.

Schema 1.1 stores portable source paths relative to the saved project directory,
with original filenames saved separately. Move sources and outputs together while
preserving their relative layout. Unrelated roots/drives use input-root-relative
references requiring explicit folder rebinding by clip ID; see the schema path
semantics. Runtime processing may still use absolute paths. Existing schema 1.0
outputs are not automatically rewritten. JSON/TXT use UTF-8, stable ordering, six-decimal project
timing and no generated timestamps; identical provider output, inputs and project
name produce identical transcripts. ASR inference itself is not guaranteed to be
bit-for-bit deterministic across engine versions/hardware.

Only owned temporary workspaces are removed on success, error or interruption.
Source media is never changed. On failure, completed clip outputs and logs remain;
absence of a completed project transcript indicates an incomplete run. Retry with
a new output name. JSON-line logs contain events, source references and concise
errors, not transcript bodies; treat output directories as private user data.

## Architecture

- `core/media/service.py`: validation, ffprobe parsing and FFmpeg audio extraction.
- `core/tools.py`: tool discovery and the only subprocess execution boundary.
- `core/asr/base.py`: provider protocol and transcription options.
- `core/asr/whisper_cpp.py`: first provider and upstream JSON normalization.
- `core/transcript.py`: validated provider-neutral data and project timeline mapping.
- `core/pipeline.py`: independent per-clip processing, temporary cleanup and output.
- `core/config/settings.py`: minimal validated JSON settings.
- `core/cli.py`: CLI and dependency composition; no GUI dependencies.

Future providers implement `transcribe(audio_path, options)` returning
`NormalizedTranscript`; the pipeline does not import whisper.cpp. See
[Phase 1 schema details](docs/transcript-schema.md). Phase 2 modules are described below.

## Tests and limitations

```bash
python3 -m unittest discover -s tests -v
bash -n scripts/macos/bootstrap_whisper_cpp.sh
```

Tests use fake providers/runners, require no models, and cover schema rejection,
normalization, timeline offsets/order, paths, invalid inputs, tool errors,
configuration, output determinism and failure cleanup. The real-media smoke check
uses synthetic video only; no user recordings or models are committed.

Word timestamps are engine-dependent. whisper.cpp full JSON exposes tokens;
these are preserved as tokens, never mislabeled as words. Token timing may be
available while words are null. Future editing must allow missing words and may
use reported token timing as its precise fallback; no word boundaries are invented.
Missing words, language,
confidence or token timing are null. Segment times are required. Segments more
than 100 ms beyond container duration are rejected rather than silently retimed.
VAD, diarization, parallel ASR, GPU selection, mutable project manifests, resumable
runs and automatic model downloads are outside this implementation. Accuracy,
long recordings and broader recognition accuracy still require validation with
representative recordings. Chinese/English real-media transcription has passed;
Japanese text preservation is covered by regression tests, not a Japanese ASR
accuracy benchmark.

This project is licensed under the [MIT License](LICENSE).

## Multilingual JSON encoding

The pinned whisper.cpp v1.8.2 full-JSON exporter may split a UTF-8 character
between token strings, producing invalid raw UTF-8 even when segment text is
valid. The adapter losslessly reassembles these byte fragments; it never ignores
or replaces transcript bytes. Original component probabilities and byte fragments
are preserved, and invalid bytes outside recoverable token groups cause an error
with the generated JSON path, UTF-8 encoding and byte position. See the
[schema encoding policy](docs/transcript-schema.md#utf-8-compatibility-with-whispercpp-v182).
Console stdout/stderr are captured as raw bytes and handled separately from
structured transcript files. Regression tests cover Chinese, Japanese, English,
split byte tokens and non-UTF-8 console bytes without downloading a model.

## Phase 2: dry-run edit analysis

Input is `project_transcript.json` only. Phase 2 reads schema 1.1 and legacy 1.0
without modifying either. It detects transcript gaps, standalone filler hints,
exact/near/prefix repetition, correction markers, unfinished starts/sentences,
cross-clip repetition and long hesitation hints. It groups related candidates,
optionally asks an LLM for semantic choices, validates decisions and resolves
conflicting edits before saving a plan. **No media files are opened by analysis,
no FFmpeg cutting is run, and no edit is executed.**

Default mode is **normal**, with LLM disabled unless explicitly enabled through
configuration, `--llm`, `--provider` or `--model`. `--no-llm` overrides all enabling
settings and works without any API key or optional SDK.

### No-AI analysis

The available local multi-basic recording output is named
`multi-basic-encoding-fixed` (the older `multi-basic` run did not produce a project
transcript). Substitute your own successfully transcribed project's path.

```bash
python3 -m core.cli analyze \
  Output/multi-basic-encoding-fixed/project_transcript.json \
  --mode normal --no-llm \
  --output-dir Output/multi-basic-encoding-fixed/edit-analysis-normal
```

No-AI decisions are deliberately conservative: shorten qualifying transcript gaps
and propose deletion only for directly adjacent verbatim repetitions. Fillers,
near matches, correction markers and unfinished sentences remain KEEP for human
review; deterministic rules do not establish semantic intent. A gap is not proof
of acoustic silence. Reported token timing can identify precise candidates when
words are null; without exact subsegment timing, a partial phrase is KEEP-only.

### Optional OpenAI analysis

Install the optional official client into a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-llm.txt
```

On Windows activate with `.venv\Scripts\Activate.ps1` in PowerShell.
Set `OPENAI_API_KEY` securely in your shell environment. Do not paste the key into
source/config files, commit it, or pass it as a CLI argument. `.env` stays ignored;
the application does not automatically load `.env` files.

Select a model available to your account that supports Responses strict structured
outputs; the edit-analysis model is explicitly configured, never tied to ASR:

```bash
python3 -m core.cli analyze \
  Output/multi-basic-encoding-fixed/project_transcript.json \
  --mode normal --provider openai --model "YOUR_MODEL_ID" \
  --output-dir Output/multi-basic-encoding-fixed/edit-analysis-openai
```

Replace `YOUR_MODEL_ID` with an actual model ID. The official SDK handles
configured timeout and retry count; no paid calls are part of the tests. The
adapter uses Responses JSON Schema with `store=False`, and rejects malformed,
refused or incomplete output. See the official
[Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
and [Python client configuration](https://developers.openai.com/api/reference/python).

The generic `openai-compatible` provider uses the same optional SDK with Chat
Completions strict JSON-schema responses. Set `LLM_API_KEY` in the environment,
then supply `--provider openai-compatible --base-url <endpoint> --model <model>`.
Gateways must implement this capability; incompatible endpoints fail clearly.
There are no pretend Qwen, Gemini, Anthropic or Ollama adapters. They can later
implement the vendor-neutral provider protocol independently.

### Privacy and configuration

Only bounded candidate text, adjacent context, structural hints, durations and
allowed actions are sent for semantic review. No video/audio, source file paths,
full transcript, ASR token arrays or exact timestamps are sent. The model returns
candidate IDs, action, confidence and reason; core timing cannot be overridden.
Text is treated as untrusted data, not executable instructions. Core gives the
model no tools or command execution. `store=False` does not imply zero provider
retention; provider policies still apply.

Copy `analysis.config.example.json` to ignored `analysis.config.local.json` and
pass `--config analysis.config.local.json`. It configures mode, pause thresholds,
target duration, LLM enablement/provider/model, base URL, timeout, retries and
bounded batching/context. API keys are environment-only and rejected as config
fields. `--timeout`, `--retries`, `--target-pause-duration` and other CLI settings
override the file. Presets live in `core/config/edit_presets.json`:

| Mode | Shorten gap threshold | Target | Minimum LLM edit confidence |
| --- | ---: | ---: | ---: |
| conservative | 2.0s | 0.35s | 0.95 |
| normal | 1.2s | 0.30s | 0.90 |
| aggressive | 0.65s | 0.20s | 0.85 |

Low-confidence or ambiguous semantic edits become KEEP. Active edits may not
overlap; containing DELETEs and higher-level correction/restart candidates take
priority. Suppressed edits remain visible as KEEP, with an explanation.

### Outputs and architecture

Default output is a new `edit-analysis/` beside the transcript:

```text
Output/<project>/
  project_transcript.json       # read-only Phase 1 input
  edit-analysis/
    edit_plan.json
    edit_plan.txt
```

Existing analysis directories are never overwritten; choose a new `--output-dir`
for reruns. JSON contains all candidates and decisions; TXT focuses on edits and
important preserved speech candidates rather than every sentence/short pause.
Each action has one or more source clip ranges plus exact project mapping,
original text/duration, optional target duration, confidence, reason and decision
source. The input hash records provenance. Output files are Git-ignored.
See [edit-plan schema](docs/edit-plan-schema.md) and its
[machine-readable JSON Schema](docs/edit-plan.schema.json).

- `core/analysis/`: transcript validation, measured-timing candidates, deterministic
  decisions, semantic grouping/review and overlap resolution.
- `core/edit_plan/`: action schema, range validation and human report.
- `core/llm/base.py`: provider-neutral structured request/response protocol.
- `core/llm/openai_provider.py`: optional official Responses adapter.
- `core/llm/compatible_provider.py`: optional compatible Chat Completions adapter.
- `prompts/candidate_review.md`: external conservative semantic-review instructions.
- `core/config/analysis_settings.py` and `edit_presets.json`: independent Phase 2 policy.

Run the full suite with `python3 -m unittest discover -s tests -v`. Phase 2 tests
cover pause categories, exact/near/cross-clip repetition, fillers, correction,
false starts, abandoned sentences, token timing, multi-range mapping, overlap,
presets, no-AI operation, schema rejection, malformed/hallucinated LLM decisions,
multilingual text and mocked SDK boundaries. Tests require no paid API usage.
Broader semantic accuracy, live cloud/gateway integration and Windows/Linux
execution remain unvalidated. Phase 3 is not implemented.
