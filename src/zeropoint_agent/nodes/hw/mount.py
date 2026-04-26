"""MountNode — mounts a formatted partition."""

import logging
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode, ResolveMode, NodeResult, SystemdUnit
from zeropoint_agent.nodes.hw.format import FormatResult

logger = logging.getLogger(__name__)


@dataclass
class MountResult:
    """Contract for a mount node."""
    mountpoint: str
    stable_id: str = ""
    device_path: str = ""
    options: str = "defaults"


class MountNode(INode[FormatResult, MountResult]):
    """Mounts a formatted partition at a mountpoint."""

    def __init__(self, mountpoint: str, options: str = "defaults"):
        self.mountpoint = mountpoint
        self.options = options

    def resolve(self, input: FormatResult, mode: ResolveMode) -> NodeResult[MountResult]:
        result = MountResult(
            mountpoint=self.mountpoint, stable_id=input.stable_id,
            device_path=input.device_path, options=self.options)

        if mode == ResolveMode.MOCK:
            return NodeResult.success(result)

        # TODO: mount command
        return NodeResult.pending_reboot(result)

    def verify(self, mode: ResolveMode) -> NodeResult[MountResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(MountResult(mountpoint=self.mountpoint))
        # TODO: check /proc/mounts
        return NodeResult.pending_reboot()

    def remove(self, mode: ResolveMode) -> NodeResult[MountResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success()
        # TODO: umount
        return NodeResult.success()
