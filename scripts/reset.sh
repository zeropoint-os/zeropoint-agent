#!/usr/bin/env bash
# Reset zeropoint to a fresh state.
#
# Tears down every docker container the agent has spun up, removes the
# SQLite state database and module data, then reseeds the canonical graph
# through the normal CLI/API transaction path in scripts/defaults.sh.
#
# Usage:
#   bash scripts/reset.sh
#   (or via the "Reset zeropoint" VS Code task)
#
# Safe to run repeatedly. Stops the agent if it's running.

set -euo pipefail

WORKSPACE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ---- refuse to run if the agent is up ------------------------------------
# The agent holds data/state.db and its ownership lock open. Removing the
# data directory underneath a running agent would invalidate its live SQLite
# connection, so the user (or IDE) must stop the debug session first.

if curl -fsS --max-time 1 http://127.0.0.1:2370/api/health >/dev/null 2>&1; then
    echo "ERROR: zeropoint-agent is running on :2370." >&2
    echo "       Stop your debug session (or 'pkill -f zeropoint-agent serve')" >&2
    echo "       and re-run this script." >&2
    exit 1
fi

echo "==> tearing down agent-managed docker resources ..."

# Stop and remove every container — module containers, the envoy
# container, anything else. The default graph + module installs
# will recreate what's needed.
docker rm -f $(docker ps -aq) 2>/dev/null || true

# Drop unused networks (every zeropoint-module-* network the agent
# created, plus zeropoint-network).
docker network prune -f >/dev/null 2>&1 || true

echo "==> wiping persisted state ..."

# Use sudo because module containers run as root and leave root-owned
# artifacts in the bind-mounted data dir.
sudo -n rm -rf "$WORKSPACE_ROOT/data" "$WORKSPACE_ROOT/modules" 2>/dev/null \
  || rm -rf "$WORKSPACE_ROOT/data" "$WORKSPACE_ROOT/modules"
mkdir -p "$WORKSPACE_ROOT/modules"

echo "==> reseeding default graph ..."

# The CLI falls back to an in-process FastAPI app while no server is running.
# That creates data/state.db and applies each seed operation through the same
# validation and SQLite transactions used by normal API clients.
bash "$WORKSPACE_ROOT/scripts/defaults.sh"

echo
echo "Reset complete. Restart your agent (debug session or 'zeropoint-agent serve')"
echo "to pick up the fresh graph."
