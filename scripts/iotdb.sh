#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh

# The official stop scripts target ports. Check ownership before invoking them.
check_owner() {
  local port="$1" pid command
  for pid in $(lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true); do
    command="$(ps -p "$pid" -o command=)"
    if [[ "$command" != *"$IOTDB_HOME/"* ]]; then
      echo "Port $port belongs to a different service (PID $pid); refusing operation." >&2
      return 1
    fi
  done
}

case "${1:-}" in
  start)
    for port in 6667 10710 10720 10730 10740 10750 10760; do
      if lsof -t -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "Port $port is occupied; inspect with bash scripts/iotdb.sh status, do not start again." >&2
        exit 1
      fi
    done
    MEMORY_SIZE=1G bash "$IOTDB_HOME/sbin/start-standalone.sh"
    python - <<'PY'
import socket, time
for _ in range(60):
    try:
        with socket.create_connection(('127.0.0.1', 6667), timeout=1):
            break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit('IoTDB did not listen within 60s; inspect its logs, do not delete data')
PY
    java -jar runner/target/periodic-ad-runner.jar query 'SHOW VERSION'
    java -jar runner/target/periodic-ad-runner.jar query 'SHOW CLUSTER'
    ;;
  stop)
    check_owner 6667
    check_owner 10710
    # Do not force-kill. Missing listeners indicate the node is already stopped.
    if lsof -t -nP -iTCP:6667 -sTCP:LISTEN >/dev/null 2>&1; then
      bash "$IOTDB_HOME/sbin/stop-datanode.sh"
      python - <<'PY'
import socket, time
for _ in range(60):
    try:
        with socket.create_connection(('127.0.0.1', 6667), timeout=1): pass
    except OSError:
        break
    time.sleep(1)
else:
    raise SystemExit('DataNode did not stop within 60s; inspect before stopping ConfigNode')
PY
    fi
    if lsof -t -nP -iTCP:10710 -sTCP:LISTEN >/dev/null 2>&1; then
      bash "$IOTDB_HOME/sbin/stop-confignode.sh"
      python - <<'PY'
import socket, time
for _ in range(60):
    try:
        with socket.create_connection(('127.0.0.1', 10710), timeout=1): pass
    except OSError:
        break
    time.sleep(1)
else:
    raise SystemExit('ConfigNode did not stop within 60s; inspect its logs')
PY
    fi
    ;;
  status)
    for port in 6667 10710 10720 10730 10740 10750 10760; do check_owner "$port"; done
    lsof -nP -iTCP:6667 -iTCP:10710 -iTCP:10720 -iTCP:10730 -iTCP:10740 -iTCP:10750 -iTCP:10760 -sTCP:LISTEN || true
    ;;
  cli)
    check_owner 6667
    exec bash "$IOTDB_HOME/sbin/start-cli.sh" -h 127.0.0.1 -p 6667 -u root -sql_dialect tree
    ;;
  *) echo 'Usage: bash scripts/iotdb.sh {start|stop|status|cli}' >&2; exit 2 ;;
esac
