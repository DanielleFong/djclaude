#!/bin/bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$HERE/codex_scroll.swift"
BINARY="$HERE/codex-scroll"
CACHE_ROOT="${TMPDIR:-/tmp}/djclaude-swift-cache"

if [[ -x "$BINARY" && "$BINARY" -nt "$SOURCE" ]]; then
  exit 0
fi

mkdir -p "$CACHE_ROOT/clang" "$CACHE_ROOT/swift"
env \
  CLANG_MODULE_CACHE_PATH="$CACHE_ROOT/clang" \
  SWIFT_MODULE_CACHE_PATH="$CACHE_ROOT/swift" \
  xcrun swiftc -O "$SOURCE" -o "$BINARY"

echo "built $BINARY"
