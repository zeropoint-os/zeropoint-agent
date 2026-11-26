"""Tailscale root, interactive enrollment, Serve ports, and ingress tests."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from zeropoint_agent.dag import DAG
from zeropoint_agent.graph_runner import GraphRunner
from zeropoint_agent.inode import NodeStatus, ResolveMode
from zeropoint_agent.integrations.tailscale import manager
from zeropoint_agent.integrations.tailscale.jobs import execute_tailscale_enroll_job
from zeropoint_agent.integrations.tailscale.nodes import (
    ENROLLMENT_MAX_ATTEMPTS,
    SYSTEM_TAILSCALE_NODE_ID,
    TAILNET_HTTPS_PORT_MIN,
    SystemTailscale,
    SystemTailscaleResult,
    TailnetExposure,
    effective_allowed_users,
    system_tailscale_result,
)
from zeropoint_agent.interaction import interaction_from_checkpoint
from zeropoint_agent.jobs import JobManager
from zeropoint_agent.nodes.config.namespace import Namespace
from zeropoint_agent.nodes.config.var import Var
from zeropoint_agent.nodes.system.docker import SystemDocker
from zeropoint_agent.nodes.system.envoy import SystemEnvoy, SystemEnvoyResult
from zeropoint_agent.nodes.user.exposure import Exposure, SYSTEM_ENVOY_NODE_ID
from zeropoint_agent.nodes.user.service import Service, ServiceResult
from zeropoint_agent.promise_controls import JobCancellation, JobCheckpoint, JobProgress
from zeropoint_agent.server import create_app
from zeropoint_agent.sqlite_store import SQLiteGraphStore
from zeropoint_agent.xds.snapshot import (
    TAILNET_LISTENER_NAME,
    TAILNET_ROUTES_NAME,
    TAILSCALE_IDENTITY_HEADERS,
    ResolvedEndpoint,
    TailnetRoute,
    build_snapshot,
)

MACHINE_NAME = "zeropoint-sahal-velas"


class FakeCache:
    def __init__(self):
        self.routes = {}

    def set_tailnet_route(self, node_id, route):
        self.routes[node_id] = route

    def clear_tailnet_route(self, node_id):
        self.routes.pop(node_id, None)


class InteractiveContext:
    def __init__(self):
        self.cancellation = SimpleNamespace(
            requested=False,
            raise_if_requested=lambda: None,
        )
        self.checkpoints = []

    def progress(self, value, checkpoint=None):
        self.checkpoints.append((value, checkpoint))


def add_system_roots(dag: DAG) -> None:
    dag.add("system", Namespace(name="system"), perms="rw-")
    dag.add("system/docker", SystemDocker(), parents=["system"], perms="r--")
    dag.add(
        SYSTEM_ENVOY_NODE_ID,
        SystemEnvoy(),
        parents=["system", "system/docker"],
        perms="r--",
    )


def add_http_exposure(dag: DAG, slug: str) -> tuple[str, str]:
    if "modules" not in dag.nodes:
        dag.add("modules", Namespace(name="modules"), perms="rwd")
    module_id = f"modules/{slug}"
    ports_id = f"{module_id}/ports"
    service_id = f"{ports_id}/http"
    exposure_id = f"{service_id}/exposure_{slug}"
    dag.add(module_id, Namespace(name=slug), parents=["modules"])
    dag.add(
        ports_id,
        Var(name="ports", value={
            "http": {"port": 8080, "protocol": "http"},
        }),
        parents=[module_id],
    )
    dag.add(service_id, Service(name="http", key="http"), parents=[ports_id])
    dag.get(service_id).output = ServiceResult(
        name="http", port=8080, protocol="http", value=8080,
    )
    dag.get(service_id).status = NodeStatus.SUCCESS
    dag.add(
        exposure_id,
        Exposure(name=slug),
        parents=[service_id, SYSTEM_ENVOY_NODE_ID],
        perms="r--",
    )
    return service_id, exposure_id


def add_tailscale_nodes(dag: DAG, exposure_id: str, port: int) -> str:
    if SYSTEM_TAILSCALE_NODE_ID not in dag.nodes:
        dag.add(
            SYSTEM_TAILSCALE_NODE_ID,
            SystemTailscale(),
            parents=["system", "system/docker", SYSTEM_ENVOY_NODE_ID],
            perms="rw-",
        )
    node_id = f"{exposure_id}/tailnet"
    dag.add(
        node_id,
        TailnetExposure(slug="qbittorrent", https_port=port),
        parents=[exposure_id, SYSTEM_TAILSCALE_NODE_ID],
        perms="rw-",
    )
    return node_id


class TailscaleExposureTests(unittest.TestCase):
    def test_effective_users_include_owner_global_and_exposure_users(self):
        self.assertEqual(
            effective_allowed_users(
                "Owner@Example.com",
                ["global@example.com"],
                ["app@example.com"],
                [],
            ),
            ["app@example.com", "global@example.com", "owner@example.com"],
        )

    def test_deny_removes_inherited_non_owner_but_never_owner(self):
        self.assertEqual(
            effective_allowed_users(
                "owner@example.com",
                ["global@example.com", "owner@example.com"],
                ["app@example.com"],
                ["global@example.com", "app@example.com", "owner@example.com"],
            ),
            ["owner@example.com"],
        )

    def test_ownerless_node_defaults_to_configured_users_only(self):
        self.assertEqual(
            effective_allowed_users(
                "",
                ["global@example.com"],
                ["app@example.com"],
                ["global@example.com"],
            ),
            ["app@example.com"],
        )
        self.assertEqual(effective_allowed_users("", [], [], []), [])

    def test_runtime_uses_linked_envoy_contract_and_persists_generation(self):
        captured = {}
        generation = "101@2026-11-25T00:00:00Z"

        class Envoy:
            id = "envoy-id"
            attrs = {"State": {"Pid": 101, "StartedAt": "2026-11-25T00:00:00Z"}}

            def reload(self):
                pass

        class Tail:
            status = "running"

            def reload(self):
                pass

            def remove(self, force=False):
                pass

        class Containers:
            def get(self, name):
                if name == "envoy-id":
                    return Envoy()
                raise RuntimeError("not found")

            def run(self, **kwargs):
                captured.update(kwargs)
                return Tail()

        contract = {
            "state": "running",
            "container_id": "envoy-id",
            "runtime_generation": generation,
        }
        with (
            patch.object(manager, "_docker_client", return_value=SimpleNamespace(containers=Containers())),
            patch.object(manager, "_ensure_image"),
            patch.object(manager, "_state_dir", return_value=Path("/state")),
            patch.object(manager, "_container_status", return_value={"authenticated": False}),
        ):
            status = manager.ensure_runtime(MACHINE_NAME, contract)
        self.assertEqual(captured["image"], manager.IMAGE)
        self.assertEqual(captured["network_mode"], "container:envoy-id")
        self.assertEqual(captured["volumes"]["/state"]["bind"], "/var/lib/tailscale")
        self.assertEqual(
            captured["labels"][manager.ENVOY_GENERATION_LABEL],
            generation,
        )
        self.assertFalse(status["authenticated"])

    def test_same_envoy_id_changed_generation_recreates_but_unchanged_is_idempotent(self):
        current_generation = "202@2026-11-25T01:00:00Z"

        class Envoy:
            id = "envoy-id"
            attrs = {"State": {"Pid": 202, "StartedAt": "2026-11-25T01:00:00Z"}}

            def reload(self):
                pass

        class Tail:
            status = "running"

            def __init__(self, generation):
                self.removed = False
                self.attrs = {
                    "HostConfig": {"NetworkMode": "container:envoy-id"},
                    "Config": {
                        "Image": manager.IMAGE,
                        "Labels": {manager.ENVOY_GENERATION_LABEL: generation},
                    },
                }

            def reload(self):
                pass

            def remove(self, force=False):
                self.removed = True

        contract = {
            "state": "running",
            "container_id": "envoy-id",
            "runtime_generation": current_generation,
        }

        def reconcile(existing_generation):
            existing = Tail(existing_generation)
            created = []

            class Containers:
                def get(self, name):
                    if name == "envoy-id":
                        return Envoy()
                    if name == manager.CONTAINER_NAME:
                        return existing
                    raise RuntimeError("not found")

                def run(self, **kwargs):
                    tail = Tail(current_generation)
                    created.append((tail, kwargs))
                    return tail

            with (
                patch.object(manager, "_docker_client", return_value=SimpleNamespace(containers=Containers())),
                patch.object(manager, "_ensure_image"),
                patch.object(manager, "_container_status", return_value={"authenticated": False}),
            ):
                manager.ensure_runtime(MACHINE_NAME, contract)
            return existing, created

        unchanged, unchanged_created = reconcile(current_generation)
        self.assertFalse(unchanged.removed)
        self.assertEqual(unchanged_created, [])

        changed, changed_created = reconcile("101@2026-11-25T00:00:00Z")
        self.assertTrue(changed.removed)
        self.assertEqual(len(changed_created), 1)
        self.assertEqual(
            changed_created[0][1]["labels"][manager.ENVOY_GENERATION_LABEL],
            current_generation,
        )

    def test_graph_orders_envoy_before_tailscale(self):
        dag = DAG()
        add_system_roots(dag)
        dag.add(
            SYSTEM_TAILSCALE_NODE_ID,
            SystemTailscale(),
            parents=["system", "system/docker", SYSTEM_ENVOY_NODE_ID],
        )
        self.assertIn(SYSTEM_ENVOY_NODE_ID, dag.get(SYSTEM_TAILSCALE_NODE_ID).parents)
        self.assertLess(
            dag._order.index(SYSTEM_ENVOY_NODE_ID),
            dag._order.index(SYSTEM_TAILSCALE_NODE_ID),
        )

    def test_rehydrated_root_consumes_updated_envoy_parent_generation(self):
        with tempfile.TemporaryDirectory() as root:
            store = SQLiteGraphStore(os.path.join(root, "state.db"))
            dag = DAG(store=store)
            dag.machine_name = MACHINE_NAME
            add_system_roots(dag)
            dag.add(
                SYSTEM_TAILSCALE_NODE_ID,
                SystemTailscale(),
                parents=["system", "system/docker", SYSTEM_ENVOY_NODE_ID],
            )
            store.close()

            reopened = SQLiteGraphStore(os.path.join(root, "state.db"))
            loaded = DAG(store=reopened)
            loaded.machine_name = MACHINE_NAME
            updated = SystemEnvoyResult(
                state="running",
                container_id="same-envoy-id",
                runtime_generation="202@new-start",
            )
            submitted = []
            context = SimpleNamespace(
                jobs=SimpleNamespace(
                    submit=lambda ctx, spec: submitted.append(spec) or "pending"
                )
            )
            with patch.object(
                manager,
                "ensure_runtime",
                return_value={"state": "running", "authenticated": False},
            ) as ensured:
                loaded.get(SYSTEM_TAILSCALE_NODE_ID).node.resolve_promise(
                    {SYSTEM_ENVOY_NODE_ID: updated},
                    ResolveMode.LIVE,
                    context,
                )
            ensured.assert_called_once_with(
                MACHINE_NAME,
                {
                    "state": "running",
                    "container_id": "same-envoy-id",
                    "runtime_generation": "202@new-start",
                },
                manager.IMAGE,
            )
            self.assertEqual(
                submitted[0].arguments["envoy"]["runtime_generation"],
                "202@new-start",
            )
            reopened.close()

    def test_authenticated_status_clears_stale_enrollment_material(self):
        payload = {
            "BackendState": "Running",
            "Self": {
                "TailscaleIPs": ["100.64.0.1"],
                "DNSName": "node.example.ts.net.",
                "UserID": 42,
            },
            "User": {
                "42": {"LoginName": "Owner@Example.com"},
            },
        }

        class Container:
            status = "running"
            short_id = "tailscale"
            image = SimpleNamespace(tags=[])

            def exec_run(self, command, stdout=True, stderr=True):
                return SimpleNamespace(exit_code=0, output=json.dumps(payload).encode())

            def logs(self, tail=200):
                return b"https://login.tailscale.com/a/stale\nstale error"

        status = manager._container_status(Container())
        self.assertTrue(status["authenticated"])
        self.assertEqual(status["login_url"], "")
        self.assertEqual(status["error"], "")
        self.assertEqual(status["primary_user_login"], "owner@example.com")
        result = system_tailscale_result({
            "authenticated": True,
            "login_url": "https://login.tailscale.com/a/stale",
            "error": "stale",
        })
        self.assertEqual(result.error, "")

    def test_stopped_backend_ignores_stale_identity_and_dispatches_enrollment(self):
        payload = {
            "BackendState": "Stopped",
            "Self": {
                "TailscaleIPs": ["100.64.0.1"],
                "DNSName": "stale.example.ts.net.",
                "Tags": ["tag:zeropoint"],
            },
            "CurrentTailnet": {
                "Name": "stale-tailnet",
                "MagicDNSSuffix": "stale.ts.net",
            },
        }

        class Container:
            status = "running"
            short_id = "tailscale"
            image = SimpleNamespace(tags=[])

            def exec_run(self, command, stdout=True, stderr=True):
                return SimpleNamespace(exit_code=0, output=json.dumps(payload).encode())

        status = manager._container_status(Container())
        self.assertFalse(status["authenticated"])
        self.assertEqual(status["node_dns_name"], "")
        self.assertEqual(status["tailnet"], "")
        self.assertEqual(status["magic_dns_suffix"], "")
        self.assertEqual(status["login_url"], "")
        self.assertEqual(status["health"], "enrollment_required")
        self.assertNotIn("tags", status)
        payload["BackendState"] = "NeedsLogin"
        needs_login = manager._container_status(Container())
        self.assertFalse(needs_login["authenticated"])
        self.assertEqual(needs_login["node_dns_name"], "")
        self.assertEqual(needs_login["health"], "enrollment_required")

        dag = DAG()
        dag.machine_name = MACHINE_NAME
        add_system_roots(dag)
        dag.add(
            SYSTEM_TAILSCALE_NODE_ID,
            SystemTailscale(),
            parents=["system", "system/docker", SYSTEM_ENVOY_NODE_ID],
        )
        submitted = []
        context = SimpleNamespace(
            jobs=SimpleNamespace(
                submit=lambda ctx, spec: submitted.append(spec) or "pending"
            )
        )
        envoy = SystemEnvoyResult(
            state="running",
            container_id="envoy-id",
            runtime_generation="101@start",
        )
        with patch.object(manager, "ensure_runtime", return_value=status):
            result = dag.get(SYSTEM_TAILSCALE_NODE_ID).node.resolve_promise(
                {SYSTEM_ENVOY_NODE_ID: envoy},
                ResolveMode.LIVE,
                context,
            )
        self.assertEqual(result, "pending")
        self.assertEqual(submitted[0].kind, "tailscale.enroll")

    def test_enrollment_streams_url_qr_and_clears_stale_attempt(self):
        commands = []

        class Container:
            id = "tailscale-id"

            def top(self):
                return {"Processes": []}

        class Api:
            def exec_create(self, container_id, command, stdout=True, stderr=True):
                commands.append(command)
                return {"Id": f"exec-{len(commands)}"}

            def exec_start(self, exec_id, stream=True):
                return iter([
                    b"Authorize at https://login.tailscale.com/a/example\n",
                    b"[small QR]\n",
                ])

        client = SimpleNamespace(
            containers=SimpleNamespace(get=lambda name: Container()),
            api=Api(),
        )

        def attempt():
            context = InteractiveContext()
            with (
                patch.object(manager, "ensure_runtime", return_value={"state": "running", "authenticated": False}),
                patch.object(manager, "_docker_client", return_value=client),
                patch.object(manager, "_container_status", return_value={"authenticated": True}),
            ):
                manager.enroll_interactively(
                    context,
                    MACHINE_NAME,
                    {
                        "state": "running",
                        "container_id": "envoy-id",
                        "runtime_generation": "101@start",
                    },
                )
            interactions = [
                interaction_from_checkpoint(value)
                for _, value in context.checkpoints
                if interaction_from_checkpoint(value)
            ]
            return context, interactions

        first, first_interactions = attempt()
        second, second_interactions = attempt()
        self.assertEqual(first.checkpoints[0], (0.0, {"phase": "starting"}))
        self.assertEqual(second.checkpoints[0], (0.0, {"phase": "starting"}))
        self.assertNotEqual(first_interactions[0]["id"], second_interactions[0]["id"])
        self.assertEqual(first_interactions[0]["kind"], "url_qr")
        self.assertIn("[small QR]", first_interactions[-1]["qr"])
        self.assertEqual(commands, [
            manager.enrollment_command(MACHINE_NAME),
            manager.enrollment_command(MACHINE_NAME),
        ])

    def test_ordinary_serve_publish_and_remove_commands(self):
        commands = []

        class Container:
            def exec_run(self, command, stdout=True, stderr=True):
                commands.append(command)
                if "off" not in command and "--bg" not in command:
                    threading.Event().wait(1)
                return SimpleNamespace(exit_code=0, output=b"")

        client = SimpleNamespace(containers=SimpleNamespace(get=lambda name: Container()))
        result = {}
        with patch.object(manager, "_docker_client", return_value=client):
            worker = threading.Thread(
                target=lambda: result.update(manager.publish_port(10000)),
            )
            worker.start()
            worker.join(0.2)
            self.assertFalse(worker.is_alive(), "Serve publish remained foreground")
            self.assertTrue(result["ok"])
            self.assertTrue(manager.remove_port(10000)["ok"])
        self.assertEqual(commands, [
            [
                "tailscale", "serve", "--yes", "--bg", "--https=10000",
                "http://127.0.0.1:18080/__zeropoint/tailnet/10000/",
            ],
            [
                "tailscale", "serve", "--yes", "--bg", "--https=10000",
                "off",
            ],
        ])
        self.assertFalse(any("--service=" in part for command in commands for part in command))

    def test_snapshot_uses_loopback_boundary_and_rewrites_each_local_host(self):
        endpoints = [
            ResolvedEndpoint(
                id="modules/qbittorrent/ports/http",
                name="qbittorrent-zeropoint-sahal-velas.local",
                protocol="http",
                container="qbittorrent-main",
                container_port=8080,
                host_port=0,
            ),
            ResolvedEndpoint(
                id="modules/youtube-dl/ports/http",
                name="youtube-dl-zeropoint-sahal-velas.local",
                protocol="http",
                container="youtube-dl-main",
                container_port=8090,
                host_port=0,
            ),
        ]
        routes = [
            TailnetRoute(
                id="qbittorrent-tailnet",
                endpoint_id=endpoints[0].id,
                path_prefix=manager.private_route_prefix(10000),
                allowed_users=("owner@example.com",),
            ),
            TailnetRoute(
                id="youtube-dl-tailnet",
                endpoint_id=endpoints[1].id,
                path_prefix=manager.private_route_prefix(10001),
                allowed_users=("friend@example.com",),
            ),
        ]
        snapshot = build_snapshot("1", endpoints, routes)
        private_listener = next(
            listener for listener in snapshot.listeners
            if listener.name == TAILNET_LISTENER_NAME
        )
        self.assertEqual(private_listener.address.socket_address.address, "127.0.0.1")
        for vhost in snapshot.routes[0].virtual_hosts:
            self.assertEqual(
                vhost.request_headers_to_remove,
                TAILSCALE_IDENTITY_HEADERS,
            )
        private = next(
            config for config in snapshot.routes
            if config.name == TAILNET_ROUTES_NAME
        )
        allowed_routes = {
            route.match.prefix: route
            for route in private.virtual_hosts[0].routes
            if route.route is not None and route.route.cluster
        }
        for route, endpoint in zip(routes, endpoints):
            allowed = allowed_routes[route.path_prefix]
            self.assertEqual(allowed.route.prefix_rewrite, "/")
            self.assertEqual(allowed.route.host_rewrite_literal, endpoint.name)
            self.assertEqual(
                allowed.match.headers[0].name,
                "tailscale-user-login",
            )
            self.assertEqual(
                allowed.match.headers[0].string_match.exact,
                route.allowed_users[0],
            )
            self.assertTrue(allowed.match.headers[0].string_match.ignore_case)
        denied = [
            route for route in private.virtual_hosts[0].routes
            if route.direct_response is not None
            and route.direct_response.status == 403
        ]
        self.assertEqual(len(denied), 2)
        self.assertTrue(all(not route.match.headers for route in denied))
        self.assertTrue(all(
            route.direct_response.body.inline_string == "Forbidden\n"
            for route in denied
        ))
        self.assertEqual(private.virtual_hosts[0].request_headers_to_remove, [])

    def test_ports_persist_and_are_unique(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "state.db")
            store = SQLiteGraphStore(path)
            dag = DAG(store=store)
            add_system_roots(dag)
            _, first_exposure = add_http_exposure(dag, "qbittorrent")
            _, second_exposure = add_http_exposure(dag, "youtube-dl")
            first = add_tailscale_nodes(dag, first_exposure, 10000)
            second = add_tailscale_nodes(dag, second_exposure, 10001)
            store.close()
            reopened = SQLiteGraphStore(path)
            try:
                loaded = DAG(store=reopened)
                self.assertEqual(loaded.get(first).node.https_port, 10000)
                self.assertEqual(loaded.get(second).node.https_port, 10001)
            finally:
                reopened.close()

    def test_root_enrollment_job_recovery_is_bounded_at_32(self):
        dag = DAG()
        dag.machine_name = MACHINE_NAME
        add_system_roots(dag)
        dag.add(
            SYSTEM_TAILSCALE_NODE_ID,
            SystemTailscale(),
            parents=["system", "system/docker", SYSTEM_ENVOY_NODE_ID],
        )
        node = dag.get(SYSTEM_TAILSCALE_NODE_ID).node
        submitted = []
        context = SimpleNamespace(
            jobs=SimpleNamespace(submit=lambda ctx, spec: submitted.append(spec) or "pending")
        )
        envoy = SystemEnvoyResult(
            state="running",
            container_id="envoy-id",
            runtime_generation="101@start",
        )
        with patch.object(manager, "ensure_runtime", return_value={"state": "running", "authenticated": False}):
            self.assertEqual(
                node.resolve_promise(
                    {SYSTEM_ENVOY_NODE_ID: envoy},
                    ResolveMode.LIVE,
                    context,
                ),
                "pending",
            )
        self.assertEqual(submitted[0].kind, "tailscale.enroll")
        self.assertEqual(submitted[0].max_attempts, ENROLLMENT_MAX_ATTEMPTS)
        self.assertEqual(ENROLLMENT_MAX_ATTEMPTS, 32)

    def test_tailnet_child_publishes_node_url_and_private_route(self):
        dag = DAG()
        dag.machine_name = MACHINE_NAME
        dag.xds_cache = FakeCache()
        add_system_roots(dag)
        service_id, exposure_id = add_http_exposure(dag, "qbittorrent")
        child_id = add_tailscale_nodes(dag, exposure_id, 10000)
        child = dag.get(child_id).node
        child.allowed_users = ["app@example.com"]
        child.denied_users = ["global@example.com", "owner@example.com"]
        root = SystemTailscaleResult(
            authenticated=True,
            node_dns_name="zeropoint-sahal-velas.example.ts.net",
            primary_user_login="owner@example.com",
            allowed_users=["global@example.com"],
        )
        with patch.object(manager, "publish_port", return_value={"ok": True}):
            result = dag.get(child_id).node.resolve(
                {SYSTEM_TAILSCALE_NODE_ID: root},
                ResolveMode.LIVE,
            )
        self.assertEqual(result.status, NodeStatus.SUCCESS)
        self.assertEqual(
            result.output.url,
            "https://zeropoint-sahal-velas.example.ts.net:10000/",
        )
        self.assertEqual(dag.xds_cache.routes[child_id].endpoint_id, service_id)
        self.assertEqual(
            dag.xds_cache.routes[child_id].allowed_users,
            ("app@example.com", "owner@example.com"),
        )
        self.assertEqual(
            result.output.effective_allowed_users,
            ["app@example.com", "owner@example.com"],
        )

    def test_api_allocates_ports_atomically_and_matrix_reports_node_url(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {
                "ZEROPOINT_ROOT_PATH": root,
                "ZEROPOINT_MODE": "mock",
                "ZEROPOINT_XDS_PORT": "0",
                "ZEROPOINT_MDNS_ADVERTISE": "0",
                "ZEROPOINT_SEED_ROOTS": "1",
                "ZEROPOINT_AGENT_NAME": MACHINE_NAME,
            }, clear=False):
                with TestClient(create_app()) as client:
                    dag = client.app.state.dag
                    _, first = add_http_exposure(dag, "qbittorrent")
                    _, second = add_http_exposure(dag, "youtube-dl")
                    with patch(
                        "zeropoint_agent.handlers.tailscale._start_resolve",
                        side_effect=["run-1", "run-2"],
                    ):
                        first_response = client.post("/api/tailscale/expose", json={
                            "exposure_id": first,
                            "allowed_users": ["App@Example.com"],
                            "denied_users": ["blocked@example.com"],
                        })
                        second_response = client.post("/api/tailscale/expose", json={"exposure_id": second})
                    self.assertEqual(first_response.json()["https_port"], TAILNET_HTTPS_PORT_MIN)
                    self.assertEqual(first_response.json()["allowed_users"], ["app@example.com"])
                    self.assertEqual(first_response.json()["denied_users"], ["blocked@example.com"])
                    self.assertEqual(second_response.json()["https_port"], TAILNET_HTTPS_PORT_MIN + 1)
                    first_node = dag.get(first_response.json()["node_id"])
                    self.assertEqual(first_node.node.allowed_users, ["app@example.com"])
                    self.assertEqual(first_node.node.denied_users, ["blocked@example.com"])
                    first_node.output = first_node.node._output(
                        SystemTailscaleResult(
                            authenticated=True,
                            node_dns_name="zeropoint-sahal-velas.example.ts.net",
                            primary_user_login="owner@example.com",
                        ),
                        status="published",
                        healthy=True,
                    )
                    matrix = client.get("/api/tailscale/matrix").json()["rows"]
                    qb = next(row for row in matrix if row["slug"] == "qbittorrent")
                    self.assertEqual(qb["tailnet"]["https_port"], 10000)
                    self.assertEqual(
                        qb["tailnet"]["url"],
                        "https://zeropoint-sahal-velas.example.ts.net:10000/",
                    )
                    self.assertEqual(
                        qb["tailnet"]["effective_allowed_users"],
                        ["app@example.com", "owner@example.com"],
                    )

    def test_policy_endpoints_update_legacy_readonly_nodes_and_reconcile(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {
                "ZEROPOINT_ROOT_PATH": root,
                "ZEROPOINT_MODE": "mock",
                "ZEROPOINT_XDS_PORT": "0",
                "ZEROPOINT_MDNS_ADVERTISE": "0",
                "ZEROPOINT_SEED_ROOTS": "1",
            }, clear=False):
                with TestClient(create_app()) as client:
                    dag = client.app.state.dag
                    _, exposure_id = add_http_exposure(dag, "qbittorrent")
                    child_id = add_tailscale_nodes(dag, exposure_id, 10000)
                    dag.get(SYSTEM_TAILSCALE_NODE_ID).perms = "r-d"
                    dag.get(child_id).perms = "r-d"
                    with patch(
                        "zeropoint_agent.handlers.tailscale._start_resolve",
                        side_effect=["run-global", "run-exposure"],
                    ):
                        global_policy = client.put(
                            "/api/tailscale/policy",
                            json={"allowed_users": ["Global@Example.com"]},
                        )
                        exposure_policy = client.put(
                            "/api/tailscale/expose/policy",
                            json={
                                "exposure_id": exposure_id,
                                "allowed_users": ["App@Example.com"],
                                "denied_users": ["Global@Example.com"],
                            },
                        )
                    self.assertEqual(global_policy.status_code, 200)
                    self.assertEqual(exposure_policy.status_code, 200)
                    self.assertEqual(
                        dag.get(SYSTEM_TAILSCALE_NODE_ID).node.allowed_users,
                        ["global@example.com"],
                    )
                    self.assertEqual(
                        dag.get(child_id).node.allowed_users,
                        ["app@example.com"],
                    )
                    self.assertEqual(
                        dag.get(child_id).node.denied_users,
                        ["global@example.com"],
                    )
                    self.assertEqual(dag.get(SYSTEM_TAILSCALE_NODE_ID).status, NodeStatus.PENDING)
                    self.assertEqual(dag.get(child_id).status, NodeStatus.PENDING)

    def test_reverse_removal_preserves_local_exposure(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {
                "ZEROPOINT_ROOT_PATH": root,
                "ZEROPOINT_MODE": "mock",
                "ZEROPOINT_XDS_PORT": "0",
                "ZEROPOINT_MDNS_ADVERTISE": "0",
                "ZEROPOINT_SEED_ROOTS": "1",
            }, clear=False):
                with TestClient(create_app()) as client:
                    dag = client.app.state.dag
                    _, exposure_id = add_http_exposure(dag, "qbittorrent")
                    child_id = add_tailscale_nodes(dag, exposure_id, 10000)
                    order = []
                    original_child = TailnetExposure.remove
                    original_root = SystemTailscale.remove

                    def child_remove(instance, mode):
                        order.append("child")
                        return original_child(instance, mode)

                    def root_remove(instance, mode):
                        order.append("root")
                        return original_root(instance, mode)

                    with (
                        patch.object(TailnetExposure, "remove", child_remove),
                        patch.object(SystemTailscale, "remove", root_remove),
                    ):
                        response = client.delete("/api/tailscale")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(order, ["child", "root"])
                    self.assertIn(exposure_id, dag.nodes)
                    self.assertNotIn(child_id, dag.nodes)


if __name__ == "__main__":
    unittest.main()
