"""CLI for zeropoint-agent.

Subcommands:
  serve           Start the API server
  status          Show graph status
  verify-node     Verify a single node (called by systemd)
  resolve-node    Resolve a single node (called by systemd)
  add             Add a node to the graph
  check           Run bootstrap + resolve (what startup does)
"""

import os
import sys
import json
import logging
from pathlib import Path

import click

from zeropoint_agent.inode import ResolveMode, NodeStatus
from zeropoint_agent.dag import DAG
from zeropoint_agent.graph_store import GraphStore
from zeropoint_agent.bootstrap import bootstrap


def _get_store_and_dag():
    """Create store + DAG from env vars."""
    store_path = os.environ.get("ZEROPOINT_ROOT_PATH", ".")
    data_dir = Path(store_path) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(data_dir / "graph.db")
    store = GraphStore(db_path)
    dag = DAG(store=store)
    return store, dag


def _get_mode() -> ResolveMode:
    mode_str = os.environ.get("ZEROPOINT_MODE", "live")
    return {
        "live": ResolveMode.LIVE,
        "dry_run": ResolveMode.DRY_RUN,
        "mock": ResolveMode.MOCK,
    }.get(mode_str, ResolveMode.LIVE)


STATUS_ICONS = {
    "success": "●",
    "success_skip": "○",
    "pending": "○",
    "pending_reboot": "●",
    "running": "●",
    "error": "●",
    "blocked": "●",
    "skipped": "○",
}

STATUS_COLORS = {
    "success": "green",
    "success_skip": "green",
    "pending": "yellow",
    "pending_reboot": "yellow",
    "running": "blue",
    "error": "red",
    "blocked": "white",
    "skipped": "white",
}


@click.group()
def cli():
    """zeropoint-agent — graph-based infrastructure management."""
    pass


@cli.command()
@click.option("--host", default="0.0.0.0", help="Bind address")
@click.option("--port", default=2370, help="Port")
def serve(host, port):
    """Start the API server."""
    import uvicorn
    from zeropoint_agent.server import app
    uvicorn.run(app, host=host, port=port, log_config=None)


@cli.command()
def status():
    """Show graph status."""
    store, dag = _get_store_and_dag()
    mode = _get_mode()

    # Bootstrap to load nodes
    bootstrap(dag, mode)

    if not dag.nodes:
        click.echo("No nodes in graph.")
        return

    for nid, entry in dag.nodes.items():
        s = entry.status.value
        icon = STATUS_ICONS.get(s, "?")
        color = STATUS_COLORS.get(s, "white")
        node_type = type(entry.node).__name__
        click.echo(f"  {click.style(icon, fg=color)} {nid:25s} {s:15s} {node_type}")


@cli.command()
def check():
    """Run bootstrap + resolve (same as server startup)."""
    store, dag = _get_store_and_dag()
    mode = _get_mode()

    click.echo("Running bootstrap...")
    actions = bootstrap(dag, mode)
    for nid, action in actions.items():
        click.echo(f"  {nid}: {action}")

    click.echo(f"\nResolving (mode={mode.value})...")
    results = dag.resolve(mode=mode)

    summary = {}
    for nid, s in results.items():
        icon = STATUS_ICONS.get(s.value, "?")
        color = STATUS_COLORS.get(s.value, "white")
        click.echo(f"  {click.style(icon, fg=color)} {nid:25s} {s.value}")
        summary[s.value] = summary.get(s.value, 0) + 1

    click.echo(f"\n{' · '.join(f'{c} {s}' for s, c in summary.items())}")


@cli.command("verify-node")
@click.argument("node_id")
def verify_node(node_id):
    """Verify a single node (called by systemd on boot)."""
    store, dag = _get_store_and_dag()
    mode = _get_mode()
    bootstrap(dag, mode)

    if node_id not in dag.nodes:
        click.echo(f"Node not found: {node_id}", err=True)
        sys.exit(1)

    entry = dag.get(node_id)
    result = entry.node.verify(mode)

    icon = STATUS_ICONS.get(result.status.value, "?")
    color = STATUS_COLORS.get(result.status.value, "white")
    click.echo(f"{click.style(icon, fg=color)} {node_id}: {result.status.value}")

    if result.error:
        click.echo(f"  error: {result.error}", err=True)

    if result.output:
        click.echo(f"  output: {result.output}")

    # Exit code: 0 for success/success_skip, 1 for anything else
    if result.status in (NodeStatus.SUCCESS, NodeStatus.SUCCESS_SKIP):
        sys.exit(0)
    else:
        sys.exit(1)


@cli.command("resolve-node")
@click.argument("node_id")
def resolve_node(node_id):
    """Resolve a single node (called by systemd for deferred work)."""
    store, dag = _get_store_and_dag()
    mode = _get_mode()
    bootstrap(dag, mode)

    if node_id not in dag.nodes:
        click.echo(f"Node not found: {node_id}", err=True)
        sys.exit(1)

    # Resolve just this node
    results = dag.resolve_subset([node_id], mode=mode)
    s = results.get(node_id, NodeStatus.ERROR)

    icon = STATUS_ICONS.get(s.value, "?")
    color = STATUS_COLORS.get(s.value, "white")
    click.echo(f"{click.style(icon, fg=color)} {node_id}: {s.value}")

    entry = dag.get(node_id)
    if entry.error:
        click.echo(f"  error: {entry.error}", err=True)
    if entry.output:
        click.echo(f"  output: {entry.output}")

    if s in (NodeStatus.SUCCESS, NodeStatus.SUCCESS_SKIP):
        sys.exit(0)
    else:
        sys.exit(1)


@cli.command()
@click.argument("node_type")
@click.argument("node_id")
@click.option("--config", "-c", multiple=True, help="Config key=value pairs")
@click.option("--parent", "-p", multiple=True, help="Parent node IDs")
def add(node_type, node_id, config, parent):
    """Add a node to the graph."""
    from zeropoint_agent.handlers import NODE_REGISTRY

    if node_type not in NODE_REGISTRY:
        click.echo(f"Unknown node type: {node_type}", err=True)
        click.echo(f"Available: {', '.join(NODE_REGISTRY.keys())}")
        sys.exit(1)

    # Parse config
    cfg = {}
    for kv in config:
        if "=" not in kv:
            click.echo(f"Invalid config: {kv} (expected key=value)", err=True)
            sys.exit(1)
        k, v = kv.split("=", 1)
        # Try to parse as JSON for non-string values
        try:
            cfg[k] = json.loads(v)
        except (json.JSONDecodeError, ValueError):
            cfg[k] = v

    store, dag = _get_store_and_dag()
    mode = _get_mode()
    bootstrap(dag, mode)

    cls = NODE_REGISTRY[node_type]
    try:
        node = cls(**cfg)
        dag.add(node_id, node, parents=list(parent))
        click.echo(f"Added {node_id} ({node_type})")
    except Exception as e:
        click.echo(f"Failed: {e}", err=True)
        sys.exit(1)


@cli.command("module-add")
@click.argument("module_id")
@click.argument("source")
@click.option("--var", "-v", multiple=True,
              help="Override a default for an auto-created VarNode (key=value).")
@click.option("--resolve/--no-resolve", default=False,
              help="Resolve the new module immediately after adding.")
def module_add(module_id, source, var, resolve):
    """Add a Terraform module to the graph.

    SOURCE must be a git URL with @<40-char-commit-sha>.
    """
    from zeropoint_agent.module_installer import add_module

    overrides = {}
    for kv in var:
        if "=" not in kv:
            click.echo(f"Invalid --var: {kv} (expected key=value)", err=True)
            sys.exit(1)
        k, v = kv.split("=", 1)
        overrides[k] = v

    store, dag = _get_store_and_dag()
    mode = _get_mode()
    bootstrap(dag, mode)

    try:
        result = add_module(dag, module_id, source, overrides=overrides)
    except Exception as e:
        click.echo(f"Failed: {e}", err=True)
        sys.exit(1)

    click.echo(f"Added module {result.module_id} at {result.namespace_id}")
    click.echo(f"  terraform:    {result.terraform_id}")
    if result.wired_existing_nodes:
        click.echo(f"  wired:        {', '.join(sorted(set(result.wired_existing_nodes)))}")
    if result.created_var_nodes:
        click.echo(f"  created:      {', '.join(result.created_var_nodes)}")

    if resolve:
        click.echo(f"\nResolving {result.namespace_id} (mode={mode.value})...")
        targets = list(dict.fromkeys(
            result.created_var_nodes
            + list(set(result.wired_existing_nodes))
        ))
        results = dag.resolve_subset(targets, mode=mode)
        for nid in targets:
            s = results.get(nid)
            if s is None:
                continue
            icon = STATUS_ICONS.get(s.value, "?")
            color = STATUS_COLORS.get(s.value, "white")
            click.echo(f"  {click.style(icon, fg=color)} {nid:50s} {s.value}")
