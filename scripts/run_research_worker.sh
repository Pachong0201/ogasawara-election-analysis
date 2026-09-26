#!/usr/bin/env bash
# Resume durable research jobs and retries independently of Feishu foreground requests.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/.deps${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m runtime.auto_research "$@"
