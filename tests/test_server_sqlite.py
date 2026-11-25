"""Application startup and API persistence through SQLite."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from zeropoint_agent.handlers.events import _encode_sse_event  # noqa: E402
from zeropoint_agent.inode import ProducedNode  # noqa: E402
from zeropoint_agent.module_installer import ModuleChildPlan  # noqa: E402
from zeropoint_agent.nodes.config.var import Var  # noqa: E402
from zeropoint_agent.server import create_app  # noqa: E402

os.environ.setdefault("ZEROPOINT_MDNS_ADVERTISE", "0")


class ServerSQLiteTests(unittest.TestCase):
    def test_event_ledger_rest_resource_and_sse_encoding(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {
                "ZEROPOINT_ROOT_PATH": root,
                "ZEROPOINT_MODE": "mock",
                "ZEROPOINT_XDS_PORT": "0",
            }, clear=False):
                app = create_app()
                with TestClient(app) as client:
                    client.post("/api/graph/nodes", json={
                        "id": "settings",
                        "type": "namespace",
                        "config": {"name": "settings"},
                        "parents": [],
                        "perms": "rwd",
                    })
                    client.post("/api/graph/resolve", json={"mode": "mock"})
                    response = client.get(
                        "/api/events",
                        params={"aggregate_type": "graph_run"},
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    body = response.json()
                    self.assertGreaterEqual(len(body["events"]), 2)
                    self.assertEqual(
                        body["next_sequence"],
                        body["events"][-1]["sequence"],
                    )
                    self.assertTrue(all(
                        event["aggregate_type"] == "graph_run"
                        for event in body["events"]
                    ))
                    encoded = _encode_sse_event(body["events"][0])
                    self.assertIn(
                        f"id: {body['events'][0]['sequence']}\n",
                        encoded,
                    )
                    self.assertIn(
                        f"event: {body['events'][0]['event_type']}\n",
                        encoded,
                    )
                    self.assertIn(
                        "/api/events/stream",
                        client.get("/openapi.json").json()["paths"],
                    )

    def test_seeded_hardware_roots_resolve_on_startup(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {
                "ZEROPOINT_ROOT_PATH": root,
                "ZEROPOINT_MODE": "mock",
                "ZEROPOINT_SEED_ROOTS": "1",
                "ZEROPOINT_XDS_PORT": "0",
            }, clear=False):
                app = create_app()
                with TestClient(app) as client:
                    graph = client.get("/api/graph").json()
                    node_ids = {node["id"] for node in graph["nodes"]}
                    self.assertIn("hardware/disks", node_ids)
                    self.assertIn("hardware/gpus", node_ids)
                    self.assertIn("hardware/networks", node_ids)
                    self.assertIn("hardware/usbs", node_ids)
                    self.assertIn("hardware/pcis", node_ids)
                    self.assertIn("hardware/system", node_ids)
                    startup = app.state.graph_runner.describe(
                        app.state.startup_run.id
                    )
                    self.assertEqual(startup["status"], "succeeded")

    def test_reboot_run_is_a_persisted_pending_reboot_promise(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {
                "ZEROPOINT_ROOT_PATH": root,
                "ZEROPOINT_MODE": "mock",
                "ZEROPOINT_XDS_PORT": "0",
            }, clear=False):
                app = create_app()
                with TestClient(app) as client:
                    system = client.post("/api/graph/nodes", json={
                        "id": "system",
                        "type": "namespace",
                        "config": {"name": "system"},
                        "parents": [],
                        "perms": "rw-",
                    })
                    self.assertEqual(system.status_code, 200, system.text)
                    response = client.post(
                        "/api/runs/reboot",
                        json={"mode": "mock"},
                    )
                    self.assertEqual(response.status_code, 202, response.text)
                    body = response.json()
                    self.assertEqual(body["status"], "succeeded")
                    self.assertTrue(body["needs_reboot"])
                    self.assertEqual(
                        body["reboot_nodes"],
                        ["system/reboot"],
                    )
                    reboot = next(
                        node for node in body["nodes"]
                        if node["id"] == "system/reboot"
                    )
                    self.assertEqual(reboot["status"], "pending_reboot")
                    stored = client.get(
                        "/api/graph/nodes/system/reboot"
                    ).json()
                    self.assertEqual(stored["status"], "pending_reboot")

    def test_module_endpoint_returns_run_before_promise_preparation(self):
        with tempfile.TemporaryDirectory() as root:
            plan = ModuleChildPlan(
                specs=[
                    ProducedNode.from_node(
                        "modules/demo/message",
                        Var(name="message", value="hello"),
                        parents=["modules/demo"],
                    )
                ],
                wired_existing_nodes=[],
            )
            with (
                patch.dict(os.environ, {
                    "ZEROPOINT_ROOT_PATH": root,
                    "ZEROPOINT_MODE": "live",
                    "ZEROPOINT_XDS_PORT": "0",
                }, clear=False),
                patch(
                    "zeropoint_agent.nodes.user.module_definition."
                    "build_module_child_plan",
                    return_value=plan,
                ),
            ):
                app = create_app()
                with TestClient(app) as client:
                    modules = client.post("/api/graph/nodes", json={
                        "id": "modules",
                        "type": "namespace",
                        "config": {"name": "modules"},
                        "parents": [],
                        "perms": "rwd",
                    })
                    self.assertEqual(modules.status_code, 200, modules.text)
                    response = client.post("/api/modules", json={
                        "module_id": "demo",
                        "source": (
                            "https://example.invalid/demo.git@"
                            "0123456789abcdef0123456789abcdef01234567"
                        ),
                        "resolve": True,
                    })
                    self.assertEqual(response.status_code, 200, response.text)
                    run_id = response.json()["run_id"]
                    self.assertIn(
                        response.json()["status"],
                        {"running", "waiting", "succeeded"},
                    )

                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        run = client.get(f"/api/runs/{run_id}").json()
                        if run["status"] in {
                            "succeeded",
                            "failed",
                            "cancelled",
                            "superseded",
                        }:
                            break
                        time.sleep(0.02)
                    else:
                        self.fail({
                            "run": client.get(f"/api/runs/{run_id}").json(),
                            "jobs": client.get("/api/jobs").json(),
                            "promises": client.get("/api/promises").json(),
                        })
                    self.assertEqual(run["status"], "succeeded", run)

                    module = client.get(
                        "/api/graph/nodes/modules/demo"
                    ).json()
                    child = client.get(
                        "/api/graph/nodes/modules/demo/message"
                    ).json()
                    self.assertEqual(module["type"], "Module")
                    self.assertEqual(child["producer_id"], "modules/demo")

    def test_delete_uses_persistent_removal_run(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {
                "ZEROPOINT_ROOT_PATH": root,
                "ZEROPOINT_MODE": "mock",
                "ZEROPOINT_XDS_PORT": "0",
            }, clear=False):
                app = create_app()
                with TestClient(app) as client:
                    client.post("/api/dag/nodes", json={
                        "id": "settings",
                        "type": "namespace",
                        "config": {"name": "settings"},
                        "parents": [],
                        "perms": "rwd",
                    })
                    client.post("/api/dag/nodes", json={
                        "id": "settings/value",
                        "type": "var",
                        "config": {"name": "value", "value": "old"},
                        "parents": ["settings"],
                        "perms": "rwd",
                    })
                    deleted = client.delete("/api/dag/settings")
                    self.assertEqual(deleted.status_code, 200, deleted.text)
                    body = deleted.json()
                    self.assertEqual(body["status"], "succeeded")
                    self.assertEqual(body["operation"], "remove")
                    self.assertEqual(
                        client.get("/api/dag/nodes/settings").status_code,
                        404,
                    )
                    self.assertEqual(
                        client.get("/api/dag/nodes/settings/value").status_code,
                        404,
                    )

    def test_live_shell_run_resumes_from_job_completion(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root) / "shell-finished"
            with patch.dict(os.environ, {
                "ZEROPOINT_ROOT_PATH": root,
                "ZEROPOINT_MODE": "live",
                "ZEROPOINT_XDS_PORT": "0",
            }, clear=False):
                app = create_app()
                with TestClient(app) as client:
                    added = client.post("/api/dag/nodes", json={
                        "id": "script",
                        "type": "shell",
                        "config": {
                            "exec_cmd": f"sleep 0.1; touch {marker}",
                            "verify_cmd": f"test -f {marker}",
                            "timeout": 2,
                        },
                        "parents": [],
                        "perms": "rwd",
                    })
                    self.assertEqual(added.status_code, 200, added.text)
                    started = client.post("/api/graph/resolve", json={})
                    self.assertEqual(started.status_code, 200, started.text)
                    run_id = started.json()["run_id"]
                    self.assertEqual(started.json()["mode"], "live")
                    self.assertFalse(started.json()["needs_reboot"])
                    self.assertEqual(
                        client.get("/api/graph").status_code,
                        200,
                    )
                    self.assertEqual(
                        client.get("/api/dag").status_code,
                        200,
                    )
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        run = client.get(f"/api/runs/{run_id}").json()
                        if run["status"] == "succeeded":
                            break
                        time.sleep(0.02)
                    else:
                        self.fail("live shell graph run did not complete")
                    self.assertTrue(marker.exists())
                    with sqlite3.connect(Path(root) / "data" / "state.db") as connection:
                        job_status = connection.execute(
                            "SELECT status FROM jobs"
                        ).fetchone()[0]
                        controls = connection.execute(
                            """
                            SELECT id, cancellation_kind, pause_kind,
                                   progress_kind, checkpoint_kind
                            FROM promises
                            """
                        ).fetchone()
                    self.assertEqual(job_status, "succeeded")
                    self.assertEqual(controls[1], "shell-process-group")
                    self.assertEqual(controls[2], "unsupported")
                    self.assertEqual(controls[3], "shell-phases")
                    self.assertEqual(controls[4], "unsupported")
                    handle = app.state.jobs.promise_handle(controls[0])
                    self.assertEqual(
                        type(handle.cancellation).__name__,
                        "ShellScriptCancellation",
                    )
                    self.assertFalse(hasattr(handle.pausing, "pause"))

    def test_api_uses_state_db_and_rehydrates_nodes(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {
                "ZEROPOINT_ROOT_PATH": root,
                "ZEROPOINT_MODE": "mock",
                "ZEROPOINT_XDS_PORT": "0",
            }, clear=False):
                app = create_app()
                with TestClient(app) as client:
                    response = client.post("/api/dag/nodes", json={
                        "id": "settings",
                        "type": "namespace",
                        "config": {"name": "settings"},
                        "parents": [],
                        "perms": "rwd",
                    })
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertTrue(app.state.store.has_node("settings"))
                    meta = client.get("/api/meta")
                    self.assertEqual(meta.status_code, 200, meta.text)
                    first_agent_id = meta.json()["agent_id"]
                    self.assertTrue(first_agent_id.startswith("zp_"))
                    self.assertEqual(meta.json()["security_mode"], "trusted_lan")
                    variable = client.post("/api/dag/nodes", json={
                        "id": "settings/value",
                        "type": "var",
                        "config": {"name": "value", "value": "old"},
                        "parents": ["settings"],
                        "perms": "rwd",
                    })
                    self.assertEqual(variable.status_code, 200, variable.text)

                    resolved = client.post(
                        "/api/runs/resolve", json={"mode": "mock"}
                    )
                    self.assertEqual(resolved.status_code, 202, resolved.text)
                    run_id = resolved.json()["run_id"]
                    self.assertEqual(resolved.json()["status"], "succeeded")
                    run = client.get(f"/api/runs/{run_id}")
                    self.assertEqual(run.status_code, 200, run.text)
                    self.assertEqual(run.json()["summary"]["success"], 2)

                    updated = client.put(
                        "/api/dag/settings/value",
                        json={"config": {"value": "new"}},
                    )
                    self.assertEqual(updated.status_code, 200, updated.text)
                    client.post("/api/dag/resolve", json={"mode": "mock"})
                    historical = client.get(f"/api/runs/{run_id}").json()
                    old_value = next(
                        node["output"]["value"]
                        for node in historical["nodes"]
                        if node["id"] == "settings/value"
                    )
                    self.assertEqual(old_value, "old")

                state_db = Path(root) / "data" / "state.db"
                self.assertTrue(state_db.exists())

                with sqlite3.connect(state_db) as connection:
                    count = connection.execute(
                        "SELECT COUNT(*) FROM nodes"
                    ).fetchone()[0]
                self.assertEqual(count, 2)

                second_app = create_app()
                with TestClient(second_app) as client:
                    response = client.get("/api/dag/nodes/settings")
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["id"], "settings")
                    self.assertEqual(
                        client.get("/api/meta").json()["agent_id"],
                        first_agent_id,
                    )


if __name__ == "__main__":
    unittest.main()
