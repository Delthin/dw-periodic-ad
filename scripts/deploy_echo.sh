#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
bash scripts/iotdb.sh status
java -jar runner/target/periodic-ad-runner.jar unregister-echo
mkdir -p "$IOTDB_HOME/ext/udf" .local/backups
if [[ -f "$IOTDB_HOME/ext/udf/periodic-ad-udf.jar" ]]; then
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  cp "$IOTDB_HOME/ext/udf/periodic-ad-udf.jar" ".local/backups/periodic-ad-udf-$stamp.jar"
  shasum -a 256 ".local/backups/periodic-ad-udf-$stamp.jar"
fi
cp detector-udf/target/periodic-ad-udf.jar "$IOTDB_HOME/ext/udf/"
shasum -a 256 "$IOTDB_HOME/ext/udf/periodic-ad-udf.jar"
java -jar runner/target/periodic-ad-runner.jar smoke tests/fixtures/smoke.csv
