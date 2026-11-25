"""SQLite graph persistence and transaction semantics."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zeropoint_agent.dag import DAG  # noqa: E402
from zeropoint_agent.graph_store import StoredNode  # noqa: E402
from zeropoint_agent.graph_transaction import graph_transaction  # noqa: E402
from zeropoint_agent.nodes.config.namespace import (  # noqa: E402
    Namespace,
    NamespaceResult,
)
from zeropoint_agent.sqlite_store import (  # noqa: E402
    SCHEMA_VERSION,
    SQLiteGraphStore,
    _MIGRATION_1,
)


class SQLiteGraphStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "state.db"
        self.store = SQLiteGraphStore(str(self.db_path))

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    @staticmethod
    def node(node_id: str, **kwargs) -> StoredNode:
        return StoredNode(
            id=node_id,
            node_type=kwargs.pop("node_type", "TestNode"),
            node_class=kwargs.pop("node_class", "tests.TestNode"),
            config=kwargs.pop("config", {"name": node_id}),
            **kwargs,
        )

    def test_schema_includes_future_execution_ledger(self):
        self.assertEqual(self.store.schema_version, SCHEMA_VERSION)
        with sqlite3.connect(self.db_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertTrue({
            "nodes",
            "edges",
            "agent_metadata",
            "graph_metadata",
            "node_state",
            "graph_runs",
            "graph_run_nodes",
            "graph_run_edges",
            "promises",
            "jobs",
            "job_attempts",
            "resource_locks",
            "events",
            "outbox",
            "schema_migrations",
        }.issubset(tables))

    def test_round_trip_nodes_edges_and_empty_output(self):
        self.store.add_node(self.node("root", perms="rwd", tags=["system"]))
        self.store.add_node(self.node("child"))
        self.store.add_node(self.node("grandchild"))
        self.store.add_edge("root", "child")
        self.store.add_edge("child", "grandchild")

        self.assertEqual(self.store.get_roots(), ["root"])
        self.assertEqual(self.store.get_parents("child"), ["root"])
        self.assertEqual(self.store.get_children("child"), ["grandchild"])

        self.store.update_status("child", "success", output={})
        child = self.store.get_node("child")
        self.assertIsNotNone(child)
        self.assertEqual(child.output, {})
        self.assertEqual(child.status, "success")

        self.store.update_status("grandchild", "success", output={"value": 1})
        child_revision = self.store.get_desired_revision("child")
        grandchild_revision = self.store.get_desired_revision("grandchild")
        self.assertEqual(self.store.invalidate_descendants("root"), 2)
        self.assertEqual(self.store.get_node("child").status, "pending")
        self.assertIsNone(self.store.get_node("grandchild").output)
        self.assertEqual(
            self.store.get_desired_revision("child"), child_revision + 1
        )
        self.assertEqual(
            self.store.get_desired_revision("grandchild"),
            grandchild_revision + 1,
        )

    def test_store_has_single_process_owner(self):
        with self.assertRaisesRegex(RuntimeError, "already owned"):
            SQLiteGraphStore(str(self.db_path))

    def test_parent_order_is_stable(self):
        for node_id in ("p1", "p2", "p3", "child"):
            self.store.add_node(self.node(node_id))
        self.store.add_edge("p2", "child")
        self.store.add_edge("p1", "child")
        self.store.add_edge("p3", "child")
        self.assertEqual(self.store.get_parents("child"), ["p2", "p1", "p3"])

    def test_desired_revision_changes_with_intent(self):
        self.store.add_node(self.node("root"))
        self.store.add_node(self.node("child"))
        initial = self.store.get_desired_revision("child")
        graph_revision = self.store.get_graph_revision()

        self.store.set_config("child", {"name": "updated"})
        self.store.set_perms("child", "rw-")
        self.store.set_tags("child", ["input"])
        self.store.add_edge("root", "child")
        self.assertEqual(
            self.store.get_desired_revision("child"),
            initial + 4,
        )
        self.assertEqual(self.store.get_graph_revision(), graph_revision + 4)

    def test_store_transaction_rolls_back_every_statement(self):
        with self.assertRaisesRegex(RuntimeError, "abort"):
            with self.store.transaction():
                self.store.add_node(self.node("root"))
                self.store.add_node(self.node("child"))
                self.store.add_edge("root", "child")
                raise RuntimeError("abort")

        self.assertFalse(self.store.has_node("root"))
        self.assertFalse(self.store.has_node("child"))

    def test_graph_transaction_reloads_memory_after_rollback(self):
        dag = DAG(store=self.store)
        with self.assertRaisesRegex(RuntimeError, "abort"):
            with graph_transaction(dag):
                dag.add("root", Namespace(name="root"), perms="rwd")
                self.assertIn("root", dag.nodes)
                raise RuntimeError("abort")

        self.assertNotIn("root", dag.nodes)
        self.assertFalse(self.store.has_node("root"))

    def test_direct_dag_add_keeps_memory_and_storage_atomic(self):
        dag = DAG(store=self.store)
        dag.add("root", Namespace(name="root"), perms="rwd")
        with patch.object(
            self.store, "add_edge", side_effect=RuntimeError("edge failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "edge failed"):
                dag.add(
                    "root/child",
                    Namespace(name="child"),
                    parents=["root"],
                )

        self.assertNotIn("root/child", dag.nodes)
        self.assertFalse(self.store.has_node("root/child"))

    def test_schema_one_upgrades_to_current(self):
        old_path = Path(self.tempdir.name) / "schema-one.db"
        with sqlite3.connect(old_path) as connection:
            connection.executescript(_MIGRATION_1)
            connection.execute("""
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT (
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    )
                ) STRICT
            """)
            connection.execute(
                "INSERT INTO schema_migrations(version, name) VALUES (1, 'initial')"
            )
            connection.execute("PRAGMA user_version=1")

        upgraded = SQLiteGraphStore(str(old_path))
        try:
            self.assertEqual(upgraded.schema_version, SCHEMA_VERSION)
            with sqlite3.connect(old_path) as connection:
                promise_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(promises)")
                }
                run_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(graph_runs)")
                }
            self.assertIn("input_fingerprint", promise_columns)
            self.assertIn("producer", promise_columns)
            self.assertIn("cancel_requested_at", run_columns)
            self.assertIn("operation", run_columns)
            with sqlite3.connect(old_path) as connection:
                agent_table = connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = 'agent_metadata'
                    """
                ).fetchone()[0]
            self.assertEqual(agent_table, 1)
        finally:
            upgraded.close()

    def test_dag_rehydrates_typed_outputs(self):
        self.store.add_node(StoredNode(
            id="root",
            node_type="Namespace",
            node_class="zeropoint_agent.nodes.config.namespace.Namespace",
            config={"name": "root"},
            status="success",
            output={"name": "root", "path": "root"},
            output_type=(
                "zeropoint_agent.nodes.config.namespace.NamespaceResult"
            ),
            perms="rwd",
        ))
        self.store.close()
        self.store = SQLiteGraphStore(str(self.db_path))
        dag = DAG(store=self.store)
        self.assertIsInstance(dag.get("root").output, NamespaceResult)
        self.assertEqual(dag.get("root").output.path, "root")

    def test_backup_is_consistent_and_reopenable(self):
        self.store.add_node(self.node("root", status="success", output={"ok": True}))
        backup_path = Path(self.tempdir.name) / "backup.db"
        self.store.backup_to(str(backup_path))

        backup = SQLiteGraphStore(str(backup_path))
        try:
            root = backup.get_node("root")
            self.assertIsNotNone(root)
            self.assertEqual(root.output, {"ok": True})
            self.assertEqual(root.status, "success")
        finally:
            backup.close()


if __name__ == "__main__":
    unittest.main()
