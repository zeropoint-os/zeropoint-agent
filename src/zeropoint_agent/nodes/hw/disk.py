"""Disk — discovers/registers a disk by stable ID."""

import logging
from dataclasses import dataclass
from dataclasses import field
from typing import List, Optional

from zeropoint_agent.hw_probe import HWProbe
from zeropoint_agent.inode import INode, ResolveMode, NodeResult

logger = logging.getLogger(__name__)


@dataclass
class DiskResult:
    """Contract for a disk node. Keyed by stable ID from /dev/disk/by-id/."""
    stable_id: str
    device_path: str = ""
    size: int = 0
    free: int = 0
    sector_size: int = 512
    removable: bool = False
    read_only: bool = False
    is_root: bool = False
    is_boot: bool = False
    holders: List[str] = field(default_factory=list)


@dataclass
class Disk(INode[None, DiskResult]):
    """Discovers/registers a disk by stable ID. Root node."""

    stable_id: str

    def _probe(self) -> Optional[DiskResult]:
        disk = HWProbe.get_disk(self.stable_id)
        if disk is None:
            return None
        return DiskResult(
            stable_id=disk.id,
            device_path=disk.device,
            size=disk.size,
            free=disk.free or 0,
            sector_size=disk.sector_size,
            removable=disk.removable,
            read_only=disk.read_only,
            is_root=disk.is_root,
            is_boot=disk.is_boot,
            holders=list(disk.holders),
        )

    def resolve(self, input: None, mode: ResolveMode) -> NodeResult[DiskResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(DiskResult(
                stable_id=self.stable_id,
                device_path=f"/dev/disk/by-id/{self.stable_id}",
                size=1000000000, free=500000000))

        result = self._probe()
        if result:
            return NodeResult.success(result)
        return NodeResult.failed(f"Disk not found: {self.stable_id}")

    def verify(self, mode: ResolveMode) -> NodeResult[DiskResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(DiskResult(
                stable_id=self.stable_id,
                device_path=f"/dev/disk/by-id/{self.stable_id}"))

        result = self._probe()
        if result:
            return NodeResult.success(result)
        return NodeResult()

    def remove(self, mode: ResolveMode) -> NodeResult[DiskResult]:
        return NodeResult.success()
