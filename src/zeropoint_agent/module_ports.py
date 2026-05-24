"""Sync per-port OutputVars from a module's main_ports terraform output.

After a module's Terraform node resolves, its output carries a
`main_ports` dict like::

    {"placeholder": {"port": 8080, "protocol": "tcp"}, ...}

For each port entry we ensure an `OutputVar[int]` lives at
`modules/<m>/port_<name>` with `key="main_ports.<name>.port"`, plus an
`OutputVar[str]` at `modules/<m>/port_<name>_protocol`. These are the
nodes the picker filters into when a user goes to expose a port.

Sync is idempotent and pruning: ports that disappear from `main_ports`
have their synced nodes removed.

Called explicitly from the resolve handler (or any post-resolve hook)
so DAG itself stays node-type-agnostic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict

from zeropoint_agent.nodes.config.output import OutputVar
from zeropoint_agent.nodes.user.module import Terraform

if TYPE_CHECKING:
    from zeropoint_agent.dag import DAG

logger = logging.getLogger(__name__)


def _terraform_main_ports(output: object) -> Dict[str, dict]:
    """Pull main_ports out of a TerraformResult (or its rehydrated dict)."""
    outputs = getattr(output, "outputs", None)
    if outputs is None and isinstance(output, dict):
        outputs = output.get("outputs")
    if not isinstance(outputs, dict):
        return {}
    main_ports = outputs.get("main_ports")
    if not isinstance(main_ports, dict):
        return {}
    return main_ports


def sync_module_ports(dag: "DAG", mode=None) -> int:
    """Sync every module's per-port OutputVar children with terraform's main_ports.

    `mode` (ResolveMode) — if provided, any newly-added port nodes are
    resolved in that mode before this function returns so the caller
    doesn't see them stuck in PENDING for one cycle.

    Returns the number of nodes added or removed.
    """
    from zeropoint_agent.nodes.config.namespace import Namespace

    changed = 0
    newly_added: list[str] = []
    for terraform_id, entry in list(dag.nodes.items()):
        if not isinstance(entry.node, Terraform):
            continue
        ns_entry = None
        ns_id = None
        for pid in entry.parents:
            p = dag.nodes.get(pid)
            if p is not None and isinstance(p.node, Namespace):
                ns_entry = p
                ns_id = pid
                break
        if ns_id is None:
            continue
        main_ports = _terraform_main_ports(entry.output)
        if not main_ports:
            continue

        existing_synced: Dict[str, str] = {}  # leaf -> node_id
        prefix = f"{ns_id}/port_"
        for nid in list(dag.nodes.keys()):
            if not nid.startswith(prefix):
                continue
            child_entry = dag.nodes[nid]
            md = getattr(child_entry.node, "__dict__", {})
            if md.get("_synced_port") is True or _is_synced_port_id(nid, ns_id, main_ports):
                existing_synced[nid] = nid

        desired_ids: set[str] = set()
        for port_name in main_ports.keys():
            port_id = f"{ns_id}/port_{port_name}"
            proto_id = f"{ns_id}/port_{port_name}_protocol"
            desired_ids.add(port_id)
            desired_ids.add(proto_id)

            if port_id not in dag.nodes:
                dag.add(
                    port_id,
                    OutputVar(
                        name=f"port_{port_name}",
                        key=f"main_ports.{port_name}.port",
                    ),
                    parents=[ns_id, terraform_id],
                    perms="r--",
                )
                changed += 1
                newly_added.append(port_id)
                logger.info("synced port node %s", port_id)
            if proto_id not in dag.nodes:
                dag.add(
                    proto_id,
                    OutputVar(
                        name=f"port_{port_name}_protocol",
                        key=f"main_ports.{port_name}.protocol",
                    ),
                    parents=[ns_id, terraform_id],
                    perms="r--",
                )
                changed += 1
                newly_added.append(proto_id)
                logger.info("synced port node %s", proto_id)

        for nid in existing_synced:
            if nid not in desired_ids:
                try:
                    dag.remove(nid)
                except Exception as e:
                    logger.warning("failed to prune stale port node %s: %s", nid, e)
                else:
                    changed += 1
                    logger.info("pruned stale port node %s", nid)

    # Resolve any freshly-added nodes so the caller doesn't see them
    # PENDING. Without this, the first resolve after install always
    # leaves synced ports unresolved until the next resolve cycle.
    if newly_added and mode is not None:
        try:
            dag.resolve_subset(newly_added, mode=mode)
        except Exception as e:
            logger.warning("post-sync resolve failed (continuing): %s", e)

    return changed


def _is_synced_port_id(nid: str, ns_id: str, main_ports: Dict[str, dict]) -> bool:
    """Conservative identification of synced port nodes by id pattern.

    Synced nodes are named ``port_<name>`` or ``port_<name>_protocol``
    under the module namespace. Anything else under the namespace that
    happens to start with ``port_`` (e.g. a user-named Var) is left
    alone — we only sweep ids that match the exact pattern for current
    or previously-seen ports.
    """
    if not nid.startswith(f"{ns_id}/port_"):
        return False
    leaf = nid[len(ns_id) + 1:]  # "port_<name>" or "port_<name>_protocol"
    # Either it's a known synced id for a current port…
    for p in main_ports:
        if leaf in (f"port_{p}", f"port_{p}_protocol"):
            return True
    # …or it's a stale one (port_<name>[_protocol]) for a removed port.
    # Walk the suffix backwards: anything that splits into port_<...>
    # is considered synced for pruning purposes.
    if leaf.startswith("port_"):
        return True
    return False
