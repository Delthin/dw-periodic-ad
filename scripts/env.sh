# Source this file from inside the project: source scripts/env.sh
export PERIODIC_AD_ROOT="$(git rev-parse --show-toplevel)"
export JAVA_HOME="$(brew --prefix openjdk@17)/libexec/openjdk.jdk/Contents/Home"
export MAVEN_HOME="$PERIODIC_AD_ROOT/.local/tools/apache-maven-3.9.11"
export IOTDB_HOME="$PERIODIC_AD_ROOT/.local/runtime/apache-iotdb-2.0.11-all-bin"
export PATH="$JAVA_HOME/bin:$MAVEN_HOME/bin:$PERIODIC_AD_ROOT/.venv/bin:$PATH"
