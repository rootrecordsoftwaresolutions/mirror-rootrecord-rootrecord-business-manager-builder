#!/usr/bin/env bash
# Build dist/RootRecord/ on Linux or WSL (one-folder bundle; run ./RootRecord inside).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python3 -m pip install -q -r requirements.txt -r requirements-build.txt
python3 -m PyInstaller --noconfirm "$ROOT/build_rootrecord.spec"
echo "Built: $ROOT/dist/RootRecord/RootRecord"
ls -la "$ROOT/dist/RootRecord/RootRecord" 2>/dev/null || ls -la "$ROOT/dist/RootRecord/"
