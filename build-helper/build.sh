#!/usr/bin/env bash
# Builds pa-helper.jar: the shell-side helper that creates the virtual display.
#
# Only `shell` may create a display with canHostTasks, so this cannot live in
# the app. It is a plain dex jar run through `app_process`, which the app then
# pushes to /data/local/tmp and starts over its own adb shell.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SDK="${ANDROID_HOME:-$HERE/.toolchain/android-sdk}"
BUILD_TOOLS="$(ls -d "$SDK"/build-tools/* | sort -V | tail -1)"
PLATFORM="$(ls -d "$SDK"/platforms/android-* | sort -V | tail -1)"
JAVA_HOME="${JAVA_HOME:-/Applications/Android Studio.app/Contents/jbr/Contents/Home}"
# d8 is a wrapper script that calls `java` from PATH, not from JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"

SRC="$HERE/android-helper/src"
OUT="$HERE/build-helper"
rm -rf "$OUT/classes" "$OUT/dex"
mkdir -p "$OUT/classes" "$OUT/dex"

find "$SRC" -name '*.java' > "$OUT/sources.txt"
"$JAVA_HOME/bin/javac" \
    --release 8 -nowarn \
    -classpath "$PLATFORM/android.jar" \
    -d "$OUT/classes" \
    @"$OUT/sources.txt"

"$BUILD_TOOLS/d8" \
    --lib "$PLATFORM/android.jar" \
    --min-api 29 \
    --output "$OUT/dex" \
    $(find "$OUT/classes" -name '*.class')

# app_process looks for classes.dex at the root of CLASSPATH
"$JAVA_HOME/bin/jar" --create --file "$OUT/pa-helper.jar" -C "$OUT/dex" classes.dex

echo "built $OUT/pa-helper.jar ($(wc -c < "$OUT/pa-helper.jar") bytes)"
