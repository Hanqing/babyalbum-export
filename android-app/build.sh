#!/bin/bash
# Gradle-free build: aapt2 -> javac -> d8 -> zip -> zipalign -> apksigner.
# Output: build/babyalbum-export.apk (debug-signed unless KEYSTORE is set).
set -euo pipefail
cd "$(dirname "$0")"

SDK="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
BT="${BUILD_TOOLS:-$(ls -d "$SDK"/build-tools/* | sort -V | tail -1)}"
PLATFORM="${PLATFORM_JAR:-$(ls -d "$SDK"/platforms/android-* | sort -V | tail -1)/android.jar}"
JAVA_HOME="${JAVA_HOME:-/Applications/Android Studio.app/Contents/jbr/Contents/Home}"
KEYSTORE="${KEYSTORE:-$HOME/.android/debug.keystore}"
KS_PASS="${KS_PASS:-android}"
KEY_ALIAS="${KEY_ALIAS:-androiddebugkey}"
VERSION_CODE="${VERSION_CODE:-1}"
VERSION_NAME="${VERSION_NAME:-0.1.0}"
export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"  # d8 and apksigner are scripts that call `java`

OUT=build
rm -rf "$OUT"
mkdir -p "$OUT/gen" "$OUT/classes" "$OUT/dex"

echo "· resources"
"$BT/aapt2" compile --dir res -o "$OUT/res.zip"
"$BT/aapt2" link -o "$OUT/base.apk" -I "$PLATFORM" --manifest AndroidManifest.xml \
  --java "$OUT/gen" --min-sdk-version 26 --target-sdk-version 35 \
  --version-code "$VERSION_CODE" --version-name "$VERSION_NAME" "$OUT/res.zip"

echo "· java"
"$JAVA_HOME/bin/javac" -nowarn -Xlint:-options -source 8 -target 8 -encoding UTF-8 \
  -bootclasspath "$PLATFORM:$BT/core-lambda-stubs.jar" -classpath "$PLATFORM" -d "$OUT/classes" \
  $(find src "$OUT/gen" -name '*.java')

echo "· dex"
"$BT/d8" --min-api 26 --release --lib "$PLATFORM" --output "$OUT/dex" $(find "$OUT/classes" -name '*.class')

echo "· package"
cp "$OUT/base.apk" "$OUT/unsigned.apk"
(cd "$OUT/dex" && zip -q -X ../unsigned.apk classes.dex)
"$BT/zipalign" -f -p 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"
"$BT/apksigner" sign --ks "$KEYSTORE" --ks-pass "pass:$KS_PASS" --key-pass "pass:$KS_PASS" \
  --ks-key-alias "$KEY_ALIAS" --out "$OUT/babyalbum-export.apk" "$OUT/aligned.apk"
"$BT/apksigner" verify "$OUT/babyalbum-export.apk"
ls -la "$OUT/babyalbum-export.apk"
