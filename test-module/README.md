# zeropoint-agent test module

The smallest possible terraform module that satisfies the zeropoint
install contract. Installed automatically by the devcontainer
postCreate so a fresh devcontainer comes up with a working module.

Spins up an `alpine:3.19` container that echoes a greeting and sleeps.
Validates the full pipeline: input VarNodes (system + user), terraform
apply, output VarNodes wiring.

## Install

Local (default during devcontainer bootstrap):

    zeropoint-agent module add zp-test /workspaces/zeropoint-agent/test-module --resolve

## Outputs

- `main` — the alpine container resource
- `main_ports` — placeholder (required by contract)
- `greeting_echoed` — passes the `greeting` input through to verify
  var → output flow
- `container_name` — the container's actual name as resolved by docker
