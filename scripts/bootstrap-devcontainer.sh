#!/usr/bin/env bash
# Bootstrap the zeropoint-agent default DAG via the REST API.
#
# Usage:
#   1. Start the server (in another terminal or backgrounded):
#        zeropoint-agent serve &
#   2. Run this script.
#
# This script IS the bootstrap. Every step is one CLI call (one REST call).
# To learn what the bootstrap does, read this file.
#
# Override the server URL with ZEROPOINT_AGENT_URL (default
# http://127.0.0.1:2370).

set -euo pipefail

URL="${ZEROPOINT_AGENT_URL:-http://127.0.0.1:2370}"

# ---- wait for the server -------------------------------------------------

echo "waiting for $URL ..."
for _ in $(seq 1 30); do
  curl -sfS -o /dev/null "$URL/api/health" && break
  sleep 1
done
curl -sfS -o /dev/null "$URL/api/health" || {
  echo "zeropoint-agent server at $URL did not come up" >&2
  exit 1
}

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
zeropoint-agent dag resolve

echo "done."
