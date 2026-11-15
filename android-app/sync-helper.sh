#!/usr/bin/env bash
# Copies the shell-side helper into the APK so it ships as one file.
#
# The app extracts it at runtime, pushes it to /data/local/tmp over its own adb
# shell and starts it with app_process -- see phoneagent/ondevice.py.
set -euo pipefail

APP="$(cd "$(dirname "$0")" && pwd)"
JAR="$(cd "$APP/.." && pwd)/build-helper/pa-helper.jar"
DEST="$APP/app/src/main/assets"

if [ ! -f "$JAR" ]; then
    echo "missing $JAR -- run build-helper/build.sh first" >&2
    exit 1
fi

mkdir -p "$DEST"
cp "$JAR" "$DEST/pa-helper.jar"
echo "helper asset: $(wc -c < "$DEST/pa-helper.jar") bytes"
