#!/usr/bin/env bash
# Devcontainer postCreate: one-time setup when the devcontainer is created.
#
# Invoked once via .devcontainer/devcontainer.json -> postCreateCommand.
# Re-running is safe — defaults.sh is idempotent.
#
# Anything that's purely about the *initial graph contents* lives in
# scripts/defaults.sh so it can be replayed (e.g. by scripts/reset.sh)
# without invoking devcontainer-only setup.

set -euo pipefail

WORKSPACE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ---- wipe stale graph state so the bootstrap is canonical ----------------
# The graph store persists in data/. If you change a node's id (e.g. a
# rename like global-settings -> settings) old entries linger. Wiping
# here keeps the bootstrap as the single source of truth for what the
# DAG looks like out of the box.

if [[ -d "$WORKSPACE_ROOT/data" ]]; then
    echo "wiping existing graph state in $WORKSPACE_ROOT/data ..."
    rm -rf "$WORKSPACE_ROOT/data"
fi

# ---- the bootstrap -------------------------------------------------------

bash "$WORKSPACE_ROOT/scripts/defaults.sh"
