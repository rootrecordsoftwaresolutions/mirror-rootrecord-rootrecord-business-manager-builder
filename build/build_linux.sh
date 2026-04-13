#!/usr/bin/env bash
# Build dist/RootRecordBusinessManager/ on Linux or WSL (one-folder bundle; run ./RootRecordBusinessManager inside).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python3 -m pip install -q -r requirements.txt -r requirements-build.txt
python3 -m PyInstaller --noconfirm "$ROOT/build_rootrecord.spec"
echo "Built: $ROOT/dist/RootRecordBusinessManager/RootRecordBusinessManager"
ls -la "$ROOT/dist/RootRecordBusinessManager/RootRecordBusinessManager" 2>/dev/null || ls -la "$ROOT/dist/RootRecordBusinessManager/"
