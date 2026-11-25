"""Persistence contract for the ZeroPoint graph runtime.

The runtime has one production implementation, SQLiteGraphStore.  Keeping the
contract separate from that implementation prevents the DAG and handlers from
coupling to SQL details and gives tests a small surface to fake.  It is not a
runtime-selectable backend system.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class StoredNode:
    """A node and its latest observed state as persisted by a GraphStore."""

    id: str
    node_type: str
    node_class: str
    config: Dict[str, Any]
    status: str = "pending"
    output: Optional[Dict[str, Any]] = None
    output_type: Optional[str] = None
    error: Optional[str] = None
    perms: str = "***"
    tags: List[str] = field(default_factory=list)
    producer_id: Optional[str] = None


class GraphStore(ABC):
    """Storage boundary used by the DAG and mutation handlers.

    Implementations must make :meth:`transaction` atomic and durable.  Methods
    called inside a transaction participate in that transaction; methods called
    outside one commit their own complete mutation before returning.
    """

    db_path: str

    @abstractmethod
    def transaction(self) -> AbstractContextManager["GraphStore"]:
        """Open an atomic write transaction."""

    @abstractmethod
    def add_node(self, node: StoredNode) -> None:
        pass

    @abstractmethod
    def replace_node_definition(self, node: StoredNode, parents: List[str]) -> None:
        """Replace a generated node definition while preserving its identity."""

    @abstractmethod
    def has_node(self, node_id: str) -> bool:
        pass

    @abstractmethod
    def add_edge(self, parent_id: str, child_id: str) -> None:
        pass

    @abstractmethod
    def remove_edge(self, parent_id: str, child_id: str) -> None:
        pass

    @abstractmethod
    def get_node(self, node_id: str) -> Optional[StoredNode]:
        pass

    @abstractmethod
    def get_all_nodes(self) -> List[StoredNode]:
        pass

    @abstractmethod
    def get_parents(self, node_id: str) -> List[str]:
        pass

    @abstractmethod
    def get_children(self, node_id: str) -> List[str]:
        pass

    @abstractmethod
    def get_roots(self) -> List[str]:
        pass

    @abstractmethod
    def update_status(
        self,
        node_id: str,
        status: str,
        output: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        pass

    @abstractmethod
    def invalidate_descendants(self, node_id: str) -> int:
        pass

    @abstractmethod
    def set_config(self, node_id: str, config: Dict[str, Any]) -> None:
        pass

    @abstractmethod
    def set_runtime_config(self, node_id: str, config: Dict[str, Any]) -> None:
        """Persist operational metadata without changing desired revision."""

    @abstractmethod
    def set_perms(self, node_id: str, perms: str) -> None:
        pass

    @abstractmethod
    def set_tags(self, node_id: str, tags: List[str]) -> None:
        pass

    @abstractmethod
    def get_desired_revision(self, node_id: str) -> int:
        """Return the revision used to reject stale asynchronous results."""

    @abstractmethod
    def get_graph_revision(self) -> int:
        """Return the monotonic revision of graph intent as a whole."""

    @abstractmethod
    def is_removal_pending(self, node_id: str) -> bool:
        pass

    @abstractmethod
    def remove_node(self, node_id: str) -> bool:
        pass

    @abstractmethod
    def clear(self) -> None:
        pass

    @abstractmethod
    def backup_to(self, destination: str) -> None:
        """Create a consistent online backup of the store."""

    @abstractmethod
    def close(self) -> None:
        pass
