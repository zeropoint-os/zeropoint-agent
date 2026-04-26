"""FormatNode — creates a filesystem on a parent partition."""

import logging
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode
from zeropoint_agent.nodes._systemd import systemd_unit, AGENT_BIN
from zeropoint_agent.nodes.hw.partition import PartitionResult

logger = logging.getLogger(__name__)


@dataclass
class FormatResult:
    """Contract for a filesystem format node. Inherits partition stable ID."""
    filesystem: str = "ext4"
    stable_id: str = ""
    device_path: str = ""
    label: Optional[str] = None
    uuid: Optional[str] = None


class FormatNode(INode[PartitionResult, FormatResult]):
    """Creates a filesystem on a parent partition."""

    def __init__(self, filesystem: str = "ext4", label: Optional[str] = None):
        self.filesystem = filesystem
        self.label = label

    def resolve(self, input: PartitionResult) -> FormatResult:
        logger.info(f"FormatNode.resolve() — mkfs.{self.filesystem} on {input.stable_id}")
        return FormatResult(filesystem=self.filesystem,
                            stable_id=input.stable_id,
                            device_path=input.device_path,
                            label=self.label)

    def mock_resolve(self, input: PartitionResult) -> FormatResult:
        return FormatResult(filesystem=self.filesystem,
                            stable_id=input.stable_id,
                            device_path=input.device_path,
                            label=self.label, uuid="mock-uuid-1234")

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        return systemd_unit(
            node_id, f"Format filesystem ({self.filesystem})",
            parent_ids,
            exec_start=f"{AGENT_BIN} resolve-node {node_id}",
            exec_verify=f"{AGENT_BIN} verify-node {node_id}",
        )
