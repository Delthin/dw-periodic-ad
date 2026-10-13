#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v brew >/dev/null
command -v uv >/dev/null
if [[ ! -x "$(brew --prefix)/opt/openjdk@17/bin/java" ]]; then
  HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 brew install openjdk@17
fi
if ! uv python find --offline 3.12.12 >/dev/null 2>&1; then
  uv python install 3.12.12
fi
if [[ ! -d .venv ]]; then uv venv --python 3.12.12 .venv; fi
uv pip sync --python .venv/bin/python requirements-lock.txt
.venv/bin/python scripts/setup_runtime.py
echo 'Ready. Run: source scripts/env.sh'
