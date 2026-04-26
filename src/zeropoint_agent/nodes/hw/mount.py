"""MountNode — mounts a formatted partition at a mountpoint."""

import logging
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode
from zeropoint_agent.nodes._systemd import systemd_unit, AGENT_BIN
from zeropoint_agent.nodes.hw.format import FormatResult

logger = logging.getLogger(__name__)


@dataclass
class MountResult:
    """Contract for a mount node. Uses stable ID to mount."""
    mountpoint: str
    stable_id: str = ""
    device_path: str = ""
    options: str = "defaults"


class MountNode(INode[FormatResult, MountResult]):
    """Mounts a formatted partition at a mountpoint."""

    def __init__(self, mountpoint: str, options: str = "defaults"):
        self.mountpoint = mountpoint
        self.options = options

    def resolve(self, input: FormatResult) -> MountResult:
        logger.info(f"MountNode.resolve() — mounting {input.stable_id} at {self.mountpoint}")
        return MountResult(mountpoint=self.mountpoint,
                           stable_id=input.stable_id,
                           device_path=input.device_path,
                           options=self.options)

    def mock_resolve(self, input: FormatResult) -> MountResult:
        return MountResult(mountpoint=self.mountpoint,
                           stable_id=input.stable_id,
                           device_path=input.device_path,
                           options=self.options)

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        return systemd_unit(
            node_id, f"Mount {self.mountpoint}",
            parent_ids,
            exec_start=f"/bin/mount {self.mountpoint}",
            exec_verify=f"/bin/mountpoint -q {self.mountpoint}",
        )
