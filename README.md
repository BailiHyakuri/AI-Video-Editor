# AI Video Editor — Phase 1

An open-source-intended, cross-platform automatic talking-head video editor.
The current implementation only ingests video and transcribes audio. There is no
LLM integration, editing, subtitles, rendering, export, GUI, database or server.
The existing future-phase directories are preserved and remain empty.

## Platform status and prerequisites

Current development/test machine: macOS, Apple Silicon M1, 16 GB RAM.
Python core tests, a synthetic FFmpeg smoke test, and real whisper.cpp small-model
transcription of one recording and three ordered clips have passed here.
Windows and Linux are planned but **not yet validated**. The bootstrap build
has not been independently validated by the automated test suite.

Use Python 3.10+ (no Python packages required), FFmpeg and ffprobe on PATH,
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
Do not add credentials; there are no cloud or LLM providers.

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
`NormalizedTranscript`; the pipeline does not import whisper.cpp. No later-phase
features are implemented. See [schema details](docs/transcript-schema.md).

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
