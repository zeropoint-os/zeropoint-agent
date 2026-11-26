"""Exposure routing and transactional Var linking behavior."""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from zeropoint_agent.dag import DAG
from zeropoint_agent.handlers.expose import ExposeRequest, _validate_http_hostname
from zeropoint_agent.inode import NodeStatus, ResolveMode
from zeropoint_agent.mdns import MdnsRegistry
from zeropoint_agent.nodes.config.namespace import Namespace
from zeropoint_agent.nodes.config.var import Var, VarResult
from zeropoint_agent.nodes.system.envoy import SystemEnvoy
from zeropoint_agent.nodes.user.exposure import (
    Exposure,
    SYSTEM_ENVOY_NODE_ID,
    canonical_local_hostname,
)
from zeropoint_agent.nodes.user.service import Service, ServiceResult
from zeropoint_agent.server import create_app
from zeropoint_agent.sqlite_store import SQLiteGraphStore
from zeropoint_agent.xds.snapshot import build_snapshot


MACHINE_NAME = "zeropoint-sahal-velas"


def _add_http_service(dag: DAG, slug: str, port: int) -> str:
    module_id = f"modules/{slug}"
    ports_id = f"{module_id}/ports"
    service_id = f"{ports_id}/http"
    dag.add(module_id, Namespace(name=slug), parents=["modules"])
    dag.add(
        ports_id,
        Var(name="ports", value={
            "http": {"port": port, "protocol": "http"},
        }),
        parents=[module_id],
    )
    dag.add(
        service_id,
        Service(name="http", key="http"),
        parents=[ports_id],
    )
    dag.get(service_id).output = ServiceResult(
        name="http",
        port=port,
        protocol="http",
        value=port,
    )
    return service_id


class _EndpointCache:
    def __init__(self):
        self.entries = {}

    def set(self, node_id, endpoint):
        self.entries[node_id] = endpoint

    def clear(self, node_id):
        self.entries.pop(node_id, None)


class ExposureAndLinkTests(unittest.TestCase):
    def test_canonical_hostname_uses_slug_and_durable_machine_identity(self):
        self.assertEqual(
            canonical_local_hostname("qbittorrent", MACHINE_NAME),
            "qbittorrent-zeropoint-sahal-velas.local",
        )
        self.assertEqual(
            canonical_local_hostname("youtube-dl", f"{MACHINE_NAME}.local."),
            "youtube-dl-zeropoint-sahal-velas.local",
        )

    def test_two_http_exposures_share_listener_and_route_root_by_host(self):
        dag = DAG()
        dag.machine_name = MACHINE_NAME
        dag.xds_cache = _EndpointCache()
        dag.add("modules", Namespace(name="modules"), perms="rwd")
        dag.add("system", Namespace(name="system"), perms="rw-")
        dag.add(
            SYSTEM_ENVOY_NODE_ID,
            SystemEnvoy(),
            parents=["system"],
            perms="r--",
        )

        services = (
            (_add_http_service(dag, "qbittorrent", 8080), "qbittorrent", 8080),
            (_add_http_service(dag, "youtube-dl", 8090), "youtube-dl", 8090),
        )
        for service_id, slug, port in services:
            dag.add(
                f"{service_id}/exposure_{slug}",
                Exposure(name=slug),
                parents=[service_id, SYSTEM_ENVOY_NODE_ID],
                perms="r--",
                tags={"exposure"},
            )
            result = dag.get(service_id).node.resolve(
                VarResult(
                    name="ports",
                    value={"http": {"port": port, "protocol": "http"}},
                ),
                ResolveMode.MOCK,
            )
            self.assertEqual(result.status, NodeStatus.SUCCESS)

        snapshot = build_snapshot("1", list(dag.xds_cache.entries.values()))
        self.assertEqual(len(snapshot.listeners), 1)
        self.assertEqual(len(snapshot.clusters), 2)
        self.assertEqual(len(snapshot.routes), 1)

        by_name = {
            vhost.name: vhost
            for vhost in snapshot.routes[0].virtual_hosts
        }
        expected = {
            "qbittorrent-zeropoint-sahal-velas.local",
            "youtube-dl-zeropoint-sahal-velas.local",
        }
        self.assertEqual(set(by_name), expected)
        for hostname, vhost in by_name.items():
            self.assertEqual(vhost.domains, [hostname])
            self.assertEqual(vhost.routes[0].match.prefix, "/")
        self.assertNotIn("qbittorrent.local", by_name)
        self.assertNotIn("youtube-dl.local", by_name)

    def test_exposure_contract_rejects_paths_and_reserved_hostname(self):
        with self.assertRaises(ValidationError):
            ExposeRequest.model_validate({
                "service_id": "modules/jellyfin/main_ports/web",
                "name": "jellyfin",
                "path_prefix": "/jellyfin",
            })

        request = SimpleNamespace(app=SimpleNamespace(
            state=SimpleNamespace(
                identity=SimpleNamespace(name="zeropoint-node"),
            )
        ))
        dag = SimpleNamespace(nodes={})
        with self.assertRaises(HTTPException) as reserved:
            _validate_http_hostname(request, dag, "zeropoint-node")
        self.assertEqual(reserved.exception.status_code, 409)

        dag.nodes["existing"] = SimpleNamespace(
            node=Exposure(name="jellyfin")
        )
        with self.assertRaises(HTTPException) as conflict:
            _validate_http_hostname(request, dag, "jellyfin")
        self.assertEqual(conflict.exception.status_code, 409)

    def test_expose_api_returns_machine_scoped_url_and_keeps_collisions(self):
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
                    qbittorrent = _add_http_service(dag, "qbittorrent", 8080)
                    youtube_dl = _add_http_service(dag, "youtube-dl", 8090)
                    with patch(
                        "zeropoint_agent.handlers.expose._trigger_resolve",
                        new=AsyncMock(),
                    ):
                        invalid = client.post("/api/expose", json={
                            "service_id": qbittorrent,
                            "name": "qbittorrent",
                            "path_prefix": "/qbittorrent",
                        })
                        self.assertEqual(invalid.status_code, 422)

                        oversized = client.post("/api/expose", json={
                            "service_id": qbittorrent,
                            "name": "a" * 42,
                        })
                        self.assertEqual(oversized.status_code, 400)

                        exposed = client.post("/api/expose", json={
                            "service_id": qbittorrent,
                            "name": "qbittorrent",
                        })
                        self.assertEqual(exposed.status_code, 200, exposed.text)
                        body = exposed.json()
                        self.assertEqual(
                            body["hostname"],
                            "qbittorrent-zeropoint-sahal-velas.local",
                        )
                        self.assertEqual(
                            body["url"],
                            "http://qbittorrent-zeropoint-sahal-velas.local/",
                        )
                        self.assertEqual(body["name"], "qbittorrent")
                        exposure = dag.get(body["exposure_id"])
                        self.assertEqual(
                            exposure.parents,
                            [qbittorrent, SYSTEM_ENVOY_NODE_ID],
                        )

                        collision = client.post("/api/expose", json={
                            "service_id": youtube_dl,
                            "name": "qbittorrent",
                        })
                        self.assertEqual(collision.status_code, 409)

    def test_mdns_registry_publishes_canonical_hostname_once(self):
        registered = []
        unregistered = []

        class FakeInfo:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class FakeZeroconf:
            def register_service(
                self,
                info,
                allow_name_change=False,
                cooperating_responders=False,
            ):
                registered.append(
                    (info, allow_name_change, cooperating_responders)
                )

            def unregister_service(self, info):
                unregistered.append(info)

            def close(self):
                pass

        hostname = "qbittorrent-zeropoint-sahal-velas.local"
        self.assertEqual(hostname.count("."), 1)
        registry = MdnsRegistry()
        with self.assertRaisesRegex(ValueError, "must end in .local"):
            registry.register("qbittorrent", 80)
        with (
            patch(
                "zeropoint_agent.mdns._zeroconf",
                return_value=(FakeZeroconf, FakeInfo),
            ),
            patch("zeropoint_agent.mdns._host_ip", return_value="10.0.0.2"),
        ):
            registry.register(hostname, 80)
            self.assertEqual(
                registered[0][0].kwargs["server"],
                f"{hostname}.",
            )
            self.assertNotIn(".local.local", registered[0][0].kwargs["server"])
            self.assertTrue(registered[0][2])
            registry.unregister(hostname)
        self.assertEqual(unregistered, [registered[0][0]])

    def test_new_exposure_keeps_envoy_parent_across_restart(self):
        with tempfile.TemporaryDirectory() as root:
            db_path = os.path.join(root, "state.db")
            store = SQLiteGraphStore(db_path)
            dag = DAG(store=store)
            dag.add("modules", Namespace(name="modules"), perms="rwd")
            dag.add("system", Namespace(name="system"), perms="rw-")
            dag.add(
                SYSTEM_ENVOY_NODE_ID,
                SystemEnvoy(),
                parents=["system"],
                perms="r--",
            )
            service_id = _add_http_service(dag, "qbittorrent", 8080)
            exposure_id = f"{service_id}/exposure_qbittorrent"
            dag.add(
                exposure_id,
                Exposure(name="qbittorrent"),
                parents=[service_id, SYSTEM_ENVOY_NODE_ID],
                perms="r--",
                tags={"exposure"},
            )
            store.close()

            restarted_store = SQLiteGraphStore(db_path)
            try:
                restarted_dag = DAG(store=restarted_store)
                self.assertEqual(
                    restarted_dag.get(exposure_id).parents,
                    [service_id, SYSTEM_ENVOY_NODE_ID],
                )
                self.assertEqual(
                    canonical_local_hostname(
                        restarted_dag.get(exposure_id).node.name,
                        MACHINE_NAME,
                    ),
                    "qbittorrent-zeropoint-sahal-velas.local",
                )
            finally:
                restarted_store.close()

    def test_link_relink_and_unlink_propagate_values(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {
                "ZEROPOINT_ROOT_PATH": root,
                "ZEROPOINT_MODE": "mock",
                "ZEROPOINT_XDS_PORT": "0",
                "ZEROPOINT_MDNS_ADVERTISE": "0",
            }, clear=False):
                with TestClient(create_app()) as client:
                    for node_id, value in (
                        ("settings/source_a", "alpha"),
                        ("settings/source_b", "beta"),
                        ("settings/target", "initial"),
                    ):
                        if node_id == "settings/source_a":
                            response = client.post("/api/graph/nodes", json={
                                "id": "settings",
                                "type": "namespace",
                                "config": {"name": "settings"},
                                "parents": [],
                                "perms": "rwd",
                            })
                            self.assertEqual(response.status_code, 200)
                        response = client.post("/api/graph/nodes", json={
                            "id": node_id,
                            "type": "var",
                            "config": {
                                "name": node_id.rsplit("/", 1)[-1],
                                "value": value,
                            },
                            "parents": ["settings"],
                            "perms": "rwd",
                        })
                        self.assertEqual(response.status_code, 200, response.text)

                    linked = client.put(
                        "/api/links/settings/target",
                        json={"target": "settings/source_a"},
                    )
                    self.assertEqual(linked.status_code, 200, linked.text)
                    client.post("/api/graph/resolve", json={"mode": "mock"})
                    target = client.get(
                        "/api/graph/nodes/settings/target"
                    ).json()
                    self.assertEqual(target["output"]["value"], "alpha")

                    relinked = client.put(
                        "/api/links/settings/target",
                        json={"target": "settings/source_b"},
                    )
                    self.assertEqual(relinked.status_code, 200, relinked.text)
                    client.post("/api/graph/resolve", json={"mode": "mock"})
                    target = client.get(
                        "/api/graph/nodes/settings/target"
                    ).json()
                    self.assertEqual(target["output"]["value"], "beta")
                    self.assertIn("settings/source_b", target["parents"])
                    self.assertNotIn("settings/source_a", target["parents"])

                    unlinked = client.put(
                        "/api/links/settings/target",
                        json={"target": None},
                    )
                    self.assertEqual(unlinked.status_code, 200, unlinked.text)
                    updated = client.put(
                        "/api/graph/settings/target",
                        json={"config": {"value": "local"}},
                    )
                    self.assertEqual(updated.status_code, 200, updated.text)
                    client.post("/api/graph/resolve", json={"mode": "mock"})
                    target = client.get(
                        "/api/graph/nodes/settings/target"
                    ).json()
                    self.assertEqual(target["output"]["value"], "local")
                    self.assertNotIn("settings/source_b", target["parents"])


if __name__ == "__main__":
    unittest.main()
