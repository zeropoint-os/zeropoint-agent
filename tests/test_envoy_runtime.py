"""Envoy runtime-generation output contract tests."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from zeropoint_agent.envoy_manager import _container_info
from zeropoint_agent.inode import NodeStatus, ResolveMode
from zeropoint_agent.nodes.system.envoy import SystemEnvoy


class EnvoyRuntimeContractTests(unittest.TestCase):
    def test_container_info_uses_full_id_and_process_generation(self):
        container = SimpleNamespace(
            id="full-envoy-container-id",
            status="running",
            image=SimpleNamespace(tags=["envoy:test"]),
            attrs={
                "State": {
                    "Pid": 4242,
                    "StartedAt": "2026-11-25T01:02:03Z",
                },
            },
            reload=lambda: None,
        )
        info = _container_info(container)
        self.assertEqual(info["id"], "full-envoy-container-id")
        self.assertEqual(
            info["runtime_generation"],
            "4242@2026-11-25T01:02:03Z",
        )

    def test_system_envoy_emits_runtime_generation(self):
        node = SystemEnvoy()
        with (
            patch("zeropoint_agent.envoy_manager.ensure_envoy"),
            patch(
                "zeropoint_agent.envoy_manager.envoy_status",
                return_value={
                    "state": "running",
                    "image": "envoy:test",
                    "id": "full-envoy-id",
                    "runtime_generation": "123@started",
                },
            ),
        ):
            result = node.resolve(None, ResolveMode.LIVE)
        self.assertEqual(result.status, NodeStatus.SUCCESS)
        self.assertEqual(result.output.container_id, "full-envoy-id")
        self.assertEqual(result.output.runtime_generation, "123@started")


if __name__ == "__main__":
    unittest.main()
