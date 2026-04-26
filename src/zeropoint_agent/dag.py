"""DAG — the compiler and runtime for the graph-based language.

dag.add() is the compiler — validates I/O types at edge creation.
dag.resolve() is the runtime — topo-walks and propagates values.

The executor is a generic NodeResult processor — it doesn't know
what any node does, just reads the result and does the plumbing.
"""

import logging
from dataclasses import dataclass, asdict
from pathlib import Path as FilePath
from typing import Any, Dict, List, Optional, get_args

from zeropoint_agent.inode import INode, ResolveMode, NodeStatus, NodeResult

logger = logging.getLogger(__name__)


def _get_io_types(node: INode) -> tuple:
    """Extract (I, O) type params from an INode subclass."""
    for base in getattr(type(node), "__orig_bases__", ()):
        origin = getattr(base, "__origin__", None)
        if origin is INode:
            args = get_args(base)
            if len(args) == 2:
                return args
    raise TypeError(f"{type(node).__name__} does not properly parameterize INode[I, O]")


@dataclass
class NodeEntry:
    """A node in the graph with its metadata."""
    node: INode
    parents: List[str]
    status: NodeStatus = NodeStatus.PENDING
    output: Any = None
    error: Optional[str] = None
    input_type: type = type(None)
    output_type: type = type(None)


class DAG:
    """
    The graph — compiler + runtime.

    add() type-checks edges. resolve() propagates values.
    The executor processes NodeResult objects generically.
    """

    def __init__(self, store=None):
        self._nodes: Dict[str, NodeEntry] = {}
        self._order: List[str] = []
        self._store = store

    def add(self, node_id: str, node: INode, parents: Optional[List[str]] = None) -> str:
        """Add a node with type-checked edges. Skips if already in graph or store."""
        parents = parents or []

        # Already in memory — skip
        if node_id in self._nodes:
            return node_id

        # Already in store (from previous run) — skip
        if self._store and self._store.has_node(node_id):
            # Re-add to in-memory graph without persisting
            i_type, o_type = _get_io_types(node)
            entry = NodeEntry(node=node, parents=parents, input_type=i_type, output_type=o_type)
            self._nodes[node_id] = entry
            self._order.append(node_id)
            logger.debug(f"Loaded {node_id} from store")
            return node_id

        i_type, o_type = _get_io_types(node)

        for parent_id in parents:
            if parent_id not in self._nodes:
                raise KeyError(f"Parent node not found: {parent_id}")
            parent_o = self._nodes[parent_id].output_type
            # I=None or I=object means "don't care about input type" — skip check
            if i_type is not type(None) and i_type is not object:
                if not issubclass(parent_o, i_type):
                    raise TypeError(
                        f"Type mismatch: {parent_id}.O ({parent_o.__name__}) "
                        f"does not match {node_id}.I ({i_type.__name__})")

        # Root nodes with typed input still need parents (unless I=None)
        if not parents and i_type is not type(None) and i_type is not object:
            raise TypeError(f"Root node {node_id} must have I=None, got I={i_type.__name__}")

        entry = NodeEntry(node=node, parents=parents, input_type=i_type, output_type=o_type)
        self._nodes[node_id] = entry
        self._order.append(node_id)

        if self._store:
            from zeropoint_agent.graph_store import StoredNode
            config = {k: v for k, v in node.__dict__.items() if not k.startswith("_")}
            self._store.add_node(StoredNode(
                id=node_id, node_type=type(node).__name__,
                node_class=f"{type(node).__module__}.{type(node).__name__}",
                config=config))
            for parent_id in parents:
                self._store.add_edge(parent_id, node_id)

        logger.debug(f"Added {node_id}: {type(node).__name__} "
                     f"[{i_type.__name__ if i_type else '∅'} → {o_type.__name__}]")
        return node_id

    def resolve(self, mode: ResolveMode = ResolveMode.LIVE) -> Dict[str, NodeStatus]:
        """Run the graph — propagate values through I → O edges."""
        logger.info(f"Resolving DAG ({len(self._nodes)} nodes, mode={mode.value})")
        results = {}

        for node_id in self._order:
            entry = self._nodes[node_id]
            result = self._resolve_node(node_id, entry, mode)
            results[node_id] = result.status

        return results

    def resolve_subset(self, node_ids: list, mode: ResolveMode = ResolveMode.LIVE) -> Dict[str, NodeStatus]:
        """Resolve only the specified nodes, in topo order."""
        results = {}
        ordered = [nid for nid in self._order if nid in node_ids]
        for node_id in ordered:
            entry = self._nodes[node_id]
            result = self._resolve_node(node_id, entry, mode)
            results[node_id] = result.status
        return results

    def _resolve_node(self, node_id: str, entry: NodeEntry, mode: ResolveMode) -> NodeResult:
        """Resolve a single node — the generic NodeResult processor."""
        node = entry.node

        try:
            # Check parents — BLOCKED, ERROR, or SKIPPED parents block children
            allowed = {NodeStatus.SUCCESS, NodeStatus.PENDING_REBOOT}
            if mode == ResolveMode.DRY_RUN:
                allowed.add(NodeStatus.PENDING)

            for parent_id in entry.parents:
                parent_status = self._nodes[parent_id].status

                # SKIPPED or SUCCESS_SKIP parent → skip children too
                if parent_status in (NodeStatus.SKIPPED, NodeStatus.SUCCESS_SKIP):
                    entry.status = NodeStatus.SKIPPED
                    self._persist_status(node_id, entry)
                    r = NodeResult.skipped(f"parent {parent_id} {parent_status.value}")
                    logger.info(f"⊘ {node_id} — skipped (parent {parent_id} {parent_status.value})")
                    return r

                if parent_status not in allowed:
                    entry.status = NodeStatus.BLOCKED
                    self._persist_status(node_id, entry)
                    r = NodeResult()
                    r.status = NodeStatus.BLOCKED
                    return r

            # Gather input
            if not entry.parents:
                input_val = None
            elif len(entry.parents) == 1:
                input_val = self._nodes[entry.parents[0]].output
            else:
                input_val = self._nodes[entry.parents[0]].output

            # Verify first
            verify_result = node.verify(mode)

            if verify_result.status == NodeStatus.SUCCESS:
                entry.status = NodeStatus.SUCCESS
                entry.output = verify_result.output
                self._persist_status(node_id, entry)
                logger.info(f"✓ {node_id} — already converged")
                return verify_result

            # Dry run: report what would change, use mock output for propagation
            if mode == ResolveMode.DRY_RUN:
                mock_result = node.resolve(input_val, ResolveMode.MOCK)
                entry.status = NodeStatus.PENDING
                entry.output = mock_result.output
                self._persist_status(node_id, entry)
                logger.info(f"~ {node_id} — would change (dry run)")
                r = NodeResult()
                r.status = NodeStatus.PENDING
                r.output = mock_result.output
                return r

            # Resolve
            entry.status = NodeStatus.RUNNING
            logger.info(f"Resolving {node_id} ({type(node).__name__})")

            result = node.resolve(input_val, mode)

            # Process the result generically
            entry.status = result.status
            entry.output = result.output
            entry.error = result.error

            # Write systemd units if any (skip in mock mode)
            if mode != ResolveMode.MOCK:
                for unit in result.systemd_units:
                    self._write_systemd_unit(unit)

            self._persist_status(node_id, entry)

            status_icon = {"success": "✓", "pending_reboot": "⏳", "error": "✗"}.get(
                result.status.value, "?")
            logger.info(f"{status_icon} {node_id} — {result.status.value}")

            return result

        except Exception as e:
            logger.error(f"✗ {node_id} — failed: {e}")
            entry.status = NodeStatus.ERROR
            entry.error = str(e)
            self._persist_status(node_id, entry)
            return NodeResult.failed(str(e))

    def _persist_status(self, node_id: str, entry: NodeEntry) -> None:
        if not self._store:
            return
        output_dict = None
        if entry.output is not None:
            output_dict = asdict(entry.output) if hasattr(entry.output, "__dataclass_fields__") else None
        self._store.update_status(
            node_id, status=entry.status.value,
            output=output_dict, error=entry.error)

    def _write_systemd_unit(self, unit) -> None:
        """Write a systemd unit file."""
        unit_dir = FilePath("/etc/systemd/system")
        unit_path = unit_dir / f"{unit.name}.service"
        try:
            unit_path.write_text(unit.render())
            logger.info(f"Wrote systemd unit: {unit_path}")
        except PermissionError:
            logger.debug(f"Would write systemd unit: {unit.name}.service")
        except Exception as e:
            logger.warning(f"Failed to write systemd unit {unit.name}: {e}")

    def remove(self, node_id: str) -> None:
        """Remove a node from the graph."""
        if node_id in self._nodes:
            del self._nodes[node_id]
            self._order = [nid for nid in self._order if nid != node_id]
            for entry in self._nodes.values():
                if node_id in entry.parents:
                    entry.parents.remove(node_id)
            if self._store:
                self._store.remove_node(node_id)
        else:
            raise KeyError(f"Node not found: {node_id}")

    def get(self, node_id: str) -> NodeEntry:
        return self._nodes[node_id]

    @property
    def nodes(self) -> Dict[str, NodeEntry]:
        return dict(self._nodes)
