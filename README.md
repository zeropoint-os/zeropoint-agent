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
- **Exposure nodes** publish one machine-qualified local hostname per HTTP
  service through the shared Envoy ingress and mDNS.
- **Tailscale integration nodes** optionally layer named tailnet Services over
  those local Exposures without changing application modules or creating a
  Tailscale device per application.

SQLite is the authoritative store. The runtime does not maintain a second
application-state database alongside the graph.

## Requirements

- Python 3.11+
- Terraform
- Docker
- Envoy for exposed services
- Tailscale is optional; ZeroPoint runs official Tailscale v1.104.7 pinned to
  the multi-arch OCI index
  `tailscale/tailscale@sha256:72c1e19e0aedca96b8533bbb0b4368564410ff20b17e53b804eb6c1f04a31be4`
  when tailnet access is enabled
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

For a machine named `zeropoint-sahal-velas`, the service is available at
`http://jellyfin-zeropoint-sahal-velas.local/`.

### Optional tailnet access

Publish an existing local Exposure through ordinary Tailscale Serve. The first
publication atomically creates the singleton `system/tailscale` root, allocates
a durable HTTPS port for the child, and starts one GraphRun for enrollment
followed by publication:

```bash
curl -X POST http://zeropoint-node.local:2370/api/tailscale/expose \
  -H 'Content-Type: application/json' \
  -d '{
    "exposure_id": "modules/jellyfin/main_ports/web/exposure_jellyfin"
  }'
```

While human action is needed, GraphRun nodes and Promises return the
nonterminal status `requires_interaction` with a typed `interaction` object.
REST clients can render the standard `url_qr`, `device_code`, or `open_url`
variants and continue polling the same run. Tailscale enrollment uses `url_qr`.
Final node `output` appears only after the Job completes, and interaction links/codes are
cleared on completion, cancellation, or supersession. Enrollment permits up to
32 interrupted Job attempts, enough for repeated ordinary reboots while still
bounding a permanently broken operation.

ZeroPoint never accepts or persists Tailscale auth keys. Each Tailnet Exposure
persists one unique HTTPS port from the bounded `10000-10999` range and is
available as `https://<machine>.<tailnet>.ts.net:<port>/`. Named Tailscale
Services, tags, approvals, TailVIP, HA, and Funnel are not used.

`system/tailscale` is graph-parented to `system/envoy` and consumes Envoy's
resolved container/runtime-generation contract. If Envoy restarts into a new
network namespace—even with the same container ID—the Tailscale runtime is
recreated against that generation while its bind-mounted identity state
survives. Tailscale Serve then shares Envoy's namespace and reaches a dedicated
`127.0.0.1` listener. Public LAN routes remove all Tailscale identity headers;
only the loopback route preserves them, so LAN callers cannot spoof tailnet
identity. The private route rewrites Host/authority to the target Exposure's
canonical local hostname before proxying. Applications continue serving at `/`
and remain unaware of both local and remote addressing.

Inspect the graph-derived accessibility matrix:

```bash
curl http://zeropoint-node.local:2370/api/tailscale/matrix
```

Removing a Tailnet Exposure preserves local access. Deleting `/api/tailscale`
removes remote children before the root runtime while retaining the persistent
device state directory for a later re-enable.

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
- `/api/tailscale`
- `/api/tailscale/matrix`
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

