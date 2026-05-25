#!/usr/bin/env bash
# Reset zeropoint to a fresh state.
#
# Tears down every docker container the agent has spun up, wipes the
# persisted graph + module data dirs, and reseeds the canonical
# default graph via scripts/defaults.sh.
#
# Usage:
#   bash scripts/reset.sh
#   (or via the "Reset zeropoint" VS Code task)
#
# Safe to run repeatedly. Stops the agent if it's running.

set -euo pipefail

WORKSPACE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ---- refuse to run if the agent is up ------------------------------------
# The agent holds graph.db open and a lock on data/. Wiping under a
# running agent corrupts both. We don't auto-kill it — the user (or
# their IDE) should explicitly stop the debug session first.

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

bash "$WORKSPACE_ROOT/scripts/defaults.sh"

echo
echo "Reset complete. Restart your agent (debug session or 'zeropoint-agent serve')"
echo "to pick up the fresh graph."
