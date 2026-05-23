"""Partition — creates a partition on a parent disk."""

import logging
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode, ResolveMode, NodeResult, SystemdUnit
from zeropoint_agent.nodes.hw.disk import DiskResult
from zeropoint_agent.nodes._systemd import AGENT_BIN

logger = logging.getLogger(__name__)


@dataclass
class PartitionResult:
    """Contract for a partition node. Keyed by stable ID."""
    number: int
    size_mb: int
    stable_id: str = ""
    device_path: str = ""
    type: str = "83"
    label: Optional[str] = None


class Partition(INode[DiskResult, PartitionResult]):
    """Creates a partition on a parent disk."""

    def __init__(self, number: int, size_mb: int, type: str = "83"):
        self.number = number
        self.size_mb = size_mb
        self.type = type

    def resolve(self, input: DiskResult, mode: ResolveMode) -> NodeResult[PartitionResult]:
        stable_id = f"{input.stable_id}-part{self.number}"
        result = PartitionResult(
            number=self.number, size_mb=self.size_mb,
            stable_id=stable_id, type=self.type)

        if mode == ResolveMode.MOCK:
            result.device_path = f"/dev/disk/by-id/{stable_id}"
            return NodeResult.success(result)

        # TODO: sfdisk to create partition
        result.device_path = f"{input.device_path}{self.number}" if input.device_path else ""
        return (NodeResult
            .pending_reboot(result)
            .add_unit(SystemdUnit(
                name=f"zeropoint-part-{self.number}",
                description=f"Verify partition {self.number}",
                exec_start=f"{AGENT_BIN} verify-node part-{self.number}",
            )))

    def verify(self, mode: ResolveMode) -> NodeResult[PartitionResult]:
        result = PartitionResult(number=self.number, size_mb=self.size_mb)
        if mode == ResolveMode.MOCK:
            return NodeResult.success(result)
        # TODO: check /dev/disk/by-id/{stable_id} exists
        return NodeResult.pending_reboot(result)

    def remove(self, mode: ResolveMode) -> NodeResult[PartitionResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success()
        return (NodeResult
            .pending_reboot()
            .add_unit(SystemdUnit(
                name=f"zeropoint-wipe-part-{self.number}",
                description=f"Wipe partition {self.number}",
                exec_start=f"wipefs -a /dev/disk/by-id/TODO",
            )))
