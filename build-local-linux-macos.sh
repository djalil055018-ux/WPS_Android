#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
WORK="$ROOT/.build/upstream"
rm -rf "$ROOT/.build"
mkdir -p "$ROOT/.build"
git clone --depth 1 --branch 2.2.6 --recurse-submodules https://github.com/2dust/v2rayNG.git "$WORK"
python3 "$ROOT/tools/patch_v2rayng.py" --source "$WORK/V2rayNG" --kit-root "$ROOT"
cd "$WORK/V2rayNG"
chmod +x gradlew
./gradlew :app:assembleFdroidDebug --no-daemon
APK="$(find app/build/outputs/apk/fdroid/debug -type f -name '*universal*.apk' | head -n 1)"
cp "$APK" "$ROOT/WinPhoneStoreVPN-test.apk"
echo "APK created: $ROOT/WinPhoneStoreVPN-test.apk"
