#!/usr/bin/env bash
# Devcontainer postCreate: seed the zeropoint-agent default DAG.
#
# Invoked once when the devcontainer is created (see ../.devcontainer/
# devcontainer.json -> postCreateCommand). Re-running it is safe — every
# step uses `node ensure`, which tolerates "already exists".
#
# Works whether or not a server is running:
#   - If `zeropoint-agent serve` is up, every CLI call goes over HTTP.
#   - Otherwise, each CLI call spins up an in-process app, runs the
#     request, and tears down. Same handlers, no network.
#
# This script IS the bootstrap. Every step is one CLI call (one REST call).
# To learn what bootstrap does, read this file.
#
# Override the server URL with ZEROPOINT_AGENT_URL (default
# http://127.0.0.1:2370). Force in-process always with
# ZEROPOINT_AGENT_LOCAL=1; force HTTP only with ZEROPOINT_AGENT_REMOTE=1.

set -euo pipefail

# ---- wipe stale graph state so the bootstrap is canonical ----------------
# The graph store persists in data/. If you change a node's id (e.g. a
# rename like global-settings -> settings) old entries linger. Wiping
# here keeps postCreate as the single source of truth for what the DAG
# looks like out of the box.

WORKSPACE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -d "$WORKSPACE_ROOT/data" ]]; then
    echo "wiping existing graph state in $WORKSPACE_ROOT/data ..."
    rm -rf "$WORKSPACE_ROOT/data"
fi

# ---- probe host values from the agent (single source of truth) -----------

ARCH="$(zeropoint-agent detect arch       | python3 -c 'import sys,json;print(json.load(sys.stdin).get("value","amd64"))')"
GPU="$( zeropoint-agent detect gpu-vendor | python3 -c 'import sys,json;print(json.load(sys.stdin).get("value",""))')"

# Default storage root for per-module data dirs. Each module's
# zp_storage_path is initialized to <ZP_MODULE_STORAGE>/<module_id>/
# by the installer; the user can then edit it independently.
export ZP_MODULE_STORAGE="${ZP_MODULE_STORAGE:-/var/lib/zeropoint}"
mkdir -p "$ZP_MODULE_STORAGE"

# ---- the bootstrap (each line = one REST call) ---------------------------

echo "seeding default DAG (arch=$ARCH gpu=${GPU:-<none>}) ..."

zeropoint-agent node ensure namespace settings \
    -c name=settings --perms rw*

zeropoint-agent node ensure namespace modules \
    -c name=modules  --perms rw*

# Settings is now minimal: only the genuinely-global, system-detected
# values live here. Per-module storage location is a per-module
# concern (modules/<id>/zp_storage_path) — each instance can live in
# a different place, including a different filesystem. Per-module
# terraform state lives at modules/<id>/zp_module_path. Both are
# created automatically by the module installer.
zeropoint-agent node ensure var settings/zp_arch \
    -p settings \
    -c name=zp_arch -c "value=\"${ARCH}\"" \
    --perms r--

zeropoint-agent node ensure var settings/zp_gpu_vendor \
    -p settings \
    -c name=zp_gpu_vendor -c "value=\"${GPU}\"" \
    --perms r--

# ---- resolve -------------------------------------------------------------

echo "resolving ..."
zeropoint-agent dag resolve > /dev/null

# ---- install the echo module ---------------------------------------------
# Verifies the full happy path: bootstrap -> module add (git source) ->
# clone @ pinned SHA -> terraform apply -> output VarNodes populated.
# Idempotent via 'module add' returning 409 if echo is already installed.

ECHO_SOURCE="https://github.com/zeropoint-os/echo.git@16f0b34cccda8a200bf33c1206226e42423a8a28"

echo "installing echo from $ECHO_SOURCE ..."
set +e
zeropoint-agent module add echo "$ECHO_SOURCE" --resolve > /tmp/echo-install.log 2>&1
rc=$?
set -e
case $rc in
  0)   echo "  installed.";;
  409) echo "  already installed; skipping.";;
  *)   echo "  WARNING: install failed (exit $rc); see /tmp/echo-install.log" >&2;;
esac

echo "done."
