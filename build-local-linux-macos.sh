#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
WORK="$ROOT/.build/upstream"
rm -rf "$ROOT/.build"
mkdir -p "$ROOT/.build"
git clone --depth 1 --branch 2.2.6 --recurse-submodules https://github.com/2dust/v2rayNG.git "$WORK"
python3 "$ROOT/tools/patch_v2rayng_v1.0.py" --source "$WORK/V2rayNG" --kit-root "$ROOT"
cd "$WORK/V2rayNG"
chmod +x gradlew
./gradlew :app:assemblePlaystoreDebug --no-daemon
APK="$(find app/build/outputs/apk/playstore/debug -type f -name '*.apk' | head -n 1)"
test -n "$APK" || { echo "PAUK APK not found" >&2; exit 1; }
cp "$APK" "$ROOT/PAUK-v1.1.apk"
echo "APK created: $ROOT/PAUK-v1.1.apk"
