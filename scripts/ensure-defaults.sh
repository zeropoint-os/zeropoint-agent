#!/usr/bin/env bash
# Seed the canonical graph only when SQLite contains no nodes.
#
# Launch tasks call this before starting the debug server. It covers existing
# containers whose one-time postCreate reset was skipped or failed, without
# reconciling the graph on every launch.
set -euo pipefail

WORKSPACE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export ZEROPOINT_ROOT_PATH="${ZEROPOINT_ROOT_PATH:-$WORKSPACE_ROOT}"
export ZP_MODULE_STORAGE="${ZP_MODULE_STORAGE:-$WORKSPACE_ROOT/data/modules}"
DB_PATH="$ZEROPOINT_ROOT_PATH/data/state.db"

if python - "$DB_PATH" <<'PY'
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(1)
try:
    connection = sqlite3.connect(path)
    count = connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
except (sqlite3.Error, TypeError):
    raise SystemExit(1)
finally:
    try:
        connection.close()
    except NameError:
        pass
raise SystemExit(0 if count else 1)
PY
then
    echo "ZeroPoint graph already seeded."
    exit 0
fi

echo "ZeroPoint graph is empty; seeding defaults in ${ZEROPOINT_MODE:-mock} mode ..."
export ZEROPOINT_AGENT_LOCAL=1
bash "$WORKSPACE_ROOT/scripts/defaults.sh"
