#!/bin/bash
# Run explicitly after offline Maven package; does not start/stop any service.
set -euo pipefail
cd "$(dirname "$0")/.."
WORKTREE="$(pwd -P)"
if GIT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" && [[ "$(cd "$GIT_ROOT" && pwd -P)" == "$WORKTREE" ]]; then
  COMMON="$(git rev-parse --git-common-dir)"
  MAIN_ROOT="$(cd "$COMMON/.." && pwd -P)"
else
  # Submission ZIPs have no .git; preserve the same local-runtime boundary.
  MAIN_ROOT="$WORKTREE"
fi
JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home}"
export JAVA_HOME
JAVA="$JAVA_HOME/bin/java"
JAR_TOOL="$JAVA_HOME/bin/jar"
RUNTIME_ROOT="$(cd "$MAIN_ROOT/.local/runtime" && pwd -P)"
IOTDB_HOME="${IOTDB_HOME:-$RUNTIME_ROOT/apache-iotdb-2.0.11-all-bin}"
IOTDB_HOME="$(cd "$IOTDB_HOME" && pwd -P)"
case "$IOTDB_HOME/" in "$RUNTIME_ROOT/"*) ;; *) echo 'Refusing a runtime outside this project .local/runtime' >&2; exit 1;; esac
[[ -x "$JAVA" && -x "$JAR_TOOL" && -f detector-udf/target/periodic-ad-udf.jar && -f runner/target/periodic-ad-runner.jar ]]
# Check the actual listener owner BEFORE copying a JAR or issuing DDL.
listener_found=false
for port in 6667 10710; do
  for pid in $(lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true); do
    command="$(ps -p "$pid" -o command=)"
    if [[ "$command" != *"$IOTDB_HOME/"* ]]; then
      echo "Port $port is not owned by this project runtime (PID $pid); refusing deployment." >&2
      exit 1
    fi
    if [[ "$port" == 6667 ]]; then listener_found=true; fi
  done
done
if [[ "$listener_found" != true ]]; then echo 'No project IoTDB listener on 6667; start it separately.' >&2; exit 1; fi
UDF_DIR="$IOTDB_HOME/ext/udf"
mkdir -p "$UDF_DIR" "$MAIN_ROOT/.local/backups"
UDF_DIR="$(cd "$UDF_DIR" && pwd -P)"
case "$UDF_DIR/" in "$IOTDB_HOME/"*) ;; *) echo 'Refusing redirected ext/udf directory' >&2; exit 1;; esac
LOCK="$MAIN_ROOT/.local/deploy-detector.lock"
mkdir "$LOCK" || { echo 'Another deployment lock exists; inspect ownership before retrying.' >&2; exit 1; }
TEMP=""
trap '[[ -z "$TEMP" ]] || rm -f "$TEMP"; rmdir "$LOCK"' EXIT
TARGET="$UDF_DIR/periodic-ad-udf.jar"
[[ ! -L "$TARGET" ]] || { echo 'Refusing symlink JAR target' >&2; exit 1; }
# Old echo artifacts include EchoValue/EchoUDTF. Never load two overlapping project
# JARs: replacing the SAME filename is safe; a second overlapping JAR is not.
NEW_CLASSES="$("$JAR_TOOL" tf detector-udf/target/periodic-ad-udf.jar | grep '^edu/course/periodicad/.*\.class$' | sort)"
for other in "$UDF_DIR"/*.jar; do
  [[ -f "$other" && "$other" != "$TARGET" ]] || continue
  OTHER_ENTRIES="$("$JAR_TOOL" tf "$other")"
  OTHER_CLASSES="$(printf '%s\n' "$OTHER_ENTRIES" | grep '^edu/course/periodicad/.*\.class$' | sort || true)"
  duplicates="$(comm -12 <(printf '%s\n' "$NEW_CLASSES") <(printf '%s\n' "$OTHER_CLASSES"))"
  if [[ -n "$duplicates" ]]; then
    echo "Refusing duplicate project classes in $other (including legacy echo jars)." >&2
    echo 'Audit and move only the project-owned legacy artifact OUTSIDE ext/udf before deployment; do not delete unrelated jars.' >&2
    printf '%s\n' "$duplicates" >&2
    exit 1
  fi
done
if [[ -f "$TARGET" ]]; then
  BACKUP="$MAIN_ROOT/.local/backups/periodic-ad-udf-$(date -u +%Y%m%dT%H%M%SZ)-$$.jar"
  cp "$TARGET" "$BACKUP"
  echo "Previous JAR preserved: $BACKUP"
fi
TEMP="$(mktemp "$UDF_DIR/.periodic-ad-XXXXXX")"
cp detector-udf/target/periodic-ad-udf.jar "$TEMP"
mv "$TEMP" "$TARGET"
TEMP=""
shasum -a 256 "$TARGET"
# The only function dropped/re-registered is periodic_ad. periodic_echo is untouched.
"$JAVA" -jar "$WORKTREE/runner/target/periodic-ad-runner.jar" register-detector
