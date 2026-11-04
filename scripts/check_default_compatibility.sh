#!/usr/bin/env bash
# Compare evidence_clip-disabled output against the pre-change core fixture.
# Fixtures are test-only snapshots, never included in a production classpath.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
JAVA="${JAVA_HOME:+$JAVA_HOME/bin/}java"
JAVAC="${JAVA_HOME:+$JAVA_HOME/bin/}javac"
BUILD="$ROOT/.local/default-compatibility"
OUT="${1:-$ROOT/results/post-feedback/java}"
FIXTURE="$ROOT/tests/fixtures/pre-evidence-clip"
SRC="$ROOT/detector-core/src/main/java/edu/course/periodicad"
mkdir -p "$BUILD/before" "$BUILD/after" "$OUT"
"$JAVAC" -d "$BUILD/before" \
  "$FIXTURE/DetectorConfig.java" "$FIXTURE/PeriodicDetector.java" \
  "$SRC/Detection.java" "$SRC/Statistics.java" "$SRC/RingBuffer.java" \
  "$FIXTURE/CompatibilityProbe.java"
"$JAVAC" -d "$BUILD/after" \
  "$SRC/DetectorConfig.java" "$SRC/PeriodicDetector.java" "$SRC/RobustArModel.java" \
  "$SRC/Detection.java" "$SRC/Statistics.java" "$SRC/RingBuffer.java" \
  "$FIXTURE/CompatibilityProbe.java"
"$JAVA" -cp "$BUILD/before" CompatibilityProbe > "$OUT/compatibility-before.txt"
"$JAVA" -cp "$BUILD/after" CompatibilityProbe > "$OUT/compatibility-after.txt"
cmp "$OUT/compatibility-before.txt" "$OUT/compatibility-after.txt"
{
  wc -l "$OUT/compatibility-before.txt"
  shasum -a 256 "$OUT/compatibility-before.txt" "$OUT/compatibility-after.txt"
  printf '%s\n' 'PASS: exact pre-change/current output match; 120 scenarios x 200 inputs; methods, strategies, score modes and legacy/map/properties construction.'
} | tee "$OUT/compatibility.log"
