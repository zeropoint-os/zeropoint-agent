"""Generic durable interactive Job checkpoint REST behavior."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from zeropoint_agent.dag import DAG
from zeropoint_agent.graph_runner import GraphRunner
from zeropoint_agent.handlers import jobs as jobs_handler
from zeropoint_agent.handlers.resolve import _run_response
from zeropoint_agent.inode import INode, NodeResult, ResolveMode
from zeropoint_agent.interaction import (
    checkpoint,
    device_code,
    url_qr,
    validate_interaction,
)
from zeropoint_agent.jobs import JobContext, JobManager, JobSpec
from zeropoint_agent.promise_controls import (
    JobCancellation,
    JobCheckpoint,
    JobProgress,
)
from zeropoint_agent.sqlite_store import SQLiteGraphStore


@dataclass
class InteractiveResult:
    value: str


@dataclass
class InteractiveNode(INode[None, InteractiveResult]):
    value: str

    def resolve(self, input, mode):
        return NodeResult.failed("interactive resolve must use a durable Job")

    def resolve_promise(self, input, mode, context):
        return context.jobs.submit(
            context,
            JobSpec(
                kind="test.interactive",
                arguments={"value": self.value},
                resources=["interactive:test"],
                max_attempts=3,
            ),
        )

    def verify(self, mode):
        return NodeResult()

    def remove(self, mode):
        return NodeResult.success()


def register_interactive(
    manager: JobManager,
    executor,
) -> None:
    manager.register_executor(
        "test.interactive",
        executor,
        cancellation=JobCancellation,
        progress=JobProgress,
        checkpoint=JobCheckpoint,
    )


def rest_app(dag: DAG, manager: JobManager, runner: GraphRunner) -> FastAPI:
    app = FastAPI()
    app.state.dag = dag
    app.state.jobs = manager
    app.state.graph_runner = runner
    app.include_router(jobs_handler.router)

    @app.get("/test/runs/{run_id}")
    async def get_run(run_id: str, request: Request):
        return _run_response(request, run_id)

    return app


class InteractiveJobRestTests(unittest.TestCase):
    def wait_for(self, predicate, timeout: float = 2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.01)
        self.fail("condition did not become true")

    def test_waiting_run_and_promise_expose_checkpoint_then_clear_on_completion(self):
        with tempfile.TemporaryDirectory() as root:
            store = SQLiteGraphStore(str(Path(root) / "state.db"))
            dag = DAG(store=store)
            manager = JobManager(store, poll_interval=0.01, lease_seconds=1)
            runner = GraphRunner(dag, store, manager)
            started = threading.Event()
            release = threading.Event()

            def executor(context: JobContext, arguments):
                context.progress(0.2, checkpoint(url_qr(
                    interaction_id="attempt-1",
                    title="Authorize device",
                    message="Open the link or scan the QR code.",
                    url="https://example.test/authorize",
                    qr="SENSITIVE-QR",
                )))
                started.set()
                while not release.wait(0.01):
                    context.cancellation.raise_if_requested()
                context.progress(1.0, {"phase": "authenticated"})
                return NodeResult.success(InteractiveResult(arguments["value"]))

            register_interactive(manager, executor)
            dag.add("interactive", InteractiveNode("done"))
            manager.set_promise_callback(
                lambda run_id, promise_id: runner.on_promise_terminal(
                    run_id, promise_id
                )
            )
            manager.start()
            try:
                run = runner.start(ResolveMode.LIVE)
                self.assertTrue(started.wait(1))
                client = TestClient(rest_app(dag, manager, runner))

                waiting = client.get(f"/test/runs/{run.id}")
                self.assertEqual(waiting.status_code, 200)
                payload = waiting.json()
                self.assertEqual(payload["status"], "requires_interaction")
                self.assertEqual(payload["internal_status"], "waiting")
                self.assertTrue(payload["interaction_required"])
                node = payload["nodes"][0]
                self.assertEqual(node["status"], "requires_interaction")
                self.assertEqual(node["internal_status"], "waiting")
                self.assertNotIn("output", node)
                self.assertEqual(node["promise"]["progress"], 0.2)
                expected_interaction = {
                    "id": "attempt-1",
                    "kind": "url_qr",
                    "title": "Authorize device",
                    "message": "Open the link or scan the QR code.",
                    "url": "https://example.test/authorize",
                    "qr": "SENSITIVE-QR",
                }
                self.assertEqual(node["interaction"], expected_interaction)
                self.assertEqual(
                    node["promise"]["interaction"],
                    expected_interaction,
                )
                self.assertNotIn("checkpoint", node["promise"])

                promise_id = node["promise"]["id"]
                promise = client.get(f"/api/promises/{promise_id}")
                self.assertEqual(promise.status_code, 200)
                self.assertEqual(promise.json()["status"], "requires_interaction")
                self.assertEqual(
                    promise.json()["interaction"],
                    expected_interaction,
                )
                self.assertNotIn("checkpoint", promise.json())

                release.set()
                self.wait_for(
                    lambda: runner.describe(run)["status"] == "succeeded"
                )
                terminal_promise = client.get(
                    f"/api/promises/{promise_id}"
                ).json()
                self.assertEqual(terminal_promise["progress"], 1)
                self.assertNotIn("checkpoint", terminal_promise)

                terminal_run = client.get(f"/test/runs/{run.id}").json()
                terminal_node = terminal_run["nodes"][0]
                self.assertEqual(terminal_node["output"], {"value": "done"})
                self.assertNotIn("promise", terminal_node)
                job = store.get_job_for_promise(promise_id)
                self.assertIsNone(job["checkpoint"])
            finally:
                release.set()
                manager.stop()
                store.close()

    def test_interaction_contract_rejects_secret_fields(self):
        value = url_qr(
            interaction_id="attempt-1",
            title="Authorize",
            message="Scan the code.",
            url="https://example.test/authorize",
            qr="QR",
        )
        with self.assertRaisesRegex(ValueError, "unsupported interaction fields"):
            validate_interaction({**value, "access_token": "secret"})

    def test_cancellation_clears_actionable_interaction(self):
        with tempfile.TemporaryDirectory() as root:
            store = SQLiteGraphStore(str(Path(root) / "state.db"))
            dag = DAG(store=store)
            manager = JobManager(store, poll_interval=0.01, lease_seconds=1)
            runner = GraphRunner(dag, store, manager)
            started = threading.Event()

            def executor(context: JobContext, arguments):
                context.progress(0.2, checkpoint(url_qr(
                    interaction_id="cancel-attempt",
                    title="Authorize",
                    message="Scan the code.",
                    url="https://example.test/authorize",
                    qr="SENSITIVE-QR",
                )))
                started.set()
                while True:
                    context.cancellation.raise_if_requested()
                    time.sleep(0.01)

            register_interactive(manager, executor)
            dag.add("interactive", InteractiveNode("done"))
            manager.set_promise_callback(
                lambda run_id, promise_id: runner.on_promise_terminal(
                    run_id, promise_id
                )
            )
            manager.start()
            try:
                run = runner.start(ResolveMode.LIVE)
                self.assertTrue(started.wait(1))
                row = runner.describe(run)["nodes"][0]
                promise_id = row["promise_id"]
                manager.cancel_promise(promise_id)
                self.wait_for(
                    lambda: manager.promise(promise_id)["status"] == "cancelled"
                )
                client = TestClient(rest_app(dag, manager, runner))
                promise = client.get(f"/api/promises/{promise_id}").json()
                self.assertEqual(promise["status"], "cancelled")
                self.assertNotIn("checkpoint", promise)
                self.assertNotIn("interaction", promise)
            finally:
                manager.stop()
                store.close()

    def test_checkpoint_survives_process_restart_and_rest_polling(self):
        with tempfile.TemporaryDirectory() as root:
            db_path = Path(root) / "state.db"
            store = SQLiteGraphStore(str(db_path))
            dag = DAG(store=store)
            manager = JobManager(store)
            runner = GraphRunner(dag, store, manager)
            register_interactive(
                manager,
                lambda context, arguments: NodeResult.success(
                    InteractiveResult(arguments["value"])
                ),
            )
            dag.add("interactive", InteractiveNode("done"))
            run = runner.start(ResolveMode.LIVE)
            job = store.list_jobs(limit=1)[0]
            worker = "crashed-worker"
            lease = "2999-01-01T00:00:00.000Z"
            claimed = store.claim_job(job["id"], worker, lease)
            self.assertIsNotNone(claimed)
            store.update_job_progress(
                job["id"],
                worker,
                0.3,
                checkpoint(device_code(
                    interaction_id="device-code-1",
                    title="Authorize device",
                    message="Open the URL and enter the code.",
                    verification_url="https://example.test/device",
                    user_code="ABCD-EFGH",
                    qr="DEVICE-CODE-QR",
                )),
                lease,
            )
            promise_id = job["promise_id"]
            store.close()

            reopened = SQLiteGraphStore(str(db_path))
            reopened.recover_incomplete_jobs()
            loaded_dag = DAG(store=reopened)
            loaded_manager = JobManager(reopened)
            register_interactive(
                loaded_manager,
                lambda context, arguments: NodeResult.success(
                    InteractiveResult(arguments["value"])
                ),
            )
            loaded_runner = GraphRunner(
                loaded_dag,
                reopened,
                loaded_manager,
            )
            try:
                client = TestClient(
                    rest_app(loaded_dag, loaded_manager, loaded_runner)
                )
                run_response = client.get(f"/test/runs/{run.id}").json()
                self.assertEqual(run_response["status"], "requires_interaction")
                node = run_response["nodes"][0]
                self.assertEqual(node["status"], "requires_interaction")
                self.assertEqual(node["promise"]["progress"], 0.3)
                expected_interaction = {
                    "id": "device-code-1",
                    "kind": "device_code",
                    "title": "Authorize device",
                    "message": "Open the URL and enter the code.",
                    "verification_url": "https://example.test/device",
                    "user_code": "ABCD-EFGH",
                    "qr": "DEVICE-CODE-QR",
                }
                self.assertEqual(node["interaction"], expected_interaction)
                promise = client.get(f"/api/promises/{promise_id}").json()
                self.assertEqual(promise["progress"], 0.3)
                self.assertEqual(promise["status"], "requires_interaction")
                self.assertEqual(promise["interaction"], expected_interaction)
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
