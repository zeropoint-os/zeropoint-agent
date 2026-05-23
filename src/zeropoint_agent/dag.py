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

PERMS_BITS = ("r", "w", "d")
_PERMS_ALPHABET = set("rwd*-")


class NodeExists(ValueError):
    """Raised by dag.add() when a node with the requested id already exists.

    Maps to HTTP 409 Conflict at the REST layer.
    """


def _valid_perms(perms: str) -> bool:
    """A perms string is exactly 3 chars from {r,w,d,*,-} at the right positions."""
    if not isinstance(perms, str) or len(perms) != 3:
        return False
    for i, ch in enumerate(perms):
        if ch == PERMS_BITS[i] or ch in ("*", "-"):
            continue
        return False
    return True


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
    # Cached path derived from namespace ancestry. "" if not under a namespace.
    path: str = ""
    # Instance-level permissions (3 chars from {r,w,d,*,-}). Resolved
    # against parent namespaces and the type default at check time.
    # See zeropoint-agent/permissions-model in the mind-map.
    perms: str = "***"


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
        if store is not None:
            self._load_from_store()

    def _load_from_store(self) -> None:
        """Instantiate and add every node persisted in the store.

        Walks the store in topological order so each node's parents are
        already present when it's added. Bypasses the duplicate check in
        `add()` via the internal `_load_one()` method.
        """
        self._reload_from_store()

    def _reload_from_store(self) -> None:
        """Discard current in-memory state and rebuild from the store.

        Used by the mutation snapshot guard to bring the DAG back in
        sync with disk after a rollback. Safe to call on a DAG that
        already has nodes — the slate is wiped first.
        """
        import importlib

        self._nodes.clear()
        self._order.clear()

        try:
            all_nodes = self._store.get_all_nodes()
        except Exception as e:
            logger.warning("failed to enumerate stored nodes: %s", e)
            return
        if not all_nodes:
            return

        by_id = {n.id: n for n in all_nodes}
        parents_of = {nid: self._store.get_parents(nid) for nid in by_id}

        # Kahn-style topological sort.
        in_degree = {nid: len(parents_of[nid]) for nid in by_id}
        ready = [nid for nid, d in in_degree.items() if d == 0]
        order: List[str] = []
        while ready:
            ready.sort()  # deterministic
            nid = ready.pop(0)
            order.append(nid)
            for cid in self._store.get_children(nid):
                if cid not in in_degree:
                    continue
                in_degree[cid] -= 1
                if in_degree[cid] == 0:
                    ready.append(cid)
        if len(order) < len(by_id):
            # Cycle or missing parent — fall back to insertion order.
            order = [nid for nid in by_id if nid not in order]
            logger.warning(
                "graph store has %d unreachable nodes (cycle?)", len(order))

        for nid in order:
            stored = by_id[nid]
            try:
                module_name, _, cls_name = stored.node_class.rpartition(".")
                cls = getattr(importlib.import_module(module_name), cls_name)
                node = cls(**(stored.config or {}))
                self._load_one(nid, node, parents=parents_of[nid], stored=stored)
            except Exception as e:
                logger.warning(
                    "failed to load node %s (%s): %s",
                    nid, stored.node_class, e)

    def add(self, node_id: str, node: INode,
            parents: Optional[List[str]] = None,
            perms: str = "***") -> str:
        """Add a new node with type-checked edges.

        Raises:
            NodeExists: if `node_id` is already present in memory or in the
                store. Callers wanting "create if absent" should catch this
                explicitly (or check first via `node_id in self.nodes`).
            ValueError: if `perms` is malformed.
            KeyError:   if a parent id doesn't exist.
            TypeError:  on parent/child I/O type mismatch.

        Args:
            perms: instance-level permission string (3 chars from {r,w,d,*,-}).
                   Default "***" means no instance opinion; resolution defers
                   to parent namespaces and the type's default_perms.
        """
        parents = parents or []
        if not _valid_perms(perms):
            raise ValueError(
                f"invalid perms {perms!r}: expected 3 chars from r/w/d/*/-")

        # Dumb primitive: error on duplicate. Callers do their own
        # "if exists, skip/edit" logic.
        if node_id in self._nodes:
            raise NodeExists(
                f"node {node_id!r} already exists in the graph")
        if self._store and self._store.has_node(node_id):
            raise NodeExists(
                f"node {node_id!r} already exists in the persistent store")

        i_type, o_type = _get_io_types(node)

        for parent_id in parents:
            if parent_id not in self._nodes:
                raise KeyError(f"Parent node not found: {parent_id}")
            parent_o = self._nodes[parent_id].output_type
            # I=None, I=object, or I=Any means "don't care about input type" — skip check
            if i_type is type(None) or i_type is object:
                continue
            try:
                from typing import Any as _Any
                if i_type is _Any:
                    continue
            except Exception:
                pass
            try:
                matches = issubclass(parent_o, i_type)
            except TypeError:
                # Non-class type annotation (Union, etc.) — don't enforce
                continue
            if not matches:
                raise TypeError(
                    f"Type mismatch: {parent_id}.O ({parent_o.__name__}) "
                    f"does not match {node_id}.I ({i_type.__name__})")

        # Root nodes: I=None or I=object are valid without parents.
        # Other typed inputs are also OK as roots (no parent means resolve
        # gets None as input, node handles it).

        entry = NodeEntry(node=node, parents=parents, input_type=i_type, output_type=o_type)
        entry.path = self._compute_path(node, parents)
        entry.perms = perms
        self._nodes[node_id] = entry
        self._order.append(node_id)

        if self._store:
            from zeropoint_agent.graph_store import StoredNode
            config = {k: v for k, v in node.__dict__.items() if not k.startswith("_")}
            self._store.add_node(StoredNode(
                id=node_id, node_type=type(node).__name__,
                node_class=f"{type(node).__module__}.{type(node).__name__}",
                config=config, perms=entry.perms))
            for parent_id in parents:
                self._store.add_edge(parent_id, node_id)

        logger.debug(f"Added {node_id}: {type(node).__name__} "
                     f"[{i_type.__name__ if i_type else '∅'} → {o_type.__name__}] "
                     f"path={entry.path!r}")
        return node_id

    def _load_one(self, node_id: str, node: INode,
                  parents: List[str], stored=None) -> None:
        """Rehydrate a single node from the store into the in-memory DAG.

        Bypasses the duplicate check in `add()`. For internal use by
        `_load_from_store()` only — never call this for user-driven adds.
        """
        if node_id in self._nodes:
            return  # already loaded (shouldn't happen in normal flow)

        i_type, o_type = _get_io_types(node)
        entry = NodeEntry(node=node, parents=parents,
                          input_type=i_type, output_type=o_type)
        if stored is not None:
            try:
                entry.status = NodeStatus(stored.status)
            except ValueError:
                pass
            stored_perms = getattr(stored, "perms", None) or "***"
            entry.perms = stored_perms if _valid_perms(stored_perms) else "***"
            # Restore the persisted output (as a dict). Consumers that
            # type-check should accept dicts as well as the original
            # dataclass shape.
            if getattr(stored, "output", None) is not None:
                entry.output = stored.output
        entry.path = self._compute_path(node, parents)
        self._nodes[node_id] = entry
        self._order.append(node_id)
        logger.debug(
            f"Rehydrated {node_id} (status={entry.status.value}, "
            f"path={entry.path!r}, perms={entry.perms})")

    def _compute_path(self, node: INode, parents: List[str]) -> str:
        """Compute a node's path from its NamespaceNode parents.

        - If `node` is itself a NamespaceNode, the path is
          `<namespace-parent's path>/<self.name>` (or just `self.name` for root).
        - Otherwise, the path is the path of the (at most one) NamespaceNode parent.
        """
        from zeropoint_agent.nodes.config.namespace import NamespaceNode

        ns_parent_paths = [
            self._nodes[pid].path
            for pid in parents
            if isinstance(self._nodes[pid].node, NamespaceNode)
        ]
        if len(ns_parent_paths) > 1:
            raise ValueError(
                f"node has more than one NamespaceNode parent: {parents}")
        inherited = ns_parent_paths[0] if ns_parent_paths else ""

        if isinstance(node, NamespaceNode):
            name = getattr(node, "name", "")
            return f"{inherited}/{name}" if inherited else name
        return inherited

    def _find_namespace_parent(self, entry: NodeEntry) -> Optional[NodeEntry]:
        """Return the (at most one) NamespaceNode parent of an entry, or None."""
        from zeropoint_agent.nodes.config.namespace import NamespaceNode
        for pid in entry.parents:
            p = self._nodes.get(pid)
            if p is not None and isinstance(p.node, NamespaceNode):
                return p
        return None

    def effective_perms(self, node_id: str) -> str:
        """Resolve a node's effective permissions across all layers.

        See zeropoint-agent/permissions-model:
          - Instance perms (own)
          - Parent namespace chain (walked outward)
          - Type default (floor)

        Per bit: any layer with '-' vetoes; else any layer with the letter
        grants; else default deny.
        """
        entry = self._nodes.get(node_id)
        if entry is None:
            raise KeyError(f"node not found: {node_id}")

        layers: List[str] = [entry.perms]
        ns = self._find_namespace_parent(entry)
        while ns is not None:
            layers.append(ns.perms)
            ns = self._find_namespace_parent(ns)
        layers.append(getattr(type(entry.node), "default_perms", "***"))

        result = []
        for i, bit in enumerate(PERMS_BITS):
            granted = False
            for layer in layers:
                if i >= len(layer):
                    continue
                ch = layer[i]
                if ch == "-":
                    granted = False
                    break  # hard veto — stop scanning this bit
                if ch == bit:
                    granted = True
                # ch == '*' — silent, keep looking
            result.append(bit if granted else "-")
        return "".join(result)

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
            # 0 parents → None
            # 1 parent → that parent's output
            # N parents → dict {parent_id: parent_output}
            if not entry.parents:
                input_val = None
            elif len(entry.parents) == 1:
                input_val = self._nodes[entry.parents[0]].output
            else:
                input_val = {
                    pid: self._nodes[pid].output
                    for pid in entry.parents
                }

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
