#!/usr/bin/env bash
# Devcontainer postCreate: one-time setup when the devcontainer is created.
#
# Invoked once via .devcontainer/devcontainer.json -> postCreateCommand.
# Just runs scripts/reset.sh — "fresh devcontainer" = "freshly reset
# zeropoint". reset.sh handles everything: tearing down stale docker
# state from rebuilds, wiping data/, and reseeding the default graph
# via scripts/defaults.sh.

set -euo pipefail

WORKSPACE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
bash "$WORKSPACE_ROOT/scripts/reset.sh"
