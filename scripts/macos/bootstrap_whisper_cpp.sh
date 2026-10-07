#!/usr/bin/env bash
set -euo pipefail
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'This development bootstrap is intended for macOS.' >&2
  exit 1
fi
for tool in git cmake clang; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing $tool. Install developer tools (xcode-select --install) and CMake, then retry." >&2
    exit 1
  fi
done
for tool in ffmpeg ffprobe; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Note: $tool is missing; install FFmpeg before running transcription." >&2
  fi
done
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
dep="$repo_root/.local/whisper.cpp"
# Pin a release for reproducible integration. Never pull or reset existing checkouts.
ref=v1.8.2
mkdir -p "$repo_root/.local"
if [[ ! -d "$dep" ]]; then
  git clone --depth 1 --branch "$ref" https://github.com/ggml-org/whisper.cpp.git "$dep"
elif [[ ! -f "$dep/CMakeLists.txt" || ! -d "$dep/.git" ]]; then
  echo "Existing dependency directory is not a whisper.cpp checkout: $dep" >&2
  exit 1
fi
cmake -S "$dep" -B "$dep/build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$dep/build" --config Release --target whisper-cli -j 2
printf '\nBuilt: %s\n' "$dep/build/bin/whisper-cli"
printf 'Download the multilingual small model:\n  bash "%s/models/download-ggml-model.sh" small\n' "$dep"
