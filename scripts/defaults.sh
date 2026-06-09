#!/usr/bin/env bash
# Seed the canonical default zeropoint graph.
#
# This script IS the bootstrap. Every line is one CLI call (one REST
# call). To learn what the default graph looks like, read this file.
#
# Works whether or not a server is running:
#   - If `zeropoint-agent serve` is up, every CLI call goes over HTTP.
#   - Otherwise, each CLI call spins up an in-process app, runs the
#     request, and tears down. Same handlers, no network.
#
# Override the server URL with ZEROPOINT_AGENT_URL (default
# http://127.0.0.1:2370). Force in-process always with
# ZEROPOINT_AGENT_LOCAL=1; force HTTP only with ZEROPOINT_AGENT_REMOTE=1.
#
# Invoked from:
#   - .devcontainer/postCreateCommand.sh (one-time devcontainer setup)
#   - scripts/reset.sh                   (wipe + reseed for repeated testing)

set -euo pipefail

# ---- probe host values from the agent (single source of truth) -----------

ARCH="$(zeropoint-agent detect arch       | python3 -c 'import sys,json;print(json.load(sys.stdin).get("value","amd64"))')"
GPU="$( zeropoint-agent detect gpu-vendor | python3 -c 'import sys,json;print(json.load(sys.stdin).get("value",""))')"

# Default storage root for per-module data dirs. Each module's
# zp_storage_dir is initialized to <ZP_MODULE_STORAGE>/<module_id>/
# by the installer; the user can then edit it independently.
export ZP_MODULE_STORAGE="${ZP_MODULE_STORAGE:-/var/lib/zeropoint}"
mkdir -p "$ZP_MODULE_STORAGE"

# ---- the bootstrap (each line = one REST call) ---------------------------

echo "seeding default DAG (arch=$ARCH gpu=${GPU:-<none>}) ..."

zeropoint-agent node ensure namespace settings \
    -c name=settings --perms rw*

zeropoint-agent node ensure namespace modules \
    -c name=modules  --perms rw*

# System namespace — agent-managed plumbing (envoy, future health
# nodes, etc). Writable so the bootstrap can place children, but
# the children are r-- so users can't tamper with them.
zeropoint-agent node ensure namespace system \
    -c name=system --perms rw-

# Docker daemon healthcheck. Root-of-the-system-subtree: every other
# system node that needs docker depends on this one (transitively),
# so its resolve runs first.
zeropoint-agent node ensure docker system/docker \
    -p system \
    --perms r-- --tags system

# Shared bridge network for cross-container DNS. Envoy and any
# module container that gets exposed live on this network so
# Envoy's STRICT_DNS clusters can resolve <module>-main.
zeropoint-agent node ensure docker_network system/zeropoint_network \
    -p system -p system/docker \
    -c name=zeropoint-network -c driver=bridge \
    --perms r-- --tags system

zeropoint-agent node ensure envoy system/envoy \
    -p system -p system/docker -p system/zeropoint_network \
    -c xds_port=18000 -c http_port=80 -c https_port=443 \
    --perms r-- --tags system

# Settings is minimal: only the genuinely-global, system-detected
# values live here. Per-module storage location is a per-module
# concern (modules/<id>/zp_storage_dir) — each instance can live in
# a different place, including a different filesystem. Per-module
# terraform state lives at modules/<id>/zp_module_dir. Both are
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
# clone @ pinned SHA -> terraform apply -> output Vars populated.
# Idempotent via 'module add' returning 409 if echo is already installed.

ECHO_SOURCE="https://github.com/zeropoint-os/echo.git@4c8b39644e2745de0c4530b5fd35a7c6d19b1ce9"

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
