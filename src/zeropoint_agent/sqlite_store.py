"""SQLite implementation of the ZeroPoint graph and execution ledger.

SQLite is the single source of truth for graph intent, observed node state and
the durable execution records that will back graph runs, Promises, jobs and the
transactional outbox.  The scheduler is still an in-memory DAG; this module is
the crash-safe ledger it can reconstruct from.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from zeropoint_agent.graph_store import GraphStore, StoredNode

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 6

_MIGRATION_1 = """
CREATE TABLE nodes (
    id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL,
    node_class TEXT NOT NULL,
    config_json TEXT NOT NULL CHECK (json_valid(config_json)),
    permissions TEXT NOT NULL DEFAULT '***' CHECK (length(permissions) = 3),
    tags_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tags_json)),
    desired_revision INTEGER NOT NULL DEFAULT 1 CHECK (desired_revision > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE edges (
    parent_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    child_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    PRIMARY KEY (parent_id, child_id),
    UNIQUE (child_id, position),
    CHECK (parent_id <> child_id)
) STRICT;

CREATE INDEX edges_by_parent ON edges(parent_id, position, child_id);
CREATE INDEX edges_by_child ON edges(child_id, position, parent_id);

CREATE TABLE graph_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    graph_revision INTEGER NOT NULL DEFAULT 0 CHECK (graph_revision >= 0)
) STRICT;

INSERT INTO graph_metadata(singleton, graph_revision) VALUES (1, 0);

CREATE TABLE promises (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    result_type TEXT,
    result_json TEXT CHECK (result_json IS NULL OR json_valid(result_json)),
    error_json TEXT CHECK (error_json IS NULL OR json_valid(error_json)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    resolved_at TEXT
) STRICT;

CREATE INDEX promises_by_status ON promises(status, created_at);

CREATE TABLE node_state (
    node_id TEXT PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending',
    observed_revision INTEGER,
    output_type TEXT,
    output_json TEXT CHECK (output_json IS NULL OR json_valid(output_json)),
    error TEXT,
    active_promise_id TEXT REFERENCES promises(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE INDEX node_state_by_status ON node_state(status, updated_at);
CREATE INDEX node_state_by_promise ON node_state(active_promise_id)
    WHERE active_promise_id IS NOT NULL;

CREATE TABLE graph_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    desired_revision INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at TEXT,
    completed_at TEXT,
    error_json TEXT CHECK (error_json IS NULL OR json_valid(error_json))
) STRICT;

CREATE INDEX graph_runs_by_status ON graph_runs(status, created_at);

CREATE TABLE graph_run_nodes (
    graph_run_id TEXT NOT NULL REFERENCES graph_runs(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    desired_revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    remaining_parents INTEGER NOT NULL CHECK (remaining_parents >= 0),
    promise_id TEXT REFERENCES promises(id) ON DELETE SET NULL,
    result_json TEXT CHECK (result_json IS NULL OR json_valid(result_json)),
    error_json TEXT CHECK (error_json IS NULL OR json_valid(error_json)),
    PRIMARY KEY (graph_run_id, node_id)
) STRICT;

CREATE INDEX graph_run_nodes_ready
    ON graph_run_nodes(graph_run_id, status, remaining_parents);
CREATE INDEX graph_run_nodes_by_promise ON graph_run_nodes(promise_id)
    WHERE promise_id IS NOT NULL;

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    promise_id TEXT NOT NULL UNIQUE REFERENCES promises(id) ON DELETE CASCADE,
    graph_run_id TEXT REFERENCES graph_runs(id) ON DELETE SET NULL,
    node_id TEXT REFERENCES nodes(id) ON DELETE SET NULL,
    desired_revision INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    executor TEXT NOT NULL,
    spec_json TEXT NOT NULL CHECK (json_valid(spec_json)),
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 1),
    checkpoint_json TEXT CHECK (checkpoint_json IS NULL OR json_valid(checkpoint_json)),
    lease_owner TEXT,
    lease_expires_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE INDEX jobs_dispatchable ON jobs(status, lease_expires_at, created_at);
CREATE INDEX jobs_by_run ON jobs(graph_run_id, created_at);
CREATE INDEX jobs_by_node ON jobs(node_id, created_at);

CREATE TABLE job_attempts (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL,
    worker_id TEXT,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at TEXT,
    checkpoint_json TEXT CHECK (checkpoint_json IS NULL OR json_valid(checkpoint_json)),
    error_json TEXT CHECK (error_json IS NULL OR json_valid(error_json)),
    PRIMARY KEY (job_id, attempt)
) STRICT;

CREATE TABLE resource_locks (
    resource_key TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    lease_owner TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    acquired_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE INDEX resource_locks_by_job ON resource_locks(job_id);
CREATE INDEX resource_locks_by_expiry ON resource_locks(lease_expires_at);

CREATE TABLE events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE INDEX events_by_aggregate
    ON events(aggregate_type, aggregate_id, sequence);
CREATE INDEX events_by_type ON events(event_type, sequence);

CREATE TABLE outbox (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    dispatched_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT
) STRICT;

CREATE INDEX outbox_dispatchable
    ON outbox(dispatched_at, next_attempt_at, lease_expires_at, sequence);
"""

_MIGRATION_2 = """
ALTER TABLE promises ADD COLUMN graph_run_id TEXT;
ALTER TABLE promises ADD COLUMN node_id TEXT;
ALTER TABLE promises ADD COLUMN desired_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE promises ADD COLUMN input_fingerprint TEXT NOT NULL DEFAULT '';
ALTER TABLE promises ADD COLUMN updated_at TEXT;
ALTER TABLE promises ADD COLUMN cancel_requested_at TEXT;

ALTER TABLE graph_runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'mock';
ALTER TABLE graph_runs ADD COLUMN pattern TEXT;
ALTER TABLE graph_runs ADD COLUMN updated_at TEXT;
ALTER TABLE graph_runs ADD COLUMN cancel_requested_at TEXT;

ALTER TABLE jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 1;
ALTER TABLE jobs ADD COLUMN timeout_seconds REAL;
ALTER TABLE jobs ADD COLUMN cancel_requested_at TEXT;

UPDATE promises SET updated_at = created_at WHERE updated_at IS NULL;
UPDATE graph_runs SET updated_at = created_at WHERE updated_at IS NULL;
"""

_MIGRATION_3 = """
ALTER TABLE promises ADD COLUMN producer TEXT NOT NULL DEFAULT '';
ALTER TABLE promises ADD COLUMN cancellation_kind TEXT NOT NULL DEFAULT 'unsupported';
ALTER TABLE promises ADD COLUMN pause_kind TEXT NOT NULL DEFAULT 'unsupported';
ALTER TABLE promises ADD COLUMN progress_kind TEXT NOT NULL DEFAULT 'unsupported';
ALTER TABLE promises ADD COLUMN checkpoint_kind TEXT NOT NULL DEFAULT 'unsupported';

ALTER TABLE graph_runs ADD COLUMN operation TEXT NOT NULL DEFAULT 'resolve';
ALTER TABLE graph_runs ADD COLUMN pause_requested_at TEXT;
ALTER TABLE graph_runs ADD COLUMN paused_at TEXT;

ALTER TABLE jobs ADD COLUMN pause_requested_at TEXT;
ALTER TABLE jobs ADD COLUMN paused_at TEXT;

CREATE TABLE graph_run_edges (
    graph_run_id TEXT NOT NULL REFERENCES graph_runs(id) ON DELETE CASCADE,
    parent_node_id TEXT NOT NULL,
    child_node_id TEXT NOT NULL,
    PRIMARY KEY (graph_run_id, parent_node_id, child_node_id)
) STRICT;

CREATE INDEX graph_run_edges_by_parent
    ON graph_run_edges(graph_run_id, parent_node_id, child_node_id);
CREATE INDEX graph_run_edges_by_child
    ON graph_run_edges(graph_run_id, child_node_id, parent_node_id);
"""

_MIGRATION_4 = """
ALTER TABLE graph_run_nodes ADD COLUMN phase TEXT NOT NULL DEFAULT 'resolve';
ALTER TABLE graph_run_nodes ADD COLUMN verification_json TEXT;
ALTER TABLE promises ADD COLUMN operation_phase TEXT NOT NULL DEFAULT 'resolve';
"""

_MIGRATION_5 = """
CREATE TABLE agent_metadata (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL CHECK (json_valid(value_json)),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;
"""

_MIGRATION_6 = """
ALTER TABLE nodes ADD COLUMN producer_node_id TEXT REFERENCES nodes(id) ON DELETE SET NULL;
CREATE INDEX nodes_by_producer ON nodes(producer_node_id, id);
"""

_MIGRATIONS = (
    (1, "initial graph and execution ledger", _MIGRATION_1),
    (2, "durable promises and graph runs", _MIGRATION_2),
    (3, "typed promise controls and removal runs", _MIGRATION_3),
    (4, "promise-based verification phases", _MIGRATION_4),
    (5, "persistent agent identity metadata", _MIGRATION_5),
    (6, "promise-produced graph nodes", _MIGRATION_6),
)

_NODE_SELECT = """
SELECT
    n.id,
    n.node_type,
    n.node_class,
    n.config_json,
    s.status,
    s.output_json,
    s.output_type,
    s.error,
    n.permissions,
    n.tags_json,
    n.producer_node_id
FROM nodes AS n
JOIN node_state AS s ON s.node_id = n.id
"""


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _json_loads(value: Optional[str], default: Any) -> Any:
    if value is None or value == "":
        return default
    return json.loads(value)


class SQLiteGraphStore(GraphStore):
    """Atomic SQLite-backed graph store and future execution ledger."""

    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._transaction_depth = 0
        self._closed = False
        self._ownership_path = f"{self.db_path}.lock"
        self._ownership_file = open(self._ownership_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(
                self._ownership_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as error:
            self._ownership_file.close()
            raise RuntimeError(
                f"graph store is already owned by another agent: {self.db_path}"
            ) from error
        self._ownership_file.seek(0)
        self._ownership_file.truncate()
        self._ownership_file.write(f"{os.getpid()}\n")
        self._ownership_file.flush()

        try:
            self._conn = sqlite3.connect(
                self.db_path,
                isolation_level=None,
                check_same_thread=False,
                timeout=5.0,
            )
            self._conn.row_factory = sqlite3.Row
            self._configure_connection()
            self._apply_migrations()
        except BaseException:
            self._release_ownership()
            raise

    def _configure_connection(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("graph store is closed")

    @contextmanager
    def transaction(self) -> Iterator[GraphStore]:
        """Serialize and atomically commit one graph/runtime transition."""
        with self._lock:
            self._ensure_open()
            if self._transaction_depth:
                # Store-level operations may establish a complete local
                # transaction while a handler already owns the outer one.
                # Joining keeps the outer transition as the sole commit point.
                yield self
                return
            self._conn.execute("BEGIN IMMEDIATE")
            self._transaction_depth = 1
            try:
                yield self
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()
            finally:
                self._transaction_depth = 0

    @contextmanager
    def _write_scope(self) -> Iterator[None]:
        """Join an outer transaction or create a complete local mutation."""
        with self._lock:
            self._ensure_open()
            if self._transaction_depth:
                yield
                return
            self._conn.execute("BEGIN IMMEDIATE")
            self._transaction_depth = 1
            try:
                yield
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()
            finally:
                self._transaction_depth = 0

    def _apply_migrations(self) -> None:
        with self.transaction():
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT (
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    )
                ) STRICT
            """)
            applied = {
                int(row["version"])
                for row in self._conn.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
            unknown = [version for version in applied if version > SCHEMA_VERSION]
            if unknown:
                raise RuntimeError(
                    f"database schema {max(unknown)} is newer than supported "
                    f"schema {SCHEMA_VERSION}"
                )
            for version, name, script in _MIGRATIONS:
                if version in applied:
                    continue
                self._execute_script(script)
                self._conn.execute(
                    "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                    (version, name),
                )
                self._conn.execute(f"PRAGMA user_version={version}")
                logger.info("Applied SQLite graph schema migration %d: %s", version, name)

    def _execute_script(self, script: str) -> None:
        statement = ""
        for line in script.splitlines(keepends=True):
            statement += line
            if not sqlite3.complete_statement(statement):
                continue
            sql = statement.strip()
            statement = ""
            if sql:
                self._conn.execute(sql)
        if statement.strip():
            raise RuntimeError("incomplete SQLite migration statement")

    @property
    def schema_version(self) -> int:
        with self._lock:
            self._ensure_open()
            row = self._conn.execute("PRAGMA user_version").fetchone()
            return int(row[0])

    def get_agent_metadata(self, key: str, default: Any = None) -> Any:
        with self._lock:
            self._ensure_open()
            row = self._conn.execute(
                "SELECT value_json FROM agent_metadata WHERE key = ?", (key,)
            ).fetchone()
            return _json_loads(row[0], default) if row is not None else default

    def set_agent_metadata(self, key: str, value: Any) -> None:
        with self._write_scope():
            self._conn.execute(
                """
                INSERT INTO agent_metadata(key, value_json)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (key, _json_dumps(value)),
            )

    @staticmethod
    def _row_to_stored(row: sqlite3.Row) -> StoredNode:
        tags = _json_loads(row["tags_json"], [])
        return StoredNode(
            id=row["id"],
            node_type=row["node_type"],
            node_class=row["node_class"],
            config=_json_loads(row["config_json"], {}),
            status=row["status"],
            output=_json_loads(row["output_json"], None),
            output_type=row["output_type"],
            error=row["error"],
            perms=row["permissions"],
            tags=list(tags) if isinstance(tags, list) else [],
            producer_id=row["producer_node_id"],
        )

    def add_node(self, node: StoredNode) -> None:
        with self._write_scope():
            self._conn.execute(
                """
                INSERT INTO nodes(
                    id, node_type, node_class, config_json, permissions, tags_json,
                    producer_node_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.id,
                    node.node_type,
                    node.node_class,
                    _json_dumps(node.config),
                    node.perms,
                    _json_dumps(list(node.tags)),
                    node.producer_id,
                ),
            )
            self._conn.execute(
                """
                INSERT INTO node_state(
                    node_id, status, output_json, output_type, error
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    node.id,
                    node.status,
                    _json_dumps(node.output) if node.output is not None else None,
                    node.output_type,
                    node.error,
                ),
            )
            self._bump_graph_revision()
        logger.debug("Stored node: %s (%s)", node.id, node.node_type)

    def replace_node_definition(
        self,
        node: StoredNode,
        parents: List[str],
    ) -> None:
        with self._write_scope():
            cursor = self._conn.execute(
                """
                UPDATE nodes
                SET node_type = ?, node_class = ?, config_json = ?,
                    permissions = ?, tags_json = ?, producer_node_id = ?,
                    desired_revision = desired_revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (
                    node.node_type,
                    node.node_class,
                    _json_dumps(node.config),
                    node.perms,
                    _json_dumps(list(node.tags)),
                    node.producer_id,
                    node.id,
                ),
            )
            self._require_updated(cursor, node.id)
            self._conn.execute("DELETE FROM edges WHERE child_id = ?", (node.id,))
            for position, parent_id in enumerate(parents):
                self._conn.execute(
                    """
                    INSERT INTO edges(parent_id, child_id, position)
                    VALUES (?, ?, ?)
                    """,
                    (parent_id, node.id, position),
                )
            self._conn.execute(
                """
                UPDATE node_state
                SET status = 'pending', observed_revision = NULL,
                    output_type = NULL, output_json = NULL, error = NULL,
                    active_promise_id = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE node_id = ?
                """,
                (node.id,),
            )
            self._bump_graph_revision()
        logger.debug("Replaced generated node definition: %s", node.id)

    def has_node(self, node_id: str) -> bool:
        with self._lock:
            self._ensure_open()
            row = self._conn.execute(
                "SELECT 1 FROM nodes WHERE id = ?", (node_id,)
            ).fetchone()
            return row is not None

    def add_edge(self, parent_id: str, child_id: str) -> None:
        with self._write_scope():
            row = self._conn.execute(
                "SELECT COALESCE(MAX(position) + 1, 0) FROM edges WHERE child_id = ?",
                (child_id,),
            ).fetchone()
            position = int(row[0])
            self._conn.execute(
                "INSERT INTO edges(parent_id, child_id, position) VALUES (?, ?, ?)",
                (parent_id, child_id, position),
            )
            self._bump_desired_revision(child_id)
            self._bump_graph_revision()
        logger.debug("Stored edge: %s -> %s", parent_id, child_id)

    def remove_edge(self, parent_id: str, child_id: str) -> None:
        with self._write_scope():
            cursor = self._conn.execute(
                "DELETE FROM edges WHERE parent_id = ? AND child_id = ?",
                (parent_id, child_id),
            )
            if cursor.rowcount:
                self._bump_desired_revision(child_id)
                self._bump_graph_revision()
        logger.debug("Removed edge: %s -> %s", parent_id, child_id)

    def get_node(self, node_id: str) -> Optional[StoredNode]:
        with self._lock:
            self._ensure_open()
            row = self._conn.execute(
                _NODE_SELECT + " WHERE n.id = ?", (node_id,)
            ).fetchone()
            return self._row_to_stored(row) if row is not None else None

    def get_all_nodes(self) -> List[StoredNode]:
        with self._lock:
            self._ensure_open()
            rows = self._conn.execute(_NODE_SELECT + " ORDER BY n.id").fetchall()
            return [self._row_to_stored(row) for row in rows]

    def get_parents(self, node_id: str) -> List[str]:
        with self._lock:
            self._ensure_open()
            rows = self._conn.execute(
                """
                SELECT parent_id FROM edges
                WHERE child_id = ?
                ORDER BY position, parent_id
                """,
                (node_id,),
            ).fetchall()
            return [str(row[0]) for row in rows]

    def get_children(self, node_id: str) -> List[str]:
        with self._lock:
            self._ensure_open()
            rows = self._conn.execute(
                """
                SELECT child_id FROM edges
                WHERE parent_id = ?
                ORDER BY child_id
                """,
                (node_id,),
            ).fetchall()
            return [str(row[0]) for row in rows]

    def get_roots(self) -> List[str]:
        with self._lock:
            self._ensure_open()
            rows = self._conn.execute("""
                SELECT n.id
                FROM nodes AS n
                WHERE NOT EXISTS (
                    SELECT 1 FROM edges AS e WHERE e.child_id = n.id
                )
                ORDER BY n.id
            """).fetchall()
            return [str(row[0]) for row in rows]

    def update_status(
        self,
        node_id: str,
        status: str,
        output: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._write_scope():
            cursor = self._conn.execute(
                """
                UPDATE node_state
                SET status = ?, output_json = ?, error = ?,
                    observed_revision = CASE
                        WHEN ? IN ('success', 'success_skip') THEN (
                            SELECT desired_revision FROM nodes WHERE id = node_id
                        )
                        ELSE NULL
                    END,
                    active_promise_id = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE node_id = ?
                """,
                (
                    status,
                    _json_dumps(output) if output is not None else None,
                    error,
                    status,
                    node_id,
                ),
            )
            self._require_updated(cursor, node_id)

    def invalidate_descendants(self, node_id: str) -> int:
        with self._write_scope():
            rows = self._conn.execute(
                """
                WITH RECURSIVE descendants(id) AS (
                    SELECT child_id FROM edges WHERE parent_id = ?
                    UNION
                    SELECT e.child_id
                    FROM edges AS e
                    JOIN descendants AS d ON e.parent_id = d.id
                )
                SELECT d.id, s.active_promise_id,
                       p.graph_run_id, j.id AS job_id
                FROM descendants AS d
                JOIN node_state AS s ON s.node_id = d.id
                LEFT JOIN promises AS p ON p.id = s.active_promise_id
                LEFT JOIN jobs AS j ON j.promise_id = p.id
                ORDER BY d.id
                """,
                (node_id,),
            ).fetchall()
            ids = [str(row["id"]) for row in rows]
            active = [row for row in rows if row["active_promise_id"] is not None]
            if ids:
                self._conn.executemany(
                    """
                    UPDATE nodes
                    SET desired_revision = desired_revision + 1,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ?
                    """,
                    ((descendant_id,) for descendant_id in ids),
                )
                self._conn.executemany(
                    """
                    UPDATE node_state
                    SET status = 'pending', observed_revision = NULL,
                        output_type = NULL, output_json = NULL, error = NULL,
                        active_promise_id = NULL,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE node_id = ?
                    """,
                    ((descendant_id,) for descendant_id in ids),
                )
                self._bump_graph_revision()
            for row in active:
                promise_id = str(row["active_promise_id"])
                self._conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'superseded',
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE promise_id = ?
                      AND status NOT IN ('succeeded', 'failed', 'cancelled', 'superseded')
                    """,
                    (promise_id,),
                )
                self._conn.execute(
                    """
                    UPDATE promises
                    SET status = 'superseded',
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ? AND status NOT IN ('resolved', 'rejected', 'superseded')
                    """,
                    (promise_id,),
                )
                if row["job_id"] is not None:
                    self._insert_event(
                        "job.superseded", "job", str(row["job_id"]),
                        {"promise_id": promise_id}, outbox=True,
                    )
                if row["graph_run_id"] is not None:
                    self._insert_event(
                        "promise.superseded", "promise", promise_id,
                        {"job_id": row["job_id"], "run_id": row["graph_run_id"]},
                        outbox=True,
                    )
            return len(ids)

    def set_config(self, node_id: str, config: Dict[str, Any]) -> None:
        with self._write_scope():
            cursor = self._conn.execute(
                """
                UPDATE nodes
                SET config_json = ?, desired_revision = desired_revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (_json_dumps(config), node_id),
            )
            self._require_updated(cursor, node_id)
            self._bump_graph_revision()

    def set_runtime_config(
        self,
        node_id: str,
        config: Dict[str, Any],
    ) -> None:
        with self._write_scope():
            cursor = self._conn.execute(
                """
                UPDATE nodes
                SET config_json = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (_json_dumps(config), node_id),
            )
            self._require_updated(cursor, node_id)

    def set_perms(self, node_id: str, perms: str) -> None:
        with self._write_scope():
            cursor = self._conn.execute(
                """
                UPDATE nodes
                SET permissions = ?, desired_revision = desired_revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (perms, node_id),
            )
            self._require_updated(cursor, node_id)
            self._bump_graph_revision()

    def set_tags(self, node_id: str, tags: List[str]) -> None:
        with self._write_scope():
            cursor = self._conn.execute(
                """
                UPDATE nodes
                SET tags_json = ?, desired_revision = desired_revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (_json_dumps(list(tags)), node_id),
            )
            self._require_updated(cursor, node_id)
            self._bump_graph_revision()

    def get_desired_revision(self, node_id: str) -> int:
        with self._lock:
            self._ensure_open()
            row = self._conn.execute(
                "SELECT desired_revision FROM nodes WHERE id = ?", (node_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"node not found: {node_id}")
            return int(row[0])

    def get_graph_revision(self) -> int:
        with self._lock:
            self._ensure_open()
            row = self._conn.execute(
                "SELECT graph_revision FROM graph_metadata WHERE singleton = 1"
            ).fetchone()
            return int(row[0])

    def _bump_graph_revision(self) -> None:
        self._conn.execute(
            """
            UPDATE graph_metadata
            SET graph_revision = graph_revision + 1
            WHERE singleton = 1
            """
        )

    def _bump_desired_revision(self, node_id: str) -> None:
        cursor = self._conn.execute(
            """
            UPDATE nodes
            SET desired_revision = desired_revision + 1,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (node_id,),
        )
        self._require_updated(cursor, node_id)

    @staticmethod
    def _require_updated(cursor: sqlite3.Cursor, node_id: str) -> None:
        if cursor.rowcount == 0:
            raise KeyError(f"node not found: {node_id}")

    def remove_node(self, node_id: str) -> bool:
        with self._write_scope():
            cursor = self._conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            if cursor.rowcount:
                self._bump_graph_revision()
            return cursor.rowcount > 0

    def clear(self) -> None:
        with self._write_scope():
            self._conn.execute("DELETE FROM resource_locks")
            self._conn.execute("DELETE FROM job_attempts")
            self._conn.execute("DELETE FROM jobs")
            self._conn.execute("DELETE FROM graph_run_nodes")
            self._conn.execute("DELETE FROM graph_runs")
            self._conn.execute("DELETE FROM outbox")
            self._conn.execute("DELETE FROM events")
            self._conn.execute("DELETE FROM promises")
            self._conn.execute("DELETE FROM nodes")
            self._bump_graph_revision()

    # ---- durable graph runs / promises / jobs ------------------------------

    def create_graph_run(
        self,
        run_id: str,
        mode: str,
        nodes: List[Dict[str, Any]],
        edges: Optional[List[tuple[str, str]]] = None,
        pattern: Optional[str] = None,
        operation: str = "resolve",
    ) -> None:
        with self._write_scope():
            self._conn.execute(
                """
                INSERT INTO graph_runs(
                    id, status, desired_revision, mode, pattern, operation,
                    started_at, updated_at
                ) VALUES (
                    ?, 'running', ?, ?, ?, ?,
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                )
                """,
                (
                    run_id, self.get_graph_revision(), mode, pattern, operation,
                ),
            )
            self._conn.executemany(
                """
                INSERT INTO graph_run_nodes(
                    graph_run_id, node_id, desired_revision, status,
                    remaining_parents, phase
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        run_id,
                        node["node_id"],
                        node["desired_revision"],
                        node["status"],
                        node["remaining_parents"],
                        node["phase"],
                    )
                    for node in nodes
                ),
            )
            if edges:
                self._conn.executemany(
                    """
                    INSERT INTO graph_run_edges(
                        graph_run_id, parent_node_id, child_node_id
                    ) VALUES (?, ?, ?)
                    """,
                    ((run_id, parent, child) for parent, child in edges),
                )
            self._insert_event(
                "graph_run.created", "graph_run", run_id,
                {"mode": mode, "pattern": pattern, "operation": operation},
            )

    def get_graph_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._ensure_open()
            row = self._conn.execute(
                "SELECT * FROM graph_runs WHERE id = ?", (run_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def list_graph_runs(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_open()
            if status:
                rows = self._conn.execute(
                    """
                    SELECT * FROM graph_runs WHERE status = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (status, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM graph_runs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]

    def get_graph_run_nodes(self, run_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_open()
            rows = self._conn.execute(
                """
                SELECT * FROM graph_run_nodes
                WHERE graph_run_id = ? ORDER BY node_id
                """,
                (run_id,),
            ).fetchall()
            values = []
            for row in rows:
                value = dict(row)
                value["result"] = _json_loads(value.pop("result_json"), None)
                value["verification"] = _json_loads(
                    value.pop("verification_json"), None
                )
                value["error"] = _json_loads(value.pop("error_json"), None)
                values.append(value)
            return values

    def get_graph_run_edges(self, run_id: str) -> List[tuple[str, str]]:
        with self._lock:
            self._ensure_open()
            rows = self._conn.execute(
                """
                SELECT parent_node_id, child_node_id
                FROM graph_run_edges WHERE graph_run_id = ?
                ORDER BY parent_node_id, child_node_id
                """,
                (run_id,),
            ).fetchall()
            return [(str(row[0]), str(row[1])) for row in rows]

    def list_incomplete_graph_runs(self) -> List[str]:
        with self._lock:
            self._ensure_open()
            rows = self._conn.execute(
                """
                SELECT id FROM graph_runs
                WHERE status IN (
                    'running', 'waiting', 'pause_requested', 'paused',
                    'cancel_requested'
                )
                ORDER BY created_at
                """
            ).fetchall()
            return [str(row[0]) for row in rows]

    def set_graph_run_status(
        self,
        run_id: str,
        status: str,
        error: Optional[Dict[str, Any]] = None,
    ) -> None:
        terminal = status in {"succeeded", "failed", "cancelled", "superseded"}
        with self._write_scope():
            cursor = self._conn.execute(
                """
                UPDATE graph_runs
                SET status = ?, error_json = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    completed_at = CASE WHEN ? THEN
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        ELSE completed_at END
                WHERE id = ?
                """,
                (
                    status,
                    _json_dumps(error) if error is not None else None,
                    1 if terminal else 0,
                    run_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"graph run not found: {run_id}")
            self._insert_event(
                f"graph_run.{status}", "graph_run", run_id,
                {"status": status, "error": error},
            )

    def set_run_node_verification(
        self,
        run_id: str,
        node_id: str,
        envelope: Dict[str, Any],
    ) -> None:
        """Record verification and make the node ready for resolve."""
        with self._write_scope():
            self._conn.execute(
                """
                UPDATE graph_run_nodes
                SET status = 'ready', phase = 'resolve', promise_id = NULL,
                    verification_json = ?
                WHERE graph_run_id = ? AND node_id = ?
                """,
                (_json_dumps(envelope), run_id, node_id),
            )
            self._conn.execute(
                """
                UPDATE node_state
                SET status = 'pending', active_promise_id = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE node_id = ?
                """,
                (node_id,),
            )
            self._insert_event(
                "graph_run_node.verified", "node", node_id,
                {"run_id": run_id, "status": envelope.get("status")},
            )

    def set_run_node_result(
        self,
        run_id: str,
        node_id: str,
        desired_revision: int,
        envelope: Dict[str, Any],
        *,
        delete_node_on_success: bool = False,
        run_status: Optional[str] = None,
    ) -> str:
        """Publish a result and distinguish node state from run scheduling."""
        with self._write_scope():
            row = self._conn.execute(
                "SELECT desired_revision FROM nodes WHERE id = ?", (node_id,)
            ).fetchone()
            if row is None or int(row[0]) != int(desired_revision):
                node_status = "superseded"
                recorded_status = "superseded"
            else:
                node_status = str(envelope["status"])
                recorded_status = run_status or node_status
                observed_revision = (
                    desired_revision
                    if node_status in {"success", "success_skip"}
                    else None
                )
                self._conn.execute(
                    """
                    UPDATE node_state
                    SET status = ?, observed_revision = ?, output_type = ?,
                        output_json = ?, error = ?, active_promise_id = NULL,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE node_id = ?
                    """,
                    (
                        node_status,
                        observed_revision,
                        envelope.get("output_type"),
                        _json_dumps(envelope.get("output"))
                        if envelope.get("output") is not None else None,
                        envelope.get("error"),
                        node_id,
                    ),
                )
            self._conn.execute(
                """
                UPDATE graph_run_nodes
                SET status = ?, promise_id = NULL, result_json = ?,
                    error_json = ?
                WHERE graph_run_id = ? AND node_id = ?
                """,
                (
                    recorded_status,
                    _json_dumps(envelope),
                    _json_dumps({"message": envelope.get("error")})
                    if envelope.get("error") else None,
                    run_id,
                    node_id,
                ),
            )
            if (
                delete_node_on_success
                and node_status in {"success", "success_skip"}
            ):
                self._conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
                self._bump_graph_revision()
            self._insert_event(
                "graph_run_node.completed", "node", node_id,
                {
                    "run_id": run_id,
                    "status": recorded_status,
                    "node_status": node_status,
                },
            )
            return recorded_status

    def sync_graph_run(
        self,
        run_id: str,
        rows: List[Dict[str, Any]],
        edges: List[tuple[str, str]],
        removed_node_ids: List[str],
    ) -> None:
        """Synchronize a running ledger after a produced-subgraph mutation."""
        with self._write_scope():
            if removed_node_ids:
                placeholders = ",".join("?" for _ in removed_node_ids)
                self._conn.execute(
                    f"""
                    DELETE FROM graph_run_nodes
                    WHERE graph_run_id = ? AND node_id IN ({placeholders})
                    """,
                    (run_id, *removed_node_ids),
                )
            for row in rows:
                existing = self._conn.execute(
                    """
                    SELECT status FROM graph_run_nodes
                    WHERE graph_run_id = ? AND node_id = ?
                    """,
                    (run_id, row["node_id"]),
                ).fetchone()
                if existing is None:
                    self._conn.execute(
                        """
                        INSERT INTO graph_run_nodes(
                            graph_run_id, node_id, desired_revision, status,
                            remaining_parents, phase
                        ) VALUES (?, ?, ?, ?, ?, 'verify')
                        """,
                        (
                            run_id,
                            row["node_id"],
                            row["desired_revision"],
                            row["status"],
                            row["remaining_parents"],
                        ),
                    )
                    continue
                if existing["status"] in {"waiting", "running"}:
                    raise RuntimeError(
                        "produced graph mutation attempted to replace an "
                        f"active run node: {row['node_id']}"
                    )
                self._conn.execute(
                    """
                    UPDATE graph_run_nodes
                    SET desired_revision = ?, status = ?,
                        remaining_parents = ?, phase = 'verify',
                        promise_id = NULL, result_json = NULL,
                        error_json = NULL, verification_json = NULL
                    WHERE graph_run_id = ? AND node_id = ?
                    """,
                    (
                        row["desired_revision"],
                        row["status"],
                        row["remaining_parents"],
                        run_id,
                        row["node_id"],
                    ),
                )

            self._conn.execute(
                "DELETE FROM graph_run_edges WHERE graph_run_id = ?",
                (run_id,),
            )
            if edges:
                self._conn.executemany(
                    """
                    INSERT INTO graph_run_edges(
                        graph_run_id, parent_node_id, child_node_id
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        (run_id, parent_id, child_id)
                        for parent_id, child_id in edges
                    ),
                )
            self._conn.execute(
                """
                UPDATE graph_runs
                SET desired_revision = (
                        SELECT graph_revision FROM graph_metadata
                        WHERE singleton = 1
                    ),
                    status = 'running',
                    completed_at = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (run_id,),
            )

    def set_run_node_status(
        self,
        run_id: str,
        node_id: str,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        with self._write_scope():
            self._conn.execute(
                """
                UPDATE graph_run_nodes
                SET status = ?, error_json = ?,
                    promise_id = CASE WHEN ? THEN NULL ELSE promise_id END
                WHERE graph_run_id = ? AND node_id = ?
                """,
                (
                    status,
                    _json_dumps({"message": error}) if error else None,
                    1 if status in {
                        "success", "success_skip", "error", "blocked",
                        "skipped", "pending_reboot", "cancelled", "superseded",
                    } else 0,
                    run_id,
                    node_id,
                ),
            )

    def clear_node_promise(
        self,
        node_id: str,
        status: str = "pending",
        error: Optional[str] = None,
    ) -> None:
        with self._write_scope():
            self._conn.execute(
                """
                UPDATE node_state
                SET status = ?, error = ?, active_promise_id = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE node_id = ?
                """,
                (status, error, node_id),
            )

    def create_job_request(
        self,
        *,
        job_id: str,
        promise_id: str,
        run_id: str,
        node_id: str,
        desired_revision: int,
        input_fingerprint: str,
        operation_phase: str,
        idempotency_key: str,
        executor: str,
        cancellation_kind: str,
        pause_kind: str,
        progress_kind: str,
        checkpoint_kind: str,
        spec: Dict[str, Any],
        max_attempts: int,
        timeout_seconds: Optional[float],
        reuse_terminal: bool = False,
    ) -> str:
        """Atomically create a Promise, Job and outbox dispatch request."""
        with self._write_scope():
            existing = self._conn.execute(
                """
                SELECT j.promise_id, p.status
                FROM jobs AS j
                JOIN promises AS p ON p.id = j.promise_id
                WHERE j.idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            effective_key = idempotency_key
            if existing is not None:
                existing_promise = str(existing["promise_id"])
                existing_status = str(existing["status"])
                if existing_status == "pending" or reuse_terminal:
                    self._conn.execute(
                        """
                        UPDATE graph_run_nodes
                        SET status = 'waiting', promise_id = ?
                        WHERE graph_run_id = ? AND node_id = ?
                        """,
                        (existing_promise, run_id, node_id),
                    )
                    if existing_status != "pending":
                        self._insert_event(
                            f"promise.{existing_status}",
                            "promise",
                            existing_promise,
                            {"run_id": run_id, "reused": True},
                            outbox=True,
                        )
                    return existing_promise
                effective_key = f"{idempotency_key}:{job_id}"

            self._conn.execute(
                """
                INSERT INTO promises(
                    id, kind, status, graph_run_id, node_id,
                    desired_revision, input_fingerprint, updated_at,
                    producer, cancellation_kind, pause_kind,
                    progress_kind, checkpoint_kind, operation_phase
                ) VALUES (
                    ?, 'job', 'pending', ?, ?, ?, ?,
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    promise_id, run_id, node_id, desired_revision,
                    input_fingerprint, executor, cancellation_kind,
                    pause_kind, progress_kind, checkpoint_kind,
                    operation_phase,
                ),
            )
            self._conn.execute(
                """
                INSERT INTO jobs(
                    id, promise_id, graph_run_id, node_id,
                    desired_revision, idempotency_key, executor, spec_json,
                    status, max_attempts, timeout_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    job_id, promise_id, run_id, node_id, desired_revision,
                    effective_key, executor, _json_dumps(spec),
                    max_attempts, timeout_seconds,
                ),
            )
            self._conn.execute(
                """
                UPDATE graph_run_nodes
                SET status = 'waiting', promise_id = ?
                WHERE graph_run_id = ? AND node_id = ?
                """,
                (promise_id, run_id, node_id),
            )
            self._conn.execute(
                """
                UPDATE node_state
                SET status = 'running', active_promise_id = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE node_id = ?
                """,
                (promise_id, node_id),
            )
            self._insert_event(
                "job.requested", "job", job_id,
                {"promise_id": promise_id, "executor": executor},
                outbox=True,
            )
            return promise_id

    def get_promise(self, promise_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._ensure_open()
            row = self._conn.execute(
                "SELECT * FROM promises WHERE id = ?", (promise_id,)
            ).fetchone()
            if row is None:
                return None
            value = dict(row)
            value["result"] = _json_loads(value.pop("result_json"), None)
            value["error"] = _json_loads(value.pop("error_json"), None)
            return value

    def list_promises(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_open()
            if status:
                rows = self._conn.execute(
                    """
                    SELECT id FROM promises WHERE status = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (status, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id FROM promises ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self.get_promise(str(row[0])) for row in rows]

    def get_runs_waiting_on_promise(self, promise_id: str) -> List[str]:
        with self._lock:
            self._ensure_open()
            rows = self._conn.execute(
                """
                SELECT graph_run_id FROM graph_run_nodes
                WHERE promise_id = ? AND status = 'waiting'
                ORDER BY graph_run_id
                """,
                (promise_id,),
            ).fetchall()
            return [str(row[0]) for row in rows]

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._ensure_open()
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            value = dict(row)
            value["spec"] = _json_loads(value.pop("spec_json"), {})
            value["checkpoint"] = _json_loads(value.pop("checkpoint_json"), None)
            return value

    def list_jobs(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_open()
            if status:
                rows = self._conn.execute(
                    """
                    SELECT id FROM jobs WHERE status = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (status, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id FROM jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self.get_job(str(row[0])) for row in rows]

    def get_job_for_promise(self, promise_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._ensure_open()
            row = self._conn.execute(
                "SELECT id FROM jobs WHERE promise_id = ?", (promise_id,)
            ).fetchone()
            return self.get_job(str(row[0])) if row is not None else None

    def recover_incomplete_jobs(self) -> None:
        """Requeue interrupted work and finish acknowledged cancellations."""
        with self._write_scope():
            rows = self._conn.execute(
                """
                SELECT id, promise_id, graph_run_id, node_id, status,
                       attempt, max_attempts
                FROM jobs WHERE status IN ('running', 'cancel_requested')
                """
            ).fetchall()
            self._conn.execute(
                "DELETE FROM resource_locks WHERE job_id IN (SELECT id FROM jobs WHERE status IN ('running', 'cancel_requested'))"
            )
            for row in rows:
                self._conn.execute(
                    """
                    UPDATE job_attempts SET status = 'interrupted',
                        finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE job_id = ? AND status = 'running'
                    """,
                    (row["id"],),
                )
                if row["status"] == "cancel_requested":
                    self._conn.execute(
                        """
                        UPDATE jobs SET status = 'cancelled', lease_owner = NULL,
                            lease_expires_at = NULL,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE id = ?
                        """,
                        (row["id"],),
                    )
                    self._conn.execute(
                        """
                        UPDATE promises SET status = 'cancelled',
                            resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE id = ? AND status = 'pending'
                        """,
                        (row["promise_id"],),
                    )
                    self._insert_event(
                        "promise.cancelled", "promise", str(row["promise_id"]),
                        {"job_id": row["id"], "run_id": row["graph_run_id"]},
                        outbox=True,
                    )
                elif int(row["attempt"]) >= int(row["max_attempts"]):
                    error = {
                        "type": "WorkerInterrupted",
                        "message": "worker stopped before the job completed",
                    }
                    self._conn.execute(
                        """
                        UPDATE jobs SET status = 'failed', lease_owner = NULL,
                            lease_expires_at = NULL,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE id = ?
                        """,
                        (row["id"],),
                    )
                    self._conn.execute(
                        """
                        UPDATE promises SET status = 'rejected', error_json = ?,
                            resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE id = ? AND status = 'pending'
                        """,
                        (_json_dumps(error), row["promise_id"]),
                    )
                    self._insert_event(
                        "promise.rejected", "promise", str(row["promise_id"]),
                        {"job_id": row["id"], "run_id": row["graph_run_id"]},
                        outbox=True,
                    )
                else:
                    self._conn.execute(
                        """
                        UPDATE jobs SET status = 'queued', lease_owner = NULL,
                            lease_expires_at = NULL,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE id = ?
                        """,
                        (row["id"],),
                    )
                    self._insert_event(
                        "job.requested", "job", str(row["id"]),
                        {"promise_id": row["promise_id"], "recovered": True},
                        outbox=True,
                    )

    def claim_outbox(
        self,
        owner: str,
        lease_expires_at: str,
        limit: int = 16,
    ) -> List[Dict[str, Any]]:
        with self._write_scope():
            rows = self._conn.execute(
                """
                SELECT * FROM outbox
                WHERE dispatched_at IS NULL
                  AND (next_attempt_at IS NULL OR next_attempt_at <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                  AND (lease_expires_at IS NULL OR lease_expires_at <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                ORDER BY sequence LIMIT ?
                """,
                (limit,),
            ).fetchall()
            sequences = [int(row["sequence"]) for row in rows]
            self._conn.executemany(
                """
                UPDATE outbox
                SET lease_owner = ?, lease_expires_at = ?, attempts = attempts + 1
                WHERE sequence = ?
                """,
                ((owner, lease_expires_at, sequence) for sequence in sequences),
            )
            values = []
            for row in rows:
                value = dict(row)
                value["payload"] = _json_loads(value.pop("payload_json"), {})
                values.append(value)
            return values

    def mark_outbox_dispatched(self, sequence: int, owner: str) -> None:
        with self._write_scope():
            self._conn.execute(
                """
                UPDATE outbox
                SET dispatched_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE sequence = ? AND lease_owner = ?
                """,
                (sequence, owner),
            )

    def release_outbox_claim(
        self,
        sequence: int,
        owner: str,
        next_attempt_at: str,
    ) -> None:
        with self._write_scope():
            self._conn.execute(
                """
                UPDATE outbox
                SET lease_owner = NULL, lease_expires_at = NULL,
                    next_attempt_at = ?
                WHERE sequence = ? AND lease_owner = ?
                """,
                (next_attempt_at, sequence, owner),
            )

    def claim_job(
        self,
        job_id: str,
        worker_id: str,
        lease_expires_at: str,
    ) -> Optional[Dict[str, Any]]:
        with self._write_scope():
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None or row["status"] != "queued":
                return None
            spec = _json_loads(row["spec_json"], {})
            resources = list(spec.get("resources") or [])
            self._conn.execute(
                """
                DELETE FROM resource_locks
                WHERE lease_expires_at <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """
            )
            for resource in resources:
                conflict = self._conn.execute(
                    "SELECT job_id FROM resource_locks WHERE resource_key = ?",
                    (resource,),
                ).fetchone()
                if conflict is not None:
                    return None
            attempt = int(row["attempt"]) + 1
            self._conn.execute(
                """
                UPDATE jobs
                SET status = 'running', attempt = ?, lease_owner = ?,
                    lease_expires_at = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = 'queued'
                """,
                (attempt, worker_id, lease_expires_at, job_id),
            )
            self._conn.execute(
                """
                INSERT INTO job_attempts(job_id, attempt, worker_id, status)
                VALUES (?, ?, ?, 'running')
                """,
                (job_id, attempt, worker_id),
            )
            self._conn.executemany(
                """
                INSERT INTO resource_locks(
                    resource_key, job_id, lease_owner, lease_expires_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    (resource, job_id, worker_id, lease_expires_at)
                    for resource in resources
                ),
            )
            return self.get_job(job_id)

    def heartbeat_worker(self, worker_id: str, lease_expires_at: str) -> None:
        with self._write_scope():
            self._conn.execute(
                """
                UPDATE jobs SET lease_expires_at = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE lease_owner = ?
                  AND status IN ('running', 'cancel_requested', 'superseded')
                """,
                (lease_expires_at, worker_id),
            )
            self._conn.execute(
                """
                UPDATE resource_locks SET lease_expires_at = ?
                WHERE lease_owner = ?
                """,
                (lease_expires_at, worker_id),
            )

    def update_job_progress(
        self,
        job_id: str,
        worker_id: str,
        progress: float,
        checkpoint: Optional[Dict[str, Any]],
        lease_expires_at: str,
    ) -> bool:
        with self._write_scope():
            cursor = self._conn.execute(
                """
                UPDATE jobs
                SET progress = ?, checkpoint_json = ?, lease_expires_at = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND lease_owner = ?
                  AND status IN ('running', 'cancel_requested', 'superseded')
                """,
                (
                    progress,
                    _json_dumps(checkpoint) if checkpoint is not None else None,
                    lease_expires_at,
                    job_id,
                    worker_id,
                ),
            )
            if cursor.rowcount:
                self._conn.execute(
                    """
                    UPDATE resource_locks SET lease_expires_at = ?
                    WHERE job_id = ? AND lease_owner = ?
                    """,
                    (lease_expires_at, job_id, worker_id),
                )
            return cursor.rowcount > 0

    def complete_job(
        self,
        job_id: str,
        worker_id: str,
        envelope: Dict[str, Any],
    ) -> Dict[str, Any]:
        with self._write_scope():
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"job not found: {job_id}")
            if row["lease_owner"] != worker_id:
                raise RuntimeError(f"worker {worker_id} does not own job {job_id}")
            node = self._conn.execute(
                "SELECT desired_revision FROM nodes WHERE id = ?",
                (row["node_id"],),
            ).fetchone()
            current = node is not None and int(node[0]) == int(row["desired_revision"])
            promise_status = "resolved" if current else "superseded"
            job_status = "succeeded" if current else "superseded"
            self._conn.execute(
                """
                UPDATE jobs SET status = ?, progress = 1,
                    lease_owner = NULL, lease_expires_at = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (job_status, job_id),
            )
            self._conn.execute(
                """
                UPDATE job_attempts SET status = ?,
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE job_id = ? AND attempt = ?
                """,
                (job_status, job_id, row["attempt"]),
            )
            self._conn.execute(
                """
                UPDATE promises
                SET status = ?, result_type = ?, result_json = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = 'pending'
                """,
                (
                    promise_status,
                    envelope.get("output_type"),
                    _json_dumps(envelope),
                    row["promise_id"],
                ),
            )
            self._conn.execute(
                "DELETE FROM resource_locks WHERE job_id = ?", (job_id,)
            )
            self._insert_event(
                f"promise.{promise_status}", "promise", str(row["promise_id"]),
                {"job_id": job_id, "run_id": row["graph_run_id"]},
                outbox=True,
            )
            return {
                "status": promise_status,
                "promise_id": str(row["promise_id"]),
                "run_id": str(row["graph_run_id"]),
                "node_id": str(row["node_id"]),
            }

    def fail_job(
        self,
        job_id: str,
        worker_id: str,
        error: Dict[str, Any],
        retryable: bool,
    ) -> Dict[str, Any]:
        with self._write_scope():
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"job not found: {job_id}")
            if row["lease_owner"] != worker_id:
                raise RuntimeError(f"worker {worker_id} does not own job {job_id}")
            retry = (
                retryable
                and row["status"] != "cancel_requested"
                and int(row["attempt"]) < int(row["max_attempts"])
            )
            next_status = "queued" if retry else (
                "cancelled" if row["status"] == "cancel_requested"
                else "superseded" if row["status"] == "superseded"
                else "failed"
            )
            self._conn.execute(
                """
                UPDATE job_attempts SET status = ?, error_json = ?,
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE job_id = ? AND attempt = ?
                """,
                (next_status, _json_dumps(error), job_id, row["attempt"]),
            )
            self._conn.execute(
                """
                UPDATE jobs SET status = ?, lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (next_status, job_id),
            )
            if retry:
                self._insert_event(
                    "job.requested", "job", job_id,
                    {"promise_id": row["promise_id"], "retry": True},
                    outbox=True,
                )
            else:
                promise_status = (
                    "cancelled" if next_status == "cancelled"
                    else "superseded" if next_status == "superseded"
                    else "rejected"
                )
                self._conn.execute(
                    """
                    UPDATE promises SET status = ?, error_json = ?,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ? AND status = 'pending'
                    """,
                    (promise_status, _json_dumps(error), row["promise_id"]),
                )
                self._insert_event(
                    f"promise.{promise_status}", "promise", str(row["promise_id"]),
                    {"job_id": job_id, "run_id": row["graph_run_id"]},
                    outbox=True,
                )
            self._conn.execute(
                "DELETE FROM resource_locks WHERE job_id = ?", (job_id,)
            )
            return {
                "status": next_status,
                "promise_id": str(row["promise_id"]),
                "run_id": str(row["graph_run_id"]),
                "node_id": str(row["node_id"]),
            }

    def request_job_cancellation(self, job_id: str) -> str:
        with self._write_scope():
            row = self._conn.execute(
                """
                SELECT promise_id, status, graph_run_id, node_id
                FROM jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"job not found: {job_id}")
            status = str(row["status"])
            if status == "queued":
                self._conn.execute(
                    """
                    UPDATE jobs SET status = 'cancelled',
                        cancel_requested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ?
                    """,
                    (job_id,),
                )
                self._conn.execute(
                    """
                    UPDATE promises SET status = 'cancelled',
                        cancel_requested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ? AND status = 'pending'
                    """,
                    (row["promise_id"],),
                )
                self._insert_event(
                    "promise.cancelled", "promise", str(row["promise_id"]),
                    {"job_id": job_id, "run_id": row["graph_run_id"]},
                    outbox=True,
                )
                return "cancelled"
            if status == "running":
                self._conn.execute(
                    """
                    UPDATE jobs SET status = 'cancel_requested',
                        cancel_requested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ?
                    """,
                    (job_id,),
                )
                self._insert_event(
                    "job.cancel_requested", "job", job_id,
                    {"promise_id": row["promise_id"]}, outbox=True,
                )
                return "cancel_requested"
            return status

    def is_removal_pending(self, node_id: str) -> bool:
        with self._lock:
            self._ensure_open()
            row = self._conn.execute(
                """
                SELECT 1
                FROM graph_run_nodes AS rn
                JOIN graph_runs AS r ON r.id = rn.graph_run_id
                WHERE rn.node_id = ?
                  AND r.operation = 'remove'
                  AND r.status IN (
                      'running', 'waiting', 'pause_requested', 'paused',
                      'cancel_requested'
                  )
                  AND rn.status IN ('pending', 'ready', 'waiting', 'running')
                LIMIT 1
                """,
                (node_id,),
            ).fetchone()
            return row is not None

    def request_graph_run_pause(self, run_id: str) -> str:
        with self._write_scope():
            run = self._conn.execute(
                "SELECT status FROM graph_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"graph run not found: {run_id}")
            status = str(run["status"])
            if status in {
                "succeeded", "failed", "cancelled", "superseded",
                "paused", "cancel_requested",
            }:
                return status
            self._conn.execute(
                """
                UPDATE graph_runs SET status = 'pause_requested',
                    pause_requested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (run_id,),
            )
            self._insert_event(
                "graph_run.pause_requested", "graph_run", run_id, {}
            )
            return "pause_requested"

    def mark_graph_run_paused(self, run_id: str) -> None:
        with self._write_scope():
            self._conn.execute(
                """
                UPDATE graph_runs SET status = 'paused',
                    paused_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = 'pause_requested'
                """,
                (run_id,),
            )
            self._insert_event("graph_run.paused", "graph_run", run_id, {})

    def resume_graph_run(self, run_id: str) -> str:
        with self._write_scope():
            run = self._conn.execute(
                "SELECT status FROM graph_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"graph run not found: {run_id}")
            status = str(run["status"])
            if status != "paused":
                return status
            self._conn.execute(
                """
                UPDATE graph_runs SET status = 'running',
                    pause_requested_at = NULL, paused_at = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (run_id,),
            )
            self._insert_event("graph_run.resumed", "graph_run", run_id, {})
            return "running"

    def request_graph_run_cancellation(self, run_id: str) -> str:
        with self._write_scope():
            run = self._conn.execute(
                "SELECT status FROM graph_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"graph run not found: {run_id}")
            if run["status"] in {"succeeded", "failed", "cancelled", "superseded"}:
                return str(run["status"])
            self._conn.execute(
                """
                UPDATE graph_runs SET status = 'cancel_requested',
                    cancel_requested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (run_id,),
            )
            self._conn.execute(
                """
                UPDATE graph_run_nodes SET status = 'cancelled'
                WHERE graph_run_id = ? AND status IN ('pending', 'ready')
                """,
                (run_id,),
            )
            jobs = self._conn.execute(
                """
                SELECT id FROM jobs
                WHERE graph_run_id = ?
                  AND status IN ('queued', 'running')
                """,
                (run_id,),
            ).fetchall()
            for job in jobs:
                self.request_job_cancellation(str(job[0]))
            return "cancel_requested"

    def _insert_event(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Dict[str, Any],
        *,
        outbox: bool = False,
    ) -> str:
        event_id = str(uuid.uuid4())
        encoded = _json_dumps(payload)
        self._conn.execute(
            """
            INSERT INTO events(
                event_id, event_type, aggregate_type, aggregate_id, payload_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (event_id, event_type, aggregate_type, aggregate_id, encoded),
        )
        if outbox:
            self._conn.execute(
                """
                INSERT INTO outbox(
                    event_id, event_type, aggregate_type, aggregate_id,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (event_id, event_type, aggregate_type, aggregate_id, encoded),
            )
        return event_id

    def list_events(
        self,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        event_type: Optional[str] = None,
        aggregate_type: Optional[str] = None,
        aggregate_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            self._ensure_open()
            rows = self._conn.execute(
                """
                SELECT sequence, event_id, event_type, aggregate_type,
                       aggregate_id, payload_json, created_at
                FROM events
                WHERE sequence > ?
                  AND (? IS NULL OR event_type = ?)
                  AND (? IS NULL OR aggregate_type = ?)
                  AND (? IS NULL OR aggregate_id = ?)
                ORDER BY sequence ASC
                LIMIT ?
                """,
                (
                    max(0, int(after_sequence)),
                    event_type,
                    event_type,
                    aggregate_type,
                    aggregate_type,
                    aggregate_id,
                    aggregate_id,
                    limit,
                ),
            ).fetchall()
            return [
                {
                    "sequence": int(row["sequence"]),
                    "event_id": str(row["event_id"]),
                    "event_type": str(row["event_type"]),
                    "aggregate_type": str(row["aggregate_type"]),
                    "aggregate_id": str(row["aggregate_id"]),
                    "payload": _json_loads(row["payload_json"], {}),
                    "created_at": str(row["created_at"]),
                }
                for row in rows
            ]

    def backup_to(self, destination: str) -> None:
        target = Path(destination)
        if target.resolve() == Path(self.db_path).resolve():
            raise ValueError("backup destination must differ from the live database")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        with self._lock:
            self._ensure_open()
            backup = sqlite3.connect(str(target))
            try:
                self._conn.backup(backup)
            finally:
                backup.close()

    def _release_ownership(self) -> None:
        ownership_file = getattr(self, "_ownership_file", None)
        if ownership_file is None or ownership_file.closed:
            return
        fcntl.flock(ownership_file.fileno(), fcntl.LOCK_UN)
        ownership_file.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._transaction_depth:
                raise RuntimeError("cannot close graph store during a transaction")
            self._conn.close()
            self._release_ownership()
            self._closed = True
