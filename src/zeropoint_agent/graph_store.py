"""GraphStore — RyuGraph persistence layer for the DAG.

Layer 1: raw graph storage. Knows about nodes, edges, status, config, output.
Does NOT know about INode types or resolve/verify logic.
"""

import json
import logging
import os
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import ryugraph

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass
class StoredNode:
    """A node as stored in the graph DB."""
    id: str
    node_type: str       # class name (e.g. "DiskNode")
    node_class: str      # fully qualified (e.g. "zeropoint_agent.nodes.DiskNode")
    config: Dict[str, Any]
    status: str = "pending"
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    perms: str = "***"   # instance-level permissions (3 chars from r/w/d/*/-)


class GraphStore:
    """
    Persistent graph storage backed by RyuGraph.

    Stores nodes (with config, status, output) and DEPENDS_ON edges.
    Single file on disk, survives restarts.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        try:
            self._db = ryugraph.Database(db_path)
        except RuntimeError as e:
            if "wal" in str(e).lower() or "temporary file" in str(e).lower():
                logger.warning(f"Stale WAL file detected, cleaning up: {e}")
                import glob
                for f in glob.glob(f"{db_path}*"):
                    os.remove(f)
                self._db = ryugraph.Database(db_path)
            else:
                raise
        self._conn = ryugraph.Connection(self._db)
        self._ensure_schema()

    def _ensure_schema(self):
        """Create tables if they don't exist."""
        try:
            # Check if schema exists by querying
            self._conn.execute("MATCH (n:Node) RETURN n.id LIMIT 1")
            logger.debug("Schema already exists")
        except Exception:
            logger.info("Creating graph schema")
            self._conn.execute("""
                CREATE NODE TABLE Node(
                    id STRING,
                    node_type STRING,
                    node_class STRING,
                    config STRING,
                    status STRING DEFAULT 'pending',
                    output STRING DEFAULT '',
                    error STRING DEFAULT '',
                    perms STRING DEFAULT '***',
                    PRIMARY KEY(id)
                )
            """)
            self._conn.execute("""
                CREATE REL TABLE DEPENDS_ON(FROM Node TO Node)
            """)
        # Probe whether the perms column exists (for DBs created before the
        # field was added). Cache the result so reads can degrade gracefully.
        try:
            self._conn.execute("MATCH (n:Node) RETURN n.perms LIMIT 1")
            self._has_perms_column = True
        except Exception:
            self._has_perms_column = False
            logger.warning(
                "Graph store predates the perms column; reads will default to '***'. "
                "Re-create the store to persist perms.")

    def add_node(self, node: StoredNode) -> None:
        """Add a node to the graph."""
        config_json = json.dumps(node.config)
        output_json = json.dumps(node.output) if node.output else ""
        error = node.error or ""

        if self._has_perms_column:
            self._conn.execute(
                "CREATE (n:Node {"
                f"id: $id, node_type: $node_type, node_class: $node_class, "
                f"config: $config, status: $status, output: $output, "
                f"error: $error, perms: $perms"
                "})",
                parameters={
                    "id": node.id,
                    "node_type": node.node_type,
                    "node_class": node.node_class,
                    "config": config_json,
                    "status": node.status,
                    "output": output_json,
                    "error": error,
                    "perms": node.perms,
                },
            )
        else:
            self._conn.execute(
                "CREATE (n:Node {"
                f"id: $id, node_type: $node_type, node_class: $node_class, "
                f"config: $config, status: $status, output: $output, error: $error"
                "})",
                parameters={
                    "id": node.id,
                    "node_type": node.node_type,
                    "node_class": node.node_class,
                    "config": config_json,
                    "status": node.status,
                    "output": output_json,
                    "error": error,
                },
            )
        logger.debug(f"Stored node: {node.id} ({node.node_type})")

    def has_node(self, node_id: str) -> bool:
        """Check if a node exists in the store."""
        result = self._conn.execute(
            "MATCH (n:Node {id: $id}) RETURN n.id",
            parameters={"id": node_id},
        )
        return result.has_next()

    def add_edge(self, parent_id: str, child_id: str) -> None:
        """Add a DEPENDS_ON edge between nodes."""
        self._conn.execute(
            "MATCH (a:Node {id: $parent}), (b:Node {id: $child}) "
            "CREATE (a)-[:DEPENDS_ON]->(b)",
            parameters={"parent": parent_id, "child": child_id},
        )
        logger.debug(f"Stored edge: {parent_id} → {child_id}")

    def get_node(self, node_id: str) -> Optional[StoredNode]:
        """Get a node by ID."""
        if self._has_perms_column:
            result = self._conn.execute(
                "MATCH (n:Node {id: $id}) "
                "RETURN n.id, n.node_type, n.node_class, n.config, "
                "n.status, n.output, n.error, n.perms",
                parameters={"id": node_id},
            )
        else:
            result = self._conn.execute(
                "MATCH (n:Node {id: $id}) "
                "RETURN n.id, n.node_type, n.node_class, n.config, "
                "n.status, n.output, n.error",
                parameters={"id": node_id},
            )
        if result.has_next():
            row = result.get_next()
            return StoredNode(
                id=row[0],
                node_type=row[1],
                node_class=row[2],
                config=json.loads(row[3]) if row[3] else {},
                status=row[4],
                output=json.loads(row[5]) if row[5] else None,
                error=row[6] if row[6] else None,
                perms=row[7] if self._has_perms_column and len(row) > 7 and row[7] else "***",
            )
        return None

    def get_all_nodes(self) -> List[StoredNode]:
        """Get all nodes."""
        if self._has_perms_column:
            result = self._conn.execute(
                "MATCH (n:Node) "
                "RETURN n.id, n.node_type, n.node_class, n.config, "
                "n.status, n.output, n.error, n.perms"
            )
        else:
            result = self._conn.execute(
                "MATCH (n:Node) "
                "RETURN n.id, n.node_type, n.node_class, n.config, "
                "n.status, n.output, n.error"
            )
        nodes = []
        while result.has_next():
            row = result.get_next()
            nodes.append(StoredNode(
                id=row[0],
                node_type=row[1],
                node_class=row[2],
                config=json.loads(row[3]) if row[3] else {},
                status=row[4],
                output=json.loads(row[5]) if row[5] else None,
                error=row[6] if row[6] else None,
                perms=row[7] if self._has_perms_column and len(row) > 7 and row[7] else "***",
            ))
        return nodes

    def get_parents(self, node_id: str) -> List[str]:
        """Get parent node IDs."""
        result = self._conn.execute(
            "MATCH (parent:Node)-[:DEPENDS_ON]->(child:Node {id: $id}) "
            "RETURN parent.id",
            parameters={"id": node_id},
        )
        parents = []
        while result.has_next():
            parents.append(result.get_next()[0])
        return parents

    def get_children(self, node_id: str) -> List[str]:
        """Get child node IDs."""
        result = self._conn.execute(
            "MATCH (parent:Node {id: $id})-[:DEPENDS_ON]->(child:Node) "
            "RETURN child.id",
            parameters={"id": node_id},
        )
        children = []
        while result.has_next():
            children.append(result.get_next()[0])
        return children

    def get_roots(self) -> List[str]:
        """Get root node IDs (no incoming edges)."""
        result = self._conn.execute(
            "MATCH (n:Node) "
            "WHERE NOT EXISTS { MATCH (:Node)-[:DEPENDS_ON]->(n) } "
            "RETURN n.id"
        )
        roots = []
        while result.has_next():
            roots.append(result.get_next()[0])
        return roots

    def update_status(self, node_id: str, status: str,
                      output: Optional[Dict[str, Any]] = None,
                      error: Optional[str] = None) -> None:
        """Update a node's status, output, and/or error."""
        output_json = json.dumps(output) if output else ""
        error_str = error or ""
        self._conn.execute(
            "MATCH (n:Node {id: $id}) "
            "SET n.status = $status, n.output = $output, n.error = $error",
            parameters={
                "id": node_id,
                "status": status,
                "output": output_json,
                "error": error_str,
            },
        )

    def invalidate_descendants(self, node_id: str) -> int:
        """Set all descendants of a node to 'pending'. Returns count."""
        # Recursive path queries don't support parameter binding in RyuGraph,
        # so we walk the graph iteratively via BFS
        visited = set()
        queue = self.get_children(node_id)
        while queue:
            child_id = queue.pop(0)
            if child_id in visited:
                continue
            visited.add(child_id)
            self._conn.execute(
                "MATCH (n:Node {id: $id}) SET n.status = 'pending'",
                parameters={"id": child_id},
            )
            queue.extend(self.get_children(child_id))
        return len(visited)

    def set_config(self, node_id: str, config: Dict[str, Any]) -> None:
        """Update a node's stored config JSON."""
        self._conn.execute(
            "MATCH (n:Node {id: $id}) SET n.config = $config",
            parameters={"id": node_id, "config": json.dumps(config)},
        )

    def set_perms(self, node_id: str, perms: str) -> None:
        """Update a node's instance-level permissions string."""
        if not self._has_perms_column:
            return
        self._conn.execute(
            "MATCH (n:Node {id: $id}) SET n.perms = $perms",
            parameters={"id": node_id, "perms": perms},
        )

    def remove_node(self, node_id: str) -> bool:
        """Remove a node and its edges."""
        self._conn.execute(
            "MATCH (n:Node {id: $id}) DETACH DELETE n",
            parameters={"id": node_id},
        )
        return True

    def clear(self) -> None:
        """Remove all nodes and edges."""
        self._conn.execute("MATCH (n:Node) DETACH DELETE n")
