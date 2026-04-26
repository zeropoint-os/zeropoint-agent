"""DiskNode — discovers/registers a disk by stable ID."""

import logging
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode
from zeropoint_agent.nodes._systemd import systemd_unit, AGENT_BIN

logger = logging.getLogger(__name__)


@dataclass
class DiskResult:
    """Contract for a disk node. Keyed by stable ID from /dev/disk/by-id/."""
    stable_id: str
    device_path: str = ""
    size: int = 0
    free: int = 0
    sector_size: int = 512


class DiskNode(INode[None, DiskResult]):
    """Discovers/registers a disk by stable ID. Root node."""

    def __init__(self, stable_id: str):
        self.stable_id = stable_id

    def resolve(self, input: None) -> DiskResult:
        logger.info(f"DiskNode.resolve() — discovering {self.stable_id}")
        return DiskResult(stable_id=self.stable_id)

    def mock_resolve(self, input: None) -> DiskResult:
        return DiskResult(stable_id=self.stable_id,
                          device_path=f"/dev/disk/by-id/{self.stable_id}",
                          size=1000000000, free=500000000)

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        return systemd_unit(
            node_id, f"Discover disk {self.stable_id}",
            parent_ids,
            exec_start=f"/usr/bin/test -e /dev/disk/by-id/{self.stable_id}",
            exec_verify=f"/usr/bin/test -e /dev/disk/by-id/{self.stable_id}",
        )
