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

# ---- probe host values from the agent (single source of truth) -----------

ARCH="$(zeropoint-agent detect arch       | python3 -c 'import sys,json;print(json.load(sys.stdin).get("value","amd64"))')"
GPU="$( zeropoint-agent detect gpu-vendor | python3 -c 'import sys,json;print(json.load(sys.stdin).get("value",""))')"

ZP_MODULE_STORAGE="${ZP_MODULE_STORAGE:-/var/lib/zeropoint}"
ZP_MARKER_DIR="${ZP_MARKER_DIR:-/etc/zeropoint}"

mkdir -p "$ZP_MODULE_STORAGE" "$ZP_MARKER_DIR"

# ---- the bootstrap (each line = one REST call) ---------------------------

echo "seeding default DAG (arch=$ARCH gpu=${GPU:-<none>}) ..."

zeropoint-agent node ensure namespace global-settings \
    -c name=global-settings --perms rw*

zeropoint-agent node ensure namespace modules \
    -c name=modules         --perms rw*

zeropoint-agent node ensure var global-settings/zp_module_storage \
    -p global-settings \
    -c name=zp_module_storage -c "value=\"${ZP_MODULE_STORAGE}\"" \
    --perms r--

zeropoint-agent node ensure var global-settings/zp_arch \
    -p global-settings \
    -c name=zp_arch -c "value=\"${ARCH}\"" \
    --perms r--

zeropoint-agent node ensure var global-settings/zp_gpu_vendor \
    -p global-settings \
    -c name=zp_gpu_vendor -c "value=\"${GPU}\"" \
    --perms r--

zeropoint-agent node ensure var global-settings/marker_dir \
    -p global-settings \
    -c name=zp_marker_dir -c "value=\"${ZP_MARKER_DIR}\"" \
    --perms r--

# ---- resolve -------------------------------------------------------------

echo "resolving ..."
zeropoint-agent dag resolve > /dev/null

# ---- install the local test module ---------------------------------------
# Confirms the full happy path: bootstrap -> module add (local source) ->
# terraform apply -> output VarNodes populated. Idempotent via 'module add'
# returning 409 if zp-test is already installed.

TEST_MODULE_DIR="$(cd "$(dirname "$0")/.." && pwd)/test-module"

if [[ -d "$TEST_MODULE_DIR" ]]; then
  echo "installing zp-test from $TEST_MODULE_DIR ..."
  set +e
  zeropoint-agent module add zp-test "$TEST_MODULE_DIR" --resolve > /tmp/zp-test-install.log 2>&1
  rc=$?
  set -e
  case $rc in
    0)   echo "  installed.";;
    409) echo "  already installed; skipping.";;
    *)   echo "  WARNING: install failed (exit $rc); see /tmp/zp-test-install.log" >&2;;
  esac
fi

echo "done."
