"""PartitionNode — creates a partition on a parent disk."""

import logging
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode
from zeropoint_agent.nodes._systemd import systemd_unit, AGENT_BIN
from zeropoint_agent.nodes.hw.disk import DiskResult

logger = logging.getLogger(__name__)


@dataclass
class PartitionResult:
    """Contract for a partition node. Keyed by stable ID (disk-stable-id-partN)."""
    number: int
    size_mb: int
    stable_id: str = ""
    device_path: str = ""
    type: str = "83"
    label: Optional[str] = None


class PartitionNode(INode[DiskResult, PartitionResult]):
    """Creates a partition on a parent disk. Stable ID derived from parent."""

    def __init__(self, number: int, size_mb: int, type: str = "83"):
        self.number = number
        self.size_mb = size_mb
        self.type = type

    def resolve(self, input: DiskResult) -> PartitionResult:
        stable_id = f"{input.stable_id}-part{self.number}"
        logger.info(f"PartitionNode.resolve() — partition {self.number} on {input.stable_id}")
        return PartitionResult(
            number=self.number, size_mb=self.size_mb,
            stable_id=stable_id,
            device_path=f"{input.device_path}{self.number}" if input.device_path else "",
            type=self.type,
        )

    def mock_resolve(self, input: DiskResult) -> PartitionResult:
        stable_id = f"{input.stable_id}-part{self.number}"
        return PartitionResult(
            number=self.number, size_mb=self.size_mb,
            stable_id=stable_id,
            device_path=f"/dev/disk/by-id/{stable_id}",
            type=self.type,
        )

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        return systemd_unit(
            node_id, f"Create partition {self.number}",
            parent_ids,
            exec_start=f"{AGENT_BIN} resolve-node {node_id}",
            exec_verify=f"{AGENT_BIN} verify-node {node_id}",
        )
