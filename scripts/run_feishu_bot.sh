#!/usr/bin/env bash
# Launch the Feishu election bot.
#
# The deployment host's Python is externally managed, so runtime dependencies
# live in .deps/ (see .gitignore). A local .env provides Feishu credentials.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="$REPO_ROOT/.deps${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m bot
