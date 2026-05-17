"""CLI for zeropoint-agent.

Thin HTTP client over the REST API. Every subcommand makes a single
request, prints the JSON body verbatim, and exits with the HTTP status
code (0 for 200; otherwise the actual code, e.g. 404, 409, 403).

The one exception is `serve`, which starts the API server directly.

Environment:
    ZEROPOINT_AGENT_URL  base URL (default http://127.0.0.1:2370)
    ZEROPOINT_MODE       default resolve mode for `serve` (live/dry_run/mock)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

import click
import requests


# --- HTTP client ---------------------------------------------------------

def _base_url() -> str:
    return os.environ.get("ZEROPOINT_AGENT_URL", "http://127.0.0.1:2370").rstrip("/")


def _emit(resp: requests.Response) -> None:
    """Print the response body (JSON if possible, else raw) and exit.

    Exit code: 0 on 2xx, otherwise the HTTP status code (clamped to 1..255).
    """
    try:
        body = resp.json()
        click.echo(json.dumps(body, indent=2))
    except ValueError:
        click.echo(resp.text or "", nl=False)
        click.echo()

    if 200 <= resp.status_code < 300:
        sys.exit(0)
    # Unix exit codes are 0..255. HTTP codes 400..599 all fit; clamp anyway.
    code = resp.status_code
    if code < 1:
        code = 1
    if code > 255:
        code = code % 256 or 1
    sys.exit(code)


class _InProcessResponse:
    """Minimal duck-type of requests.Response for TestClient results."""

    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return json.loads(self.text) if self.text else None


def _request_in_process(method: str, path: str,
                        **kwargs) -> _InProcessResponse:
    """Dispatch a request through an in-process app (no server, no socket).

    Used as a fallback when no real server is reachable. Same handlers
    run; the only difference is there's no network. Holds the GraphStore
    lock for the duration of the call, so this fails (loudly) if a real
    server is already running against the same store.
    """
    from fastapi.testclient import TestClient
    from zeropoint_agent.server import app
    with TestClient(app) as client:
        resp = client.request(method, path, **kwargs)
        return _InProcessResponse(resp.status_code, resp.text)


def _request(method: str, path: str, **kwargs):
    """HTTP request to the running server, falling back to in-process.

    If a server is reachable at ZEROPOINT_AGENT_URL, the request goes
    over HTTP. Otherwise the CLI starts a transient in-process app
    (FastAPI TestClient), runs the request, and tears down — same code
    path as the real server, no network, no separate process.

    Force one mode via ZEROPOINT_AGENT_REMOTE=1 (HTTP only; never fall
    back) or ZEROPOINT_AGENT_LOCAL=1 (always in-process; never HTTP).
    """
    url = _base_url() + path
    force_local = os.environ.get("ZEROPOINT_AGENT_LOCAL")
    force_remote = os.environ.get("ZEROPOINT_AGENT_REMOTE")

    if force_local and not force_remote:
        return _request_in_process(method, path, **kwargs)

    try:
        return requests.request(method, url, **kwargs)
    except requests.ConnectionError as e:
        if force_remote:
            click.echo(json.dumps({
                "error": "connection_failed",
                "detail": str(e),
                "url": url,
            }, indent=2), err=True)
            sys.exit(7)
        # Auto-fallback: no server reachable, try in-process.
        try:
            return _request_in_process(method, path, **kwargs)
        except Exception as fb_err:
            click.echo(json.dumps({
                "error": "no_server_and_in_process_failed",
                "http_detail": str(e),
                "in_process_detail": str(fb_err),
                "url": url,
            }, indent=2), err=True)
            sys.exit(7)
    except requests.RequestException as e:
        click.echo(json.dumps({
            "error": "request_failed",
            "detail": str(e),
            "url": url,
        }, indent=2), err=True)
        sys.exit(8)


def _parse_kv(pairs: Iterable[str]) -> Dict[str, Any]:
    """Parse --foo k=v style options. Values are JSON-parsed if possible."""
    out: Dict[str, Any] = {}
    for kv in pairs:
        if "=" not in kv:
            raise click.UsageError(f"expected key=value, got {kv!r}")
        k, v = kv.split("=", 1)
        try:
            out[k] = json.loads(v)
        except (json.JSONDecodeError, ValueError):
            out[k] = v
    return out


# --- root group ----------------------------------------------------------

@click.group()
def cli():
    """zeropoint-agent — graph-based infrastructure management.

    All subcommands except `serve` are thin HTTP clients over the REST API.
    Set ZEROPOINT_AGENT_URL to point at a non-default server.
    """


# --- serve ---------------------------------------------------------------

@cli.command()
@click.option("--host", default="0.0.0.0", help="Bind address")
@click.option("--port", default=2370, type=int, help="Port")
def serve(host: str, port: int):
    """Start the API server (the one local command).

    The server owns the persistent DAG; CLI subcommands hit its REST API.
    """
    import uvicorn
    from zeropoint_agent.server import app, setup_logging
    logger = setup_logging()
    logger.info("Zeropoint Agent starting...")
    uvicorn.run(app, host=host, port=port, log_config=None)


# --- node ----------------------------------------------------------------

@cli.group()
def node():
    """Inspect and mutate individual nodes."""


@node.command("list")
@click.argument("pattern", required=False)
def node_list(pattern: Optional[str]):
    """List nodes. With no pattern, returns the whole graph."""
    if pattern:
        from urllib.parse import quote
        _emit(_request("GET", f"/api/dag/query/{quote(pattern, safe='/*')}"))
    else:
        _emit(_request("GET", "/api/dag"))


@node.command("get")
@click.argument("node_id")
def node_get(node_id: str):
    """Fetch a single node by id."""
    from urllib.parse import quote
    _emit(_request("GET", f"/api/dag/nodes/{quote(node_id, safe='/')}"))


@node.command("add")
@click.argument("node_type")
@click.argument("node_id")
@click.option("--parent", "-p", "parents", multiple=True,
              help="Parent node id (repeatable).")
@click.option("--config", "-c", "config_kvs", multiple=True,
              help="Config key=value (JSON-parsed; repeatable).")
@click.option("--perms", default="***",
              help='Permission string, 3 chars from r/w/d/*/-  (default "***").')
def node_add(node_type: str, node_id: str,
             parents: Tuple[str, ...], config_kvs: Tuple[str, ...],
             perms: str):
    """Create a new node. Errors 409 if the id already exists."""
    payload = {
        "id": node_id,
        "type": node_type,
        "config": _parse_kv(config_kvs),
        "parents": list(parents),
        "perms": perms,
    }
    _emit(_request("POST", "/api/dag/nodes", json=payload))


@node.command("ensure")
@click.argument("node_type")
@click.argument("node_id")
@click.option("--parent", "-p", "parents", multiple=True,
              help="Parent node id (repeatable).")
@click.option("--config", "-c", "config_kvs", multiple=True,
              help="Config key=value (JSON-parsed; repeatable).")
@click.option("--perms", default="***",
              help='Permission string, 3 chars from r/w/d/*/-  (default "***").')
def node_ensure(node_type: str, node_id: str,
                parents: Tuple[str, ...], config_kvs: Tuple[str, ...],
                perms: str):
    """Create the node if absent; do nothing if it already exists.

    Same shape as `add`, but tolerates 409 (already-exists). Use this
    when the script's intent is "I want this to exist" rather than
    "I am creating a new thing."
    """
    payload = {
        "id": node_id,
        "type": node_type,
        "config": _parse_kv(config_kvs),
        "parents": list(parents),
        "perms": perms,
    }
    resp = _request("POST", "/api/dag/nodes", json=payload)
    if resp.status_code == 409:
        click.echo(json.dumps({"ok": True, "node_id": node_id,
                               "existed": True}, indent=2))
        sys.exit(0)
    _emit(resp)


@node.command("update")
@click.argument("node_id")
@click.option("--config", "-c", "config_kvs", multiple=True,
              help="Config key=value to set (repeatable).")
def node_update(node_id: str, config_kvs: Tuple[str, ...]):
    """Edit a node's config. Resets it (and descendants) to PENDING."""
    from urllib.parse import quote
    payload = _parse_kv(config_kvs)
    _emit(_request("PUT", f"/api/dag/{quote(node_id, safe='/')}", json=payload))


@node.command("remove")
@click.argument("pattern")
def node_remove(pattern: str):
    """Remove a node (or pattern of nodes). Cascading."""
    from urllib.parse import quote
    _emit(_request("DELETE", f"/api/dag/{quote(pattern, safe='/*')}"))


@node.command("verify")
@click.argument("pattern", required=False, default="**")
def node_verify(pattern: str):
    """Check node health (status snapshot)."""
    from urllib.parse import quote
    _emit(_request("GET", f"/api/dag/health/{quote(pattern, safe='/*')}"))


# --- dag -----------------------------------------------------------------

@cli.group()
def dag():
    """Whole-graph operations."""


@dag.command("status")
def dag_status():
    """Return overall graph health + status summary."""
    _emit(_request("GET", "/api/health"))


@dag.command("resolve")
@click.argument("pattern", required=False)
@click.option("--mode", "mode", default=None,
              type=click.Choice(["live", "dry_run", "mock"], case_sensitive=False),
              help="Override server's default resolve mode.")
def dag_resolve(pattern: Optional[str], mode: Optional[str]):
    """Resolve the whole graph (or a pattern's subgraph)."""
    body: Dict[str, Any] = {}
    if mode:
        body["mode"] = mode
    if pattern:
        from urllib.parse import quote
        _emit(_request("POST",
                       f"/api/dag/resolve/{quote(pattern, safe='/*')}",
                       json=body))
    else:
        _emit(_request("POST", "/api/dag/resolve", json=body))


# --- module --------------------------------------------------------------

@cli.group()
def module():
    """Install / remove modules (terraform-managed)."""


@module.command("add")
@click.argument("module_id")
@click.argument("source")
@click.option("--var", "-v", "overrides", multiple=True,
              help="Override a user var (key=value; repeatable).")
@click.option("--parent-namespace", default="modules",
              help='Namespace to install under (default "modules").')
@click.option("--resolve/--no-resolve", default=False,
              help="Resolve the module immediately after adding.")
def module_add(module_id: str, source: str,
               overrides: Tuple[str, ...], parent_namespace: str,
               resolve: bool):
    """Install a terraform module. Errors 409 if it already exists."""
    overrides_dict = {}
    for kv in overrides:
        if "=" not in kv:
            raise click.UsageError(f"--var expected key=value, got {kv!r}")
        k, v = kv.split("=", 1)
        overrides_dict[k] = v
    payload = {
        "module_id": module_id,
        "source": source,
        "overrides": overrides_dict,
        "resolve": resolve,
        "parent_namespace": parent_namespace,
    }
    _emit(_request("POST", "/api/modules", json=payload))


@module.command("remove")
@click.argument("module_id")
@click.option("--parent-namespace", default="modules",
              help='Namespace the module lives under (default "modules").')
def module_remove(module_id: str, parent_namespace: str):
    """Remove a module (cascading delete of its namespace subtree)."""
    from urllib.parse import quote
    target = f"{parent_namespace}/{module_id}"
    _emit(_request("DELETE", f"/api/dag/{quote(target, safe='/*')}"))


# --- detect --------------------------------------------------------------

@cli.command()
@click.argument("kind")
def detect(kind: str):
    """Probe a host-derived value (arch, gpu-vendor)."""
    _emit(_request("GET", f"/api/detect/{kind}"))


def _safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text


if __name__ == "__main__":
    cli()
