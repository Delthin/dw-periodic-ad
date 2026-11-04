# Source from the project root: source scripts/env.sh. Also works in a ZIP export.
if [ -n "${BASH_SOURCE:-}" ]; then
  export PERIODIC_AD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
else
  export PERIODIC_AD_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd -P)"
fi
export JAVA_HOME="$(brew --prefix openjdk@17)/libexec/openjdk.jdk/Contents/Home"
export MAVEN_HOME="$PERIODIC_AD_ROOT/.local/tools/apache-maven-3.9.11"
export IOTDB_HOME="$PERIODIC_AD_ROOT/.local/runtime/apache-iotdb-2.0.11-all-bin"
export PATH="$JAVA_HOME/bin:$MAVEN_HOME/bin:$PERIODIC_AD_ROOT/.venv/bin:$PATH"
