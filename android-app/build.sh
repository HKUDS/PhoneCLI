#!/usr/bin/env bash
# Build the APK without Gradle: aapt2 -> javac -> d8 -> zipalign -> apksigner.
#
# Useful where Gradle cannot be downloaded, and it keeps the M0 shell honest:
# no dependencies means nothing to resolve.
set -euo pipefail

SDK="${ANDROID_SDK:-$HOME/Library/Android/sdk}"
BT="${BUILD_TOOLS:-$SDK/build-tools/35.0.0}"
PLATFORM="${PLATFORM_JAR:-$SDK/platforms/android-33/android.jar}"
JAVA_HOME="${JAVA_HOME:-/Applications/Android Studio.app/Contents/jbr/Contents/Home}"
PATH="$JAVA_HOME/bin:$PATH"

cd "$(dirname "$0")"
OUT=build
rm -rf "$OUT"
mkdir -p "$OUT/compiled" "$OUT/classes" "$OUT/dex" "$OUT/gen"

echo "[1/6] aapt2 compile"
"$BT/aapt2" compile --dir app/src/main/res -o "$OUT/compiled/res.zip"

echo "[2/6] aapt2 link"
"$BT/aapt2" link \
    -o "$OUT/app-unsigned.apk" \
    -I "$PLATFORM" \
    --manifest app/src/main/AndroidManifest.xml \
    --java "$OUT/gen" \
    --min-sdk-version 31 \
    --target-sdk-version 33 \
    "$OUT/compiled/res.zip"

echo "[3/6] javac"
# no -bootclasspath: JDK 9+ rejects it together with -target; android.jar on
# the classpath is enough for a dependency-free app
javac -source 17 -target 17 -nowarn \
    -classpath "$PLATFORM" \
    -d "$OUT/classes" \
    $(find app/src/main/java "$OUT/gen" -name '*.java')

echo "[4/6] d8"
"$BT/d8" --lib "$PLATFORM" --min-api 31 --output "$OUT/dex" \
    $(find "$OUT/classes" -name '*.class')

echo "[5/6] package + align"
(cd "$OUT" && zip -q app-unsigned.apk dex/classes.dex && mv dex/classes.dex . && zip -q -d app-unsigned.apk dex/classes.dex || true)
(cd "$OUT" && zip -q app-unsigned.apk classes.dex)
"$BT/zipalign" -f 4 "$OUT/app-unsigned.apk" "$OUT/app-aligned.apk"

echo "[6/6] sign"
# keep the keystore outside build/ so rebuilt APKs stay upgrade-compatible
if [ ! -f debug.keystore ]; then
    keytool -genkeypair -v -keystore debug.keystore -storepass android \
        -keypass android -alias androiddebugkey -keyalg RSA -keysize 2048 \
        -validity 10000 -dname "CN=Android Debug,O=Android,C=US" >/dev/null 2>&1
fi
"$BT/apksigner" sign --ks debug.keystore --ks-pass pass:android \
    --key-pass pass:android --ks-key-alias androiddebugkey \
    --out "$OUT/app-debug.apk" "$OUT/app-aligned.apk"
"$BT/apksigner" verify "$OUT/app-debug.apk"

echo
echo "APK: $(pwd)/$OUT/app-debug.apk"
ls -lh "$OUT/app-debug.apk"
