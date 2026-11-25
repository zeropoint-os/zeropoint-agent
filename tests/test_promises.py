"""Durable Promise, JobManager and readiness-scheduler behavior."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zeropoint_agent.dag import DAG  # noqa: E402
from zeropoint_agent.graph_runner import GraphRunner  # noqa: E402
from zeropoint_agent.inode import (  # noqa: E402
    INode,
    NodeResult,
    NodeStatus,
    ProducedNode,
    ResolveMode,
)
from zeropoint_agent.jobs import JobContext, JobManager, JobSpec  # noqa: E402
from zeropoint_agent.nodes.core.shell_script import (  # noqa: E402
    ShellScript,
    execute_shell_script_job,
    execute_shell_script_verify_job,
)
from zeropoint_agent.nodes.config.directory import (  # noqa: E402
    DirectoryVar,
    execute_directory_move_job,
)
from zeropoint_agent.nodes.config.var import Var  # noqa: E402
from zeropoint_agent.nodes.user.module import (  # noqa: E402
    ContainerInfo,
    TerraformResult,
    _ensure_module_source,
    _flatten_inputs,
    _module_source_matches,
    execute_terraform_job,
)
from zeropoint_agent.module_installer import (  # noqa: E402
    TfVariable,
    _hcl_variable_to_str,
    build_module_child_plan,
)
from zeropoint_agent.promise import ResultEnvelope  # noqa: E402
from zeropoint_agent.sqlite_store import SQLiteGraphStore  # noqa: E402


@dataclass
class ValueResult:
    value: str


@dataclass
class AsyncValue(INode[None, ValueResult]):
    value: str
    executor: str = "test.wait"

    def resolve(self, input, mode):
        return NodeResult.failed("async hook was bypassed")

    def resolve_promise(self, input, mode, context):
        if mode == ResolveMode.MOCK:
            return super().resolve_promise(input, mode, context)
        return context.jobs.submit(
            context,
            JobSpec(
                kind=self.executor,
                arguments={"value": self.value},
                resources=[f"value:{self.value}"],
            ),
        )

    def verify(self, mode):
        return NodeResult()

    def remove(self, mode):
        return NodeResult.success()


@dataclass
class ExplicitAsyncValue(AsyncValue):
    def resolve_promise(self, input, mode, context):
        return context.jobs.submit(
            context,
            JobSpec(
                kind=self.executor,
                arguments={"value": self.value},
                resources=[f"value:{self.value}"],
                idempotency_key=f"explicit:{self.id}",
            ),
        )


@dataclass
class CopyValue(INode[ValueResult, ValueResult]):
    suffix: str = ""

    def resolve(self, input, mode):
        if isinstance(input, dict):
            input = next(iter(input.values()))
        return NodeResult.success(ValueResult(input.value + self.suffix))

    def verify(self, mode):
        return NodeResult()

    def remove(self, mode):
        return NodeResult.success()


@dataclass
class ImmediateValue(INode[None, ValueResult]):
    value: str

    def resolve(self, input, mode):
        return NodeResult.success(ValueResult(self.value))

    def verify(self, mode):
        return NodeResult()

    def remove(self, mode):
        return NodeResult.success()


@dataclass
class ProducedRoot(INode[None, ValueResult]):
    child_value: str
    include_child: bool = True

    def resolve(self, input, mode):
        result = NodeResult.success(ValueResult("root"))
        if self.include_child:
            result.produce(
                f"{self.id}/child",
                ImmediateValue(self.child_value),
                parents=[self.id],
                tags=["generated"],
            )
        return result

    def verify(self, mode):
        return NodeResult()

    def remove(self, mode):
        return NodeResult.success()


@dataclass
class AsyncProducedRoot(ProducedRoot):
    def resolve_promise(self, input, mode, context):
        return context.jobs.submit(
            context,
            JobSpec(
                kind="test.produce",
                arguments={
                    "node_id": self.id,
                    "child_value": self.child_value,
                },
                resources=[f"producer:{self.id}"],
            ),
        )


@dataclass
class AsyncVerify(ImmediateValue):
    def verify_promise(self, input, mode, context):
        return context.jobs.submit(
            context,
            JobSpec(
                kind="test.verify.pending",
                arguments={"value": self.value},
                resources=[f"verify:{self.id}"],
            ),
        )

    def verify(self, mode):
        return NodeResult.pending_reboot(ValueResult("not-converged"))


@dataclass
class AsyncRemove(ImmediateValue):
    def remove_promise(self, input, mode, context):
        return context.jobs.submit(
            context,
            JobSpec(
                kind="test.wait",
                arguments={"value": self.value},
                resources=[f"remove:{self.id}"],
            ),
        )


class PromiseRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "state.db"
        self.store = SQLiteGraphStore(str(self.db_path))
        self.dag = DAG(store=self.store)
        self.jobs = JobManager(
            self.store,
            max_workers=2,
            poll_interval=0.01,
            lease_seconds=1,
        )
        self.runner = GraphRunner(self.dag, self.store, self.jobs)
        self.started = threading.Event()
        self.release = threading.Event()

        def wait_executor(context: JobContext, arguments):
            self.started.set()
            while not self.release.wait(0.01):
                context.cancellation.raise_if_requested()
                context.progress(0.5, {"waiting": True})
            context.cancellation.raise_if_requested()
            return NodeResult.success(ValueResult(arguments["value"]))

        self.jobs.register_executor("test.wait", wait_executor)

        def verify_pending_executor(context: JobContext, arguments):
            self.started.set()
            while not self.release.wait(0.01):
                context.cancellation.raise_if_requested()
            return NodeResult.pending_reboot(
                ValueResult(arguments["value"])
            )

        self.jobs.register_executor(
            "test.verify.pending", verify_pending_executor
        )

        def produce_executor(context: JobContext, arguments):
            return NodeResult.success(ValueResult("root")).produce(
                f"{arguments['node_id']}/child",
                ImmediateValue(arguments["child_value"]),
                parents=[arguments["node_id"]],
                tags=["generated"],
            )

        self.jobs.register_executor("test.produce", produce_executor)
        self.jobs.register_executor(
            "node.directory.move",
            execute_directory_move_job,
        )
        self.jobs.start()

    def tearDown(self):
        self.release.set()
        self.jobs.stop()
        self.store.close()
        self.tempdir.cleanup()

    def wait_for(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.01)
        self.fail("condition did not become true")

    def test_result_envelope_rehydrates_nested_dataclasses(self):
        original = TerraformResult(
            source="source",
            containers={
                "main": ContainerInfo(name="main", ip="10.0.0.2")
            },
        )
        restored = ResultEnvelope.from_result(
            NodeResult.success(original)
        ).to_result().output
        self.assertIsInstance(restored, TerraformResult)
        self.assertIsInstance(restored.containers["main"], ContainerInfo)
        self.assertEqual(restored.containers["main"].ip, "10.0.0.2")

    def test_promise_result_produces_and_resolves_child_in_same_run(self):
        self.dag.add("root", ProducedRoot("child-value"))

        run = self.runner.start(ResolveMode.MOCK)
        description = self.runner.describe(run.id)

        self.assertEqual(description["status"], "succeeded")
        self.assertIn("root/child", self.dag.nodes)
        child = self.dag.get("root/child")
        self.assertEqual(child.producer_id, "root")
        self.assertEqual(child.status, NodeStatus.SUCCESS)
        self.assertEqual(child.output, ValueResult("child-value"))
        self.assertEqual(
            {node["node_id"] for node in description["nodes"]},
            {"root", "root/child"},
        )

        reloaded = DAG(store=self.store)
        self.assertEqual(reloaded.get("root/child").producer_id, "root")

    def test_durable_job_result_produces_child_in_same_run(self):
        self.dag.add("root", AsyncProducedRoot("async-child"))

        run = self.runner.start(ResolveMode.LIVE)
        self.wait_for(
            lambda: self.runner.describe(run.id)["status"] == "succeeded"
        )

        child = self.dag.get("root/child")
        self.assertEqual(child.producer_id, "root")
        self.assertEqual(child.output, ValueResult("async-child"))

    def test_directory_var_move_runs_as_durable_job(self):
        old = Path(self.tempdir.name) / "old"
        new = Path(self.tempdir.name) / "new"
        old.mkdir()
        (old / "value.txt").write_text("preserved", encoding="utf-8")
        self.dag.add(
            "storage",
            DirectoryVar(
                name="zp_storage_dir",
                value=str(new),
                previous_value=str(old),
            ),
        )

        run = self.runner.start(ResolveMode.LIVE)
        self.wait_for(
            lambda: self.runner.describe(run.id)["status"] == "succeeded"
        )

        self.assertFalse(old.exists())
        self.assertEqual(
            (new / "value.txt").read_text(encoding="utf-8"),
            "preserved",
        )
        self.assertEqual(
            self.dag.get("storage").output.value,
            str(new),
        )
        self.assertIsNone(self.dag.get("storage").node.previous_value)
        self.assertIsNone(
            self.store.get_node("storage").config["previous_value"]
        )

    def test_directory_var_clears_stale_completed_move_on_verify(self):
        destination = Path(self.tempdir.name) / "destination"
        destination.mkdir()
        self.dag.add(
            "storage",
            DirectoryVar(
                name="zp_storage_dir",
                value=str(destination),
                previous_value=str(Path(self.tempdir.name) / "missing-source"),
            ),
        )

        run = self.runner.start(ResolveMode.LIVE)

        self.assertEqual(self.runner.describe(run.id)["status"], "succeeded")
        self.assertIsNone(self.dag.get("storage").node.previous_value)
        self.assertIsNone(
            self.store.get_node("storage").config["previous_value"]
        )

    def test_producer_removes_unreferenced_child_on_next_resolve(self):
        self.dag.add("root", ProducedRoot("child-value"))
        first = self.runner.start(ResolveMode.MOCK)
        self.assertEqual(self.runner.describe(first.id)["status"], "succeeded")

        root = self.dag.get("root")
        root.node.include_child = False
        self.store.set_config(
            "root",
            {
                "child_value": root.node.child_value,
                "include_child": False,
            },
        )
        second = self.runner.start(ResolveMode.MOCK)

        self.assertEqual(self.runner.describe(second.id)["status"], "succeeded")
        self.assertNotIn("root/child", self.dag.nodes)
        self.assertFalse(self.store.has_node("root/child"))

    def test_producer_refresh_preserves_var_value_and_external_link(self):
        self.dag.add("root", ProducedRoot("child-value"))
        self.dag.add("library", Var(name="library", value="/storage/media"))
        initial = ProducedNode.from_node(
            "root/storage",
            DirectoryVar(name="zp_storage_dir", value="/default"),
            parents=["root"],
            perms="rw-",
            tags=["input"],
        )
        self.dag.reconcile_produced("root", [initial])

        storage = self.dag.get("root/storage")
        storage.node.value = None
        storage.parents.append("library")
        self.store.set_config(
            "root/storage",
            {
                "name": "zp_storage_dir",
                "value": None,
                "previous_value": None,
            },
        )
        self.store.add_edge("library", "root/storage")

        refreshed = ProducedNode.from_node(
            "root/storage",
            DirectoryVar(name="zp_storage_dir", value="/new-default"),
            parents=["root"],
            perms="r--",
            tags=["updated"],
        )
        self.dag.reconcile_produced("root", [refreshed])

        storage = self.dag.get("root/storage")
        self.assertIsNone(storage.node.value)
        self.assertEqual(storage.parents, ["root", "library"])
        self.assertEqual(storage.perms, "r--")
        self.assertEqual(storage.tags, {"updated"})
        persisted = self.store.get_node("root/storage")
        self.assertIsNone(persisted.config["value"])
        reloaded = DAG(store=self.store)
        self.assertEqual(
            reloaded.get("root/storage").parents,
            ["root", "library"],
        )

    def test_persisted_var_outputs_feed_terraform_removal(self):
        self.assertEqual(
            _flatten_inputs({
                "module-dir": {"name": "zp_module_dir", "value": "/tmp/module"},
                "network": {"name": "zp_network_name", "value": "network"},
            }),
            {
                "zp_module_dir": "/tmp/module",
                "zp_network_name": "network",
            },
        )

    def test_startup_terraform_recovery_starts_existing_container(self):
        module_dir = Path(self.tempdir.name) / "module"
        module_dir.mkdir()
        (module_dir / ".zeropoint-source").write_text(
            "https://example.invalid/demo.git@"
            "0123456789abcdef0123456789abcdef01234567\n",
            encoding="utf-8",
        )
        context = MagicMock()
        context.cancellation.raise_if_requested.return_value = None
        executor = MagicMock()
        executor.output.return_value = {
            "main": {"value": "demo-main"},
            "main_ports": {
                "value": {
                    "http": {"port": 8080, "protocol": "http"}
                }
            },
        }
        with (
            patch(
                "zeropoint_agent.nodes.user.module._docker_inspect",
                side_effect=[
                    {"State": {"Status": "exited"}},
                    {"State": {"Status": "running"}},
                    {"State": {"Status": "running"}},
                ],
            ),
            patch(
                "zeropoint_agent.nodes.user.module.subprocess.run",
                return_value=MagicMock(returncode=0, stdout="", stderr=""),
            ) as run,
            patch(
                "zeropoint_agent.nodes.user.module.TerraformExecutor",
                return_value=executor,
            ),
        ):
            result = execute_terraform_job(
                context,
                {
                    "source": (
                        "https://example.invalid/demo.git@"
                        "0123456789abcdef0123456789abcdef01234567"
                    ),
                    "recover_existing": True,
                    "tfvars": {
                        "zp_module_id": "demo",
                        "zp_module_dir": str(module_dir),
                        "zp_network_name": "demo-network",
                    },
                },
            )
        self.assertEqual(result.status, NodeStatus.SUCCESS)
        run.assert_called_once_with(
            ["docker", "start", "demo-main"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        executor.output.assert_called_once_with()
        executor.init.assert_not_called()
        executor.apply.assert_not_called()
        context.progress.assert_any_call(1.0, {"phase": "recovered"})

    def test_module_source_refresh_preserves_terraform_runtime(self):
        module_dir = Path(self.tempdir.name) / "module"
        module_dir.mkdir()
        (module_dir / "main.tf").write_text("old", encoding="utf-8")
        (module_dir / "terraform.tfstate").write_text(
            "state",
            encoding="utf-8",
        )
        (module_dir / ".terraform.lock.hcl").write_text(
            "lock",
            encoding="utf-8",
        )
        provider_dir = module_dir / ".terraform" / "providers"
        provider_dir.mkdir(parents=True)
        (provider_dir / "cached").write_text("provider", encoding="utf-8")

        url = "https://example.invalid/module.git"
        sha = "0123456789abcdef0123456789abcdef01234567"

        def clone(_url, _sha, target):
            target.mkdir(parents=True)
            (target / "main.tf").write_text("new", encoding="utf-8")

        with patch(
            "zeropoint_agent.nodes.user.module._git_clone_at_sha",
            side_effect=clone,
        ) as clone_at_sha:
            self.assertTrue(_ensure_module_source(url, sha, module_dir))
            self.assertFalse(_ensure_module_source(url, sha, module_dir))

        clone_at_sha.assert_called_once()
        self.assertEqual(
            (module_dir / "main.tf").read_text(encoding="utf-8"),
            "new",
        )
        self.assertEqual(
            (module_dir / "terraform.tfstate").read_text(encoding="utf-8"),
            "state",
        )
        self.assertEqual(
            (module_dir / ".terraform.lock.hcl").read_text(encoding="utf-8"),
            "lock",
        )
        self.assertEqual(
            (module_dir / ".terraform" / "providers" / "cached").read_text(
                encoding="utf-8"
            ),
            "provider",
        )
        self.assertTrue(_module_source_matches(module_dir, url, sha))

    def test_explicit_terraform_null_default_is_preserved(self):
        self.assertEqual(_hcl_variable_to_str(None), "null")

    def test_explicit_null_default_is_omitted_from_module_children(self):
        inspection = Path(self.tempdir.name) / "inspection" / "module"
        inspection.mkdir(parents=True)
        with (
            patch(
                "zeropoint_agent.module_installer._parse_git_source",
                return_value=("https://example.invalid/module.git", "0" * 40),
            ),
            patch(
                "zeropoint_agent.module_installer."
                "_shallow_clone_for_inspection",
                return_value=inspection,
            ),
            patch(
                "zeropoint_agent.module_installer.parse_variables_tf",
                return_value=[
                    TfVariable(
                        name="optional_path",
                        default=None,
                        has_default=True,
                    ),
                    TfVariable(
                        name="required_path",
                        default=None,
                        has_default=False,
                    ),
                ],
            ),
            patch(
                "zeropoint_agent.module_installer.parse_outputs_tf",
                return_value=[],
            ),
        ):
            plan = build_module_child_plan(
                "demo",
                "https://example.invalid/module.git@" + "0" * 40,
            )
        ids = {spec.id for spec in plan.specs}
        self.assertNotIn("modules/demo/optional_path", ids)
        self.assertIn("modules/demo/required_path", ids)

    def test_dry_run_records_terminal_scheduler_status(self):
        self.dag.add("value", ImmediateValue("dry"))
        run = self.runner.start(ResolveMode.DRY_RUN)
        result = self.runner.describe(run)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["nodes"][0]["status"], "dry_run")
        self.assertEqual(result["nodes"][0]["result"]["status"], "pending")

    def test_pending_verification_blocks_only_descendants_then_resolves(self):
        self.dag.add("verify", AsyncVerify("resolved"))
        self.dag.add("verify/child", CopyValue("-child"), parents=["verify"])
        self.dag.add("independent", ImmediateValue("ready"))

        run = self.runner.start(ResolveMode.LIVE)
        self.assertTrue(self.started.wait(1))
        current = self.runner.describe(run)
        statuses = {row["node_id"]: row for row in current["nodes"]}
        self.assertEqual(statuses["verify"]["phase"], "verify")
        self.assertEqual(statuses["verify"]["status"], "waiting")
        self.assertEqual(statuses["verify/child"]["status"], "pending")
        self.assertEqual(statuses["independent"]["status"], "success")

        self.release.set()
        final = self.wait_for(
            lambda: (
                value if (value := self.runner.describe(run))["status"] == "succeeded"
                else None
            )
        )
        rows = {row["node_id"]: row for row in final["nodes"]}
        self.assertEqual(rows["verify"]["phase"], "resolve")
        self.assertEqual(rows["verify"]["verification"]["status"], "pending_reboot")
        self.assertEqual(self.dag.get("verify").output.value, "resolved")
        self.assertEqual(
            self.dag.get("verify/child").output.value,
            "resolved-child",
        )

    def test_pending_promise_blocks_only_descendants(self):
        self.dag.add("async", AsyncValue("parent"))
        self.dag.add("async/child", CopyValue("-child"), parents=["async"])
        self.dag.add("independent", ImmediateValue("ready"))

        run_id = self.runner.start(ResolveMode.LIVE)
        self.assertTrue(self.started.wait(1))
        run = self.runner.describe(run_id)
        statuses = {row["node_id"]: row["status"] for row in run["nodes"]}
        self.assertEqual(statuses["async"], "waiting")
        self.assertEqual(statuses["async/child"], "pending")
        self.assertEqual(statuses["independent"], "success")
        self.assertEqual(self.dag.get("independent").output.value, "ready")

        self.release.set()
        final = self.wait_for(
            lambda: (
                value if (value := self.runner.describe(run_id))["status"] == "succeeded"
                else None
            )
        )
        statuses = {row["node_id"]: row["status"] for row in final["nodes"]}
        self.assertEqual(statuses["async"], "success")
        self.assertEqual(statuses["async/child"], "success")
        self.assertEqual(self.dag.get("async/child").output.value, "parent-child")
        waited = asyncio.run(run_id.wait())
        self.assertEqual(waited["status"], "succeeded")
        self.assertEqual(type(run_id.pausing).__name__, "GraphRunPause")

    def test_overlapping_runs_share_active_promise(self):
        self.dag.add("async", AsyncValue("value"))
        first = self.runner.start(ResolveMode.LIVE)
        self.assertTrue(self.started.wait(1))
        second = self.runner.start(ResolveMode.LIVE)
        with sqlite3.connect(self.db_path) as connection:
            job_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        self.assertEqual(job_count, 1)

        self.release.set()
        self.wait_for(lambda: self.runner.describe(first)["status"] == "succeeded")
        self.wait_for(lambda: self.runner.describe(second)["status"] == "succeeded")

    def test_explicit_idempotency_key_reuses_terminal_promise(self):
        self.dag.add("async", ExplicitAsyncValue("value"))
        first = self.runner.start(ResolveMode.LIVE)
        self.assertTrue(self.started.wait(1))
        self.release.set()
        self.wait_for(lambda: self.runner.describe(first)["status"] == "succeeded")

        second = self.runner.start(ResolveMode.LIVE)
        self.wait_for(lambda: self.runner.describe(second)["status"] == "succeeded")
        with sqlite3.connect(self.db_path) as connection:
            job_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        self.assertEqual(job_count, 1)

    def test_explicit_idempotency_key_isolated_by_resolve_mode(self):
        self.dag.add("async", ExplicitAsyncValue("value"))
        first = self.runner.start(ResolveMode.MOCK)
        self.assertTrue(self.started.wait(1))
        self.release.set()
        self.wait_for(lambda: self.runner.describe(first)["status"] == "succeeded")

        second = self.runner.start(ResolveMode.LIVE)
        self.wait_for(lambda: self.runner.describe(second)["status"] == "succeeded")
        with sqlite3.connect(self.db_path) as connection:
            job_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        self.assertEqual(job_count, 2)

    def test_shell_script_uses_durable_job_executor(self):
        self.jobs.register_executor(
            "node.shell_script", execute_shell_script_job
        )
        self.jobs.register_executor(
            "node.shell_script.verify", execute_shell_script_verify_job
        )
        marker = Path(self.tempdir.name) / "script-complete"
        self.dag.add("script", ShellScript(
            exec_cmd=(
                f"python -c 'print(\"x\" * 200000)'; touch {marker}"
            ),
            verify_cmd=f"test -f {marker}",
            timeout=2,
        ))
        run_id = self.runner.start(ResolveMode.LIVE)
        final = self.wait_for(
            lambda: (
                value if (value := self.runner.describe(run_id))["status"] == "succeeded"
                else None
            )
        )
        self.assertEqual(final["summary"].get("success"), 1)
        output = self.dag.get("script").output
        self.assertEqual(len(output.stdout), 500)
        self.assertEqual(set(output.stdout), {"x"})
        job_rows = self.store.get_graph_run_nodes(run_id.id)
        self.assertEqual(job_rows[0]["status"], "success")

    def test_cancellation_is_requested_then_acknowledged(self):
        self.dag.add("async", AsyncValue("parent"))
        run_id = self.runner.start(ResolveMode.LIVE)
        self.assertTrue(self.started.wait(1))

        self.runner.cancel(run_id)
        pause_after_cancel = asyncio.run(run_id.pausing.pause())
        self.assertEqual(pause_after_cancel["status"], "cancel_requested")
        final = self.wait_for(
            lambda: (
                value if (value := self.runner.describe(run_id))["status"] == "cancelled"
                else None
            )
        )
        self.assertEqual(final["summary"].get("cancelled"), 1)
        jobs = self.store.get_graph_run_nodes(run_id.id)
        promise_id = jobs[0]["promise_id"]
        self.assertIsNone(promise_id)

    def test_removal_commit_recovers_memory_if_local_delete_fails(self):
        self.dag.add("root", ImmediateValue("root"))
        with patch.object(
            self.dag,
            "remove",
            side_effect=RuntimeError("local delete failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "local delete failed"):
                self.runner.start_remove(["root"], ResolveMode.LIVE)
        self.assertFalse(self.store.has_node("root"))
        self.assertNotIn("root", self.dag.nodes)

    def test_remove_run_deletes_children_before_parents(self):
        self.dag.add("root", ImmediateValue("root"))
        self.dag.add("root/child", AsyncRemove("child"), parents=["root"])

        run = self.runner.start_remove(
            ["root", "root/child"],
            ResolveMode.LIVE,
        )
        self.assertTrue(self.started.wait(1))
        current = self.runner.describe(run)
        statuses = {row["node_id"]: row["status"] for row in current["nodes"]}
        self.assertEqual(statuses["root/child"], "waiting")
        self.assertEqual(statuses["root"], "pending")
        self.assertIn("root", self.dag.nodes)
        self.assertIn("root/child", self.dag.nodes)
        self.assertTrue(self.store.is_removal_pending("root"))
        self.assertTrue(self.store.is_removal_pending("root/child"))

        self.release.set()
        final = self.wait_for(
            lambda: (
                value if (value := self.runner.describe(run))["status"] == "succeeded"
                else None
            )
        )
        self.assertEqual(final["operation"], "remove")
        self.assertNotIn("root/child", self.dag.nodes)
        self.assertNotIn("root", self.dag.nodes)
        self.assertFalse(self.store.is_removal_pending("root"))

    def test_remove_cancellation_keeps_unremoved_nodes(self):
        self.dag.add("root", ImmediateValue("root"))
        self.dag.add("root/child", AsyncRemove("child"), parents=["root"])
        run = self.runner.start_remove(
            ["root", "root/child"],
            ResolveMode.LIVE,
        )
        self.assertTrue(self.started.wait(1))
        asyncio.run(run.cancellation.cancel())
        self.wait_for(
            lambda: self.runner.describe(run)["status"] == "cancelled"
        )
        self.assertIn("root", self.dag.nodes)
        self.assertIn("root/child", self.dag.nodes)

    def test_graph_run_pause_waits_for_active_job_then_resumes(self):
        self.dag.add("async", AsyncValue("parent"))
        self.dag.add("async/child", CopyValue("-child"), parents=["async"])
        run = self.runner.start(ResolveMode.LIVE)
        self.assertTrue(self.started.wait(1))

        asyncio.run(run.pausing.pause())
        self.release.set()
        self.wait_for(lambda: self.runner.describe(run)["status"] == "paused")
        paused = self.runner.describe(run)
        statuses = {row["node_id"]: row["status"] for row in paused["nodes"]}
        self.assertEqual(statuses["async"], "success")
        self.assertEqual(statuses["async/child"], "pending")

        asyncio.run(run.pausing.resume())
        self.wait_for(lambda: self.runner.describe(run)["status"] == "succeeded")
        self.assertEqual(
            self.dag.get("async/child").output.value,
            "parent-child",
        )

    def test_descendant_invalidation_wakes_run_and_releases_lock(self):
        self.dag.add("root", ImmediateValue("root"))
        self.dag.add("root/async", AsyncValue("child"), parents=["root"])
        run_id = self.runner.start(ResolveMode.LIVE)
        self.assertTrue(self.started.wait(1))

        self.store.invalidate_descendants("root")
        final = self.wait_for(
            lambda: (
                value if (value := self.runner.describe(run_id))["status"] == "superseded"
                else None
            )
        )
        self.assertEqual(final["summary"].get("superseded"), 1)

        def locks_released():
            with sqlite3.connect(self.db_path) as connection:
                return connection.execute(
                    "SELECT COUNT(*) FROM resource_locks"
                ).fetchone()[0] == 0

        self.wait_for(locks_released)

    def test_changed_revision_supersedes_job_result(self):
        self.dag.add("async", AsyncValue("old"))
        run_id = self.runner.start(ResolveMode.LIVE)
        self.assertTrue(self.started.wait(1))

        self.store.set_config("async", {"value": "new", "executor": "test.wait"})
        self.release.set()
        final = self.wait_for(
            lambda: (
                value if (value := self.runner.describe(run_id))["status"] == "superseded"
                else None
            )
        )
        self.assertEqual(final["summary"].get("superseded"), 1)
        self.assertNotEqual(self.dag.get("async").status, NodeStatus.SUCCESS)


if __name__ == "__main__":
    unittest.main()
