"""Exposure routing and transactional Var linking behavior."""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from fastapi import HTTPException
from pydantic import ValidationError

from zeropoint_agent.handlers.expose import (
    ExposeRequest,
    _validate_http_hostname,
)
from zeropoint_agent.mdns import MdnsRegistry
from zeropoint_agent.nodes.user.exposure import Exposure
from zeropoint_agent.server import create_app
from zeropoint_agent.xds.snapshot import (
    ResolvedEndpoint,
    make_route_config,
)


class ExposureAndLinkTests(unittest.TestCase):
    def test_http_exposures_use_dedicated_root_hostnames(self):
        config = make_route_config([
            ResolvedEndpoint(
                id="modules/echo/main_ports/http",
                name="echo",
                protocol="http",
                container="echo-main",
                container_port=8080,
                host_port=0,
            ),
            ResolvedEndpoint(
                id="modules/jellyfin/main_ports/web",
                name="jellyfin",
                protocol="http",
                container="jellyfin-main",
                container_port=8096,
                host_port=0,
            ),
        ])

        self.assertEqual(len(config.virtual_hosts), 2)
        by_name = {vhost.name: vhost for vhost in config.virtual_hosts}
        self.assertEqual(by_name["echo"].domains, ["echo", "echo.local"])
        self.assertEqual(by_name["jellyfin"].domains, [
            "jellyfin",
            "jellyfin.local",
        ])
        self.assertEqual(by_name["echo"].routes[0].match.prefix, "/")
        self.assertEqual(by_name["jellyfin"].routes[0].match.prefix, "/")

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
            _validate_http_hostname(
                request,
                dag,
                "zeropoint-node",
            )
        self.assertEqual(reserved.exception.status_code, 409)

        dag.nodes["existing"] = SimpleNamespace(
            node=Exposure(name="jellyfin")
        )
        with self.assertRaises(HTTPException) as conflict:
            _validate_http_hostname(request, dag, "jellyfin")
        self.assertEqual(conflict.exception.status_code, 409)

    def test_mdns_registry_publishes_exposure_hostname(self):
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

        registry = MdnsRegistry()
        with (
            patch(
                "zeropoint_agent.mdns._zeroconf",
                return_value=(FakeZeroconf, FakeInfo),
            ),
            patch("zeropoint_agent.mdns._host_ip", return_value="10.0.0.2"),
        ):
            registry.register("jellyfin", 80)
            self.assertEqual(
                registered[0][0].kwargs["server"],
                "jellyfin.local.",
            )
            self.assertTrue(registered[0][2])
            registry.unregister("jellyfin")
        self.assertEqual(unregistered, [registered[0][0]])

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
