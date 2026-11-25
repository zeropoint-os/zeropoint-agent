"""Live hardware roots and durable storage-node operations."""

from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from zeropoint_agent.dag import DAG
from zeropoint_agent.graph_runner import GraphRunner
from zeropoint_agent.hw_probe import Disk as ProbedDisk
from zeropoint_agent.hw_probe import Partition as ProbedPartition
from zeropoint_agent.inode import INode, NodeResult, NodeStatus, ResolveMode
from zeropoint_agent.jobs import JobManager
from zeropoint_agent.nodes.config.namespace import Namespace
from zeropoint_agent.nodes.hw.disks import Disks
from zeropoint_agent.nodes.hw.format import execute_format_job
from zeropoint_agent.nodes.hw.mount import MountResult, execute_mount_job
from zeropoint_agent.nodes.hw.mountdir import execute_mountdir_job
from zeropoint_agent.nodes.hw.partition import execute_partition_job
from zeropoint_agent.sqlite_store import SQLiteGraphStore


@dataclass
class ConsumerResult:
    mountpoint: str


@dataclass
class MountConsumer(INode[MountResult, ConsumerResult]):
    def resolve(self, input, mode):
        return NodeResult.success(ConsumerResult(input.mountpoint))

    def verify(self, mode):
        return NodeResult()

    def remove(self, mode):
        return NodeResult.success()


class HardwareGraphTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteGraphStore(
            str(Path(self.tempdir.name) / "state.db")
        )
        self.dag = DAG(store=self.store)
        self.jobs = JobManager(
            self.store,
            max_workers=2,
            poll_interval=0.01,
            lease_seconds=1,
        )
        for kind, executor in (
            ("node.partition", execute_partition_job),
            ("node.format", execute_format_job),
            ("node.mount", execute_mount_job),
            ("node.mountdir", execute_mountdir_job),
        ):
            self.jobs.register_executor(kind, executor)
        self.runner = GraphRunner(self.dag, self.store, self.jobs)
        self.jobs.start()

    def tearDown(self):
        self.jobs.stop()
        self.store.close()
        self.tempdir.cleanup()

    def wait_terminal(self, run_id: str, timeout: float = 3) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.runner.describe(run_id)
            if run["status"] in {
                "succeeded",
                "failed",
                "cancelled",
                "superseded",
            }:
                return run
            time.sleep(0.01)
        self.fail("hardware GraphRun did not become terminal")

    def _probe_disk(self) -> ProbedDisk:
        return ProbedDisk(
            id="disk-stable",
            device="/dev/testdisk",
            size=10 * 1024 * 1024 * 1024,
            free=5 * 1024 * 1024 * 1024,
            partitions=[
                ProbedPartition(
                    id="disk-stable-part1",
                    device="/dev/testdisk1",
                    size=5 * 1024 * 1024 * 1024,
                    number=1,
                    filesystem="ext4",
                    uuid="fs-uuid",
                    mountpoints=["/storage"],
                    mount_options={"/storage": "defaults"},
                )
            ],
        )

    def test_disk_root_produces_live_storage_subgraph(self):
        current = [self._probe_disk()]
        with (
            patch(
                "zeropoint_agent.nodes.hw.disks.HWProbe.get_disks",
                side_effect=lambda: list(current),
            ),
            patch(
                "zeropoint_agent.nodes.hw.disk.HWProbe.get_disks",
                side_effect=lambda: list(current),
            ),
            patch(
                "zeropoint_agent.nodes.hw._ops.device_for",
                side_effect=lambda stable_id: {
                    "disk-stable": "/dev/testdisk",
                    "disk-stable-part1": "/dev/testdisk1",
                }.get(stable_id),
            ),
            patch(
                "zeropoint_agent.nodes.hw._ops.fs_type",
                return_value="ext4",
            ),
            patch(
                "zeropoint_agent.nodes.hw._ops.fs_uuid",
                return_value="fs-uuid",
            ),
            patch(
                "zeropoint_agent.nodes.hw._ops.is_mounted",
                return_value=True,
            ),
        ):
            self.dag.add("hardware", Namespace("hardware"))
            self.dag.add(
                "hardware/disks",
                Disks(),
                parents=["hardware"],
            )
            run = self.runner.start(ResolveMode.LIVE)
            result = self.wait_terminal(run.id)

            self.assertEqual(result["status"], "succeeded")
            generated = {
                node_id: entry
                for node_id, entry in self.dag.nodes.items()
                if entry.producer_id == "hardware/disks"
            }
            self.assertEqual(len(generated), 4)
            self.assertTrue(any(node_id.endswith("/format") for node_id in generated))
            self.assertTrue(any("/mount_" in node_id for node_id in generated))
            self.assertTrue(all(
                entry.status == NodeStatus.SUCCESS
                for entry in generated.values()
            ))

            current.clear()
            second = self.runner.start(ResolveMode.LIVE)
            self.assertEqual(
                self.wait_terminal(second.id)["status"],
                "succeeded",
            )
            self.assertFalse(any(
                entry.producer_id == "hardware/disks"
                for entry in self.dag.nodes.values()
            ))

    def test_missing_referenced_hardware_remains_and_blocks_dependents(self):
        current = [self._probe_disk()]
        with (
            patch(
                "zeropoint_agent.nodes.hw.disks.HWProbe.get_disks",
                side_effect=lambda: list(current),
            ),
            patch(
                "zeropoint_agent.nodes.hw.disk.HWProbe.get_disks",
                side_effect=lambda: list(current),
            ),
            patch(
                "zeropoint_agent.nodes.hw._ops.device_for",
                side_effect=lambda stable_id: (
                    "/dev/testdisk1"
                    if current and stable_id.endswith("part1")
                    else "/dev/testdisk"
                    if current and stable_id == "disk-stable"
                    else None
                ),
            ),
            patch(
                "zeropoint_agent.nodes.hw._ops.fs_type",
                return_value="ext4",
            ),
            patch(
                "zeropoint_agent.nodes.hw._ops.fs_uuid",
                return_value="fs-uuid",
            ),
            patch(
                "zeropoint_agent.nodes.hw._ops.is_mounted",
                side_effect=lambda mountpoint: bool(current),
            ),
        ):
            self.dag.add("hardware", Namespace("hardware"))
            self.dag.add(
                "hardware/disks",
                Disks(),
                parents=["hardware"],
            )
            first = self.runner.start(ResolveMode.LIVE)
            self.assertEqual(
                self.wait_terminal(first.id)["status"],
                "succeeded",
            )
            mount_id = next(
                node_id for node_id in self.dag.nodes
                if "/mount_" in node_id
            )
            self.dag.add(
                "consumer",
                MountConsumer(),
                parents=[mount_id],
            )

            current.clear()
            second = self.runner.start(ResolveMode.LIVE)
            result = self.wait_terminal(second.id)

            self.assertEqual(result["status"], "failed")
            self.assertIn(mount_id, self.dag.nodes)
            self.assertEqual(
                self.dag.get("consumer").status,
                NodeStatus.BLOCKED,
            )


if __name__ == "__main__":
    unittest.main()
