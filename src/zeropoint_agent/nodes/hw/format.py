"""Format — creates a filesystem on a parent partition."""

import logging
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode, ResolveMode, NodeResult, SystemdUnit
from zeropoint_agent.nodes.hw.partition import PartitionResult
from zeropoint_agent.nodes._systemd import AGENT_BIN

logger = logging.getLogger(__name__)


@dataclass
class FormatResult:
    """Contract for a filesystem format node."""
    filesystem: str = "ext4"
    stable_id: str = ""
    device_path: str = ""
    label: Optional[str] = None
    uuid: Optional[str] = None


class Format(INode[PartitionResult, FormatResult]):
    """Creates a filesystem on a parent partition."""

    def __init__(self, filesystem: str = "ext4", label: Optional[str] = None):
        self.filesystem = filesystem
        self.label = label

    def resolve(self, input: PartitionResult, mode: ResolveMode) -> NodeResult[FormatResult]:
        result = FormatResult(
            filesystem=self.filesystem, stable_id=input.stable_id,
            device_path=input.device_path, label=self.label)

        if mode == ResolveMode.MOCK:
            result.uuid = "mock-uuid-1234"
            return NodeResult.success(result)

        # TODO: mkfs
        return NodeResult.pending_reboot(result)

    def verify(self, mode: ResolveMode) -> NodeResult[FormatResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(FormatResult(filesystem=self.filesystem))
        # TODO: blkid to check
        return NodeResult.pending_reboot()

    def remove(self, mode: ResolveMode) -> NodeResult[FormatResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success()
        return NodeResult.pending_reboot()
