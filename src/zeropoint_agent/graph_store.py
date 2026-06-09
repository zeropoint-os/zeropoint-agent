"""GraphStore — RyuGraph persistence layer for the DAG.

Layer 1: raw graph storage. Knows about nodes, edges, status, config, output.
Does NOT know about INode types or resolve/verify logic.
"""

import json
import logging
import os
import shutil
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import ryugraph

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass
class StoredNode:
    """A node as stored in the graph DB."""
    id: str
    node_type: str       # class name (e.g. "Disk")
    node_class: str      # fully qualified (e.g. "zeropoint_agent.nodes.Disk")
    config: Dict[str, Any]
    status: str = "pending"
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    perms: str = "***"   # instance-level permissions (3 chars from r/w/d/*/-)
    tags: List[str] = field(default_factory=list)  # creator-assigned tags


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
                    tags STRING DEFAULT '',
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
        # Same probe for the tags column (added after perms).
        try:
            self._conn.execute("MATCH (n:Node) RETURN n.tags LIMIT 1")
            self._has_tags_column = True
        except Exception:
            self._has_tags_column = False
            logger.warning(
                "Graph store predates the tags column; reads will default to []. "
                "Re-create the store to persist tags.")

    # ---- schema helpers -----------------------------------------------------

    def _node_columns(self) -> List[str]:
        """Column names for a Node row in the order returned by reads."""
        cols = ["id", "node_type", "node_class", "config", "status", "output", "error"]
        if self._has_perms_column:
            cols.append("perms")
        if self._has_tags_column:
            cols.append("tags")
        return cols

    def _row_to_stored(self, row, cols: List[str]) -> "StoredNode":
        """Build a StoredNode from a row + column list."""
        get = dict(zip(cols, row)).get
        perms = get("perms") or "***"
        tags_raw = get("tags") or ""
        try:
            tags = json.loads(tags_raw) if tags_raw else []
        except (ValueError, TypeError):
            tags = []
        return StoredNode(
            id=get("id"),
            node_type=get("node_type"),
            node_class=get("node_class"),
            config=json.loads(get("config")) if get("config") else {},
            status=get("status"),
            output=json.loads(get("output")) if get("output") else None,
            error=get("error") if get("error") else None,
            perms=perms,
            tags=list(tags) if isinstance(tags, list) else [],
        )

    def add_node(self, node: StoredNode) -> None:
        """Add a node to the graph."""
        params: Dict[str, Any] = {
            "id": node.id,
            "node_type": node.node_type,
            "node_class": node.node_class,
            "config": json.dumps(node.config),
            "status": node.status,
            "output": json.dumps(node.output) if node.output else "",
            "error": node.error or "",
        }
        cols = ["id", "node_type", "node_class", "config", "status", "output", "error"]
        if self._has_perms_column:
            cols.append("perms")
            params["perms"] = node.perms
        if self._has_tags_column:
            cols.append("tags")
            params["tags"] = json.dumps(list(node.tags)) if node.tags else ""
        props = ", ".join(f"{c}: ${c}" for c in cols)
        self._conn.execute(f"CREATE (n:Node {{{props}}})", parameters=params)
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

    def remove_edge(self, parent_id: str, child_id: str) -> None:
        """Remove a DEPENDS_ON edge between two nodes."""
        self._conn.execute(
            "MATCH (a:Node {id: $parent})-[e:DEPENDS_ON]->(b:Node {id: $child}) "
            "DELETE e",
            parameters={"parent": parent_id, "child": child_id},
        )
        logger.debug(f"Removed edge: {parent_id} → {child_id}")

    def get_node(self, node_id: str) -> Optional[StoredNode]:
        """Get a node by ID."""
        cols = self._node_columns()
        projection = ", ".join(f"n.{c}" for c in cols)
        result = self._conn.execute(
            f"MATCH (n:Node {{id: $id}}) RETURN {projection}",
            parameters={"id": node_id},
        )
        if result.has_next():
            return self._row_to_stored(result.get_next(), cols)
        return None

    def get_all_nodes(self) -> List[StoredNode]:
        """Get all nodes."""
        cols = self._node_columns()
        projection = ", ".join(f"n.{c}" for c in cols)
        result = self._conn.execute(f"MATCH (n:Node) RETURN {projection}")
        nodes = []
        while result.has_next():
            nodes.append(self._row_to_stored(result.get_next(), cols))
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

    def set_tags(self, node_id: str, tags: List[str]) -> None:
        """Update a node's creator-assigned tags (JSON-encoded list)."""
        if not self._has_tags_column:
            return
        encoded = json.dumps(list(tags)) if tags else ""
        self._conn.execute(
            "MATCH (n:Node {id: $id}) SET n.tags = $tags",
            parameters={"id": node_id, "tags": encoded},
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

    # ---- lifecycle / snapshot ----------------------------------------------

    def close(self) -> None:
        """Release the underlying connection + database handles.

        After close() the store must not be used. Used by the snapshot
        guard to release the file lock before swapping the db file.
        """
        self._conn = None
        self._db = None

    def _all_files(self) -> List[Path]:
        """Every file ryugraph currently owns for this database.

        ryugraph keeps a single primary file plus WAL/lock sidecars
        named graph.db.* — we glob for everything matching so the
        snapshot includes any in-flight sidecars.
        """
        import glob
        return [Path(p) for p in glob.glob(f"{self.db_path}*")
                if not p.endswith(".snapshot")]

    def snapshot_to(self, snapshot_path: str) -> None:
        """Copy the current on-disk db state to `snapshot_path`.

        Used by mutation handlers to bookmark a known-good state
        before performing a multi-step change. The caller is
        responsible for `restore_from(...)` if anything fails, and
        for discarding the snapshot on success.
        """
        # ryugraph writes through; a copy under the connection's
        # lock is safe because we hold the only writer.
        snap = Path(snapshot_path)
        snap.parent.mkdir(parents=True, exist_ok=True)
        if snap.exists():
            if snap.is_dir():
                shutil.rmtree(snap)
            else:
                snap.unlink()
        snap.mkdir(parents=True)
        for src in self._all_files():
            shutil.copy2(src, snap / src.name)

    def restore_from(self, snapshot_path: str) -> None:
        """Replace the current db file(s) with the snapshot copy.

        Closes the connection first, swaps the files, then reopens
        the database. After this call the store is usable again at
        the original db_path.
        """
        snap = Path(snapshot_path)
        if not snap.exists() or not snap.is_dir():
            raise RuntimeError(f"snapshot not found: {snap}")

        self.close()
        # Remove any current db files (including stale sidecars from
        # the failed operation we're rolling back).
        for src in self._all_files():
            try:
                src.unlink()
            except FileNotFoundError:
                pass
        # Copy snapshot contents back into place.
        db_dir = Path(self.db_path).parent
        for src in snap.iterdir():
            shutil.copy2(src, db_dir / src.name)
        # Re-open the connection at the original path.
        self._db = ryugraph.Database(self.db_path)
        self._conn = ryugraph.Connection(self._db)
        # No _ensure_schema — the snapshot already has the right schema.
        # _has_perms_column is set during __init__ and unchanged here.

    def discard_snapshot(self, snapshot_path: str) -> None:
        """Delete a snapshot directory; safe to call if it doesn't exist."""
        snap = Path(snapshot_path)
        if snap.exists():
            shutil.rmtree(snap, ignore_errors=True)
