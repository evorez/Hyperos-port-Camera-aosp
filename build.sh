#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] python3 is required to build the module." >&2
  exit 1
fi

ALLOW_NO_APK="false"
for arg in "$@"; do
  if [ "$arg" = "--allow-no-apk" ]; then
    ALLOW_NO_APK="true"
  fi
done

BUILD_LOG="$(mktemp)"
trap 'rm -f "$BUILD_LOG"' EXIT

python3 "$REPO_ROOT/tools/build_module.py" "$@" | tee "$BUILD_LOG"

BUILT_ZIP="$(sed -n 's/^\[OK\] Built ZIP : //p' "$BUILD_LOG" | tail -n 1)"
if [ -n "$BUILT_ZIP" ] && [ -f "$BUILT_ZIP" ]; then
  if [ "$ALLOW_NO_APK" = "true" ]; then
    python3 "$REPO_ROOT/tools/validate_module.py" --allow-no-apk "$BUILT_ZIP"
  else
    python3 "$REPO_ROOT/tools/validate_module.py" "$BUILT_ZIP"
  fi
fi
