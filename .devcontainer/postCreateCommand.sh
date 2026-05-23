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

ZP_MODULE_STORAGE="${ZP_MODULE_STORAGE:-/var/lib/zeropoint}"
ZP_MARKER_DIR="${ZP_MARKER_DIR:-/etc/zeropoint}"

mkdir -p "$ZP_MODULE_STORAGE" "$ZP_MARKER_DIR"

# ---- the bootstrap (each line = one REST call) ---------------------------

echo "seeding default DAG (arch=$ARCH gpu=${GPU:-<none>}) ..."

zeropoint-agent node ensure namespace settings \
    -c name=settings --perms rw*

zeropoint-agent node ensure namespace modules \
    -c name=modules  --perms rw*

# zp_module_storage is the one settings entry the user can sensibly edit
# — pointing it at a different on-disk location is a legitimate config
# change. TerraformNode detects the change on next resolve and relocates
# the module's working dir (state + .terraform/) to the new path. The
# others are detected host-properties; making them editable would be a
# footgun (e.g. lying to terraform about arch).
zeropoint-agent node ensure var settings/zp_module_storage \
    -p settings \
    -c name=zp_module_storage -c "value=\"${ZP_MODULE_STORAGE}\"" \
    --perms rw-

zeropoint-agent node ensure var settings/zp_arch \
    -p settings \
    -c name=zp_arch -c "value=\"${ARCH}\"" \
    --perms r--

zeropoint-agent node ensure var settings/zp_gpu_vendor \
    -p settings \
    -c name=zp_gpu_vendor -c "value=\"${GPU}\"" \
    --perms r--

zeropoint-agent node ensure var settings/zp_marker_dir \
    -p settings \
    -c name=zp_marker_dir -c "value=\"${ZP_MARKER_DIR}\"" \
    --perms r--

# ---- resolve -------------------------------------------------------------

echo "resolving ..."
zeropoint-agent dag resolve > /dev/null

# ---- install the echo module ---------------------------------------------
# Verifies the full happy path: bootstrap -> module add (git source) ->
# clone @ pinned SHA -> terraform apply -> output VarNodes populated.
# Idempotent via 'module add' returning 409 if echo is already installed.

ECHO_SOURCE="https://github.com/zeropoint-os/echo.git@5504795d3cf6f53fae8a12a6d860d44beb5e21f5"

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
