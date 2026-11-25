#!/usr/bin/env bash
# Prepare the backend-only development container after Docker-in-Docker starts.
set -euo pipefail

WORKSPACE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export ZEROPOINT_ROOT_PATH="${ZEROPOINT_ROOT_PATH:-$WORKSPACE_ROOT}"
export ZP_MODULE_STORAGE="${ZP_MODULE_STORAGE:-$WORKSPACE_ROOT/data/modules}"
export ZEROPOINT_AGENT_LOCAL=1

for _ in $(seq 1 60); do
    if [ -S /var/run/docker.sock ]; then
        sudo chgrp "$(id -gn)" /var/run/docker.sock
        sudo chmod 0660 /var/run/docker.sock
        if docker info >/dev/null 2>&1; then
            break
        fi
    fi
    sleep 0.5
done

if ! docker info >/dev/null 2>&1; then
    echo "Docker-in-Docker did not become available." >&2
    exit 1
fi

bash "$WORKSPACE_ROOT/scripts/ensure-defaults.sh"
result="$(zeropoint-agent dag resolve --mode "${ZEROPOINT_MODE:-mock}")"
echo "$result"
python -c 'import json,sys; result=json.load(sys.stdin); raise SystemExit(0 if result.get("status") == "succeeded" else 1)' <<<"$result"

echo "ZeroPoint development state is seeded and reconciled."
