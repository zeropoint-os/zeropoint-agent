"""DAG — the compiler and runtime for the graph-based language.

dag.add() is the compiler — validates I/O types at edge creation.
dag.resolve() is the runtime — topo-walks and propagates values.
"""

import logging
from typing import Any, Dict, List, Optional, get_args
from dataclasses import dataclass

from zeropoint_agent.inode import INode
from zeropoint_agent.entities import NodeStatus

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
    parents: List[str]         # parent node IDs
    status: NodeStatus = NodeStatus.PENDING
    output: Any = None         # resolved O value
    error: Optional[str] = None
    input_type: type = type(None)
    output_type: type = type(None)


class DAG:
    """
    The graph — compiler + runtime.

    add() type-checks edges. resolve() propagates values.
    """

    def __init__(self):
        self._nodes: Dict[str, NodeEntry] = {}
        self._order: List[str] = []  # insertion order (valid topo order if added correctly)

    def add(self, node_id: str, node: INode, parents: Optional[List[str]] = None) -> str:
        """
        Add a node to the graph with type-checked edges.

        This is the compiler — if parent.O doesn't match node.I,
        it's a type error and the edge is rejected.

        Args:
            node_id: Unique identifier for this node
            node: The INode instance (config = desired state)
            parents: List of parent node IDs

        Returns:
            The node_id

        Raises:
            TypeError: if edge types don't match
            KeyError: if a parent doesn't exist
        """
        parents = parents or []
        i_type, o_type = _get_io_types(node)

        # Type-check each parent edge
        for parent_id in parents:
            if parent_id not in self._nodes:
                raise KeyError(f"Parent node not found: {parent_id}")

            parent_entry = self._nodes[parent_id]
            parent_o = parent_entry.output_type

            if i_type is type(None):
                raise TypeError(
                    f"Node {node_id} declares no input (I=None) but has parents"
                )
            if not issubclass(parent_o, i_type):
                raise TypeError(
                    f"Type mismatch: {parent_id}.O ({parent_o.__name__}) "
                    f"does not match {node_id}.I ({i_type.__name__})"
                )

        # Root nodes must have I=None
        if not parents and i_type is not type(None):
            raise TypeError(
                f"Root node {node_id} must have I=None, "
                f"got I={i_type.__name__}"
            )

        self._nodes[node_id] = NodeEntry(
            node=node,
            parents=parents,
            input_type=i_type,
            output_type=o_type,
        )
        self._order.append(node_id)

        logger.debug(
            f"Added {node_id}: {type(node).__name__} "
            f"[{i_type.__name__ if i_type else '∅'} → {o_type.__name__}]"
        )
        return node_id

    def resolve(self) -> Dict[str, NodeStatus]:
        """
        Run the graph — propagate values through I → O edges.

        Walks nodes in topological order. For each node:
        - Collects parent output as input
        - Calls node.resolve(input)
        - Stores output for children

        Returns:
            Dict of node_id → final status
        """
        logger.info(f"Resolving DAG ({len(self._nodes)} nodes)")
        results = {}

        for node_id in self._order:
            entry = self._nodes[node_id]
            node = entry.node

            try:
                # Check if any parent failed/blocked
                blocked = False
                for parent_id in entry.parents:
                    parent_status = self._nodes[parent_id].status
                    if parent_status not in (NodeStatus.SUCCESS, NodeStatus.PENDING_REBOOT):
                        logger.info(f"Blocking {node_id}: parent {parent_id} is {parent_status.value}")
                        entry.status = NodeStatus.BLOCKED
                        blocked = True
                        break

                if blocked:
                    results[node_id] = entry.status
                    continue

                # Gather input from parent(s)
                if not entry.parents:
                    input_val = None
                elif len(entry.parents) == 1:
                    input_val = self._nodes[entry.parents[0]].output
                else:
                    # Multiple parents — for now, pass first parent's output
                    # TODO: multi-input projection for union types
                    input_val = self._nodes[entry.parents[0]].output

                # Resolve
                entry.status = NodeStatus.RUNNING
                logger.info(f"Resolving {node_id} ({type(node).__name__})")

                output = node.resolve(input_val)
                entry.output = output

                # Verify convergence
                if node.verify():
                    entry.status = NodeStatus.SUCCESS
                    logger.info(f"✓ {node_id} — converged")
                else:
                    entry.status = NodeStatus.PENDING_REBOOT
                    logger.info(f"⏳ {node_id} — applied, pending verification")

            except Exception as e:
                logger.error(f"✗ {node_id} — failed: {e}")
                entry.status = NodeStatus.ERROR
                entry.error = str(e)

            results[node_id] = entry.status

        return results

    def get(self, node_id: str) -> NodeEntry:
        """Get a node entry by ID."""
        return self._nodes[node_id]

    @property
    def nodes(self) -> Dict[str, NodeEntry]:
        """All nodes in the graph."""
        return dict(self._nodes)
