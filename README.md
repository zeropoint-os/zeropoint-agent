# ZeroPoint Agent

ZeroPoint Agent is the graph-based control plane for ZeroPoint OS. It models
hardware, storage, configuration, modules, services, and exposures as one typed
dependency graph backed by SQLite.

The agent is backend-only. Clients such as the CLI, MCP servers, UIs, and local
language models inspect and mutate the same REST API and durable graph.

## Core model

- **GraphStore** persists desired state, observed state, ownership, and graph
  revisions in SQLite.
- **Promises and Jobs** make external operations durable, cancellable,
  restart-safe, and observable.
- **GraphRuns** verify, resolve, or remove dependency subgraphs until they reach
  a fixed point.
- **Hardware nodes** discover disks, partitions, filesystems, mounts, GPUs,
  network interfaces, USB, PCI, and machine inventory.
- **Module nodes** inspect immutable, commit-pinned Terraform repositories and
  produce their complete input/output/service subgraphs.
- **Var links** compose modules without coupling them. Storage and library path
  resources can feed any compatible module input.
- **Exposure nodes** publish one dedicated hostname per HTTP service through
  Envoy and mDNS.

SQLite is the authoritative store. The runtime does not maintain a second
application-state database alongside the graph.

## Requirements

- Python 3.11+
- Terraform
- Docker
- Envoy for exposed services
- Linux utilities used by live hardware operations (`lsblk`, `findmnt`,
  `sfdisk`, `wipefs`, filesystem tools, and `rsync`)

Development commands must run in the repository dev container.

## Run locally

```bash
zeropoint-agent serve --host 0.0.0.0 --port 2370
```

The default mode is configured with `ZEROPOINT_MODE`:

```bash
ZEROPOINT_MODE=live zeropoint-agent serve
```

Common environment variables include:

| Variable | Purpose |
|---|---|
| `ZEROPOINT_ROOT_PATH` | Agent state root |
| `ZEROPOINT_MODE` | `mock`, `dry_run`, or `live` |
| `ZEROPOINT_XDS_PORT` | Envoy xDS listener port |
| `ZEROPOINT_MDNS_ADVERTISE` | Enable or disable mDNS advertising |

## API examples

Install an immutable module and start its GraphRun:

```bash
curl -X POST http://zeropoint-node.local:2370/api/modules \
  -H 'Content-Type: application/json' \
  -d '{
    "module_id": "jellyfin",
    "source": "https://github.com/zeropoint-os/jellyfin.git@<40-character-commit-sha>",
    "overrides": {
      "movies_path": "/storage/media/movies",
      "tv_path": "/storage/media/tv"
    },
    "resolve": true
  }'
```

Link a module input to a canonical graph value:

```bash
curl -X PUT \
  http://zeropoint-node.local:2370/api/links/modules/jellyfin/movies_path \
  -H 'Content-Type: application/json' \
  -d '{"target":"libraries/movies_path"}'
```

Expose an HTTP service at its own hostname:

```bash
curl -X POST http://zeropoint-node.local:2370/api/expose \
  -H 'Content-Type: application/json' \
  -d '{
    "service_id": "modules/jellyfin/main_ports/web",
    "name": "jellyfin"
  }'
```

The service is then available at `http://jellyfin.local/`.

Canonical resources include:

- `/api/graph`
- `/api/runs`
- `/api/jobs`
- `/api/promises`
- `/api/events`
- `/api/events/stream`
- `/api/modules`
- `/api/links`
- `/api/expose`
- `/api/meta`

## Development

```bash
python -m pytest -q
python -m compileall -q src/zeropoint_agent
git diff --check
```

The test suite covers persistence, Promise and Job recovery, produced
subgraphs, module source upgrades, hardware operations, storage migration,
exposure routing, mDNS behavior, REST resources, and restart semantics.

## Release

The package version is declared in `pyproject.toml`. Create a GitHub release
whose tag matches that version, for example `v2.0.0`. The release workflow
validates the runtime, builds a universal wheel and source distribution, and
publishes the generated OpenAPI documents.

