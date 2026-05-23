"""Disk — discovers/registers a disk by stable ID."""

import logging
import os
from dataclasses import dataclass
from typing import Optional

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


class Disk(INode[None, DiskResult]):
    """Discovers/registers a disk by stable ID. Root node."""

    def __init__(self, stable_id: str):
        self.stable_id = stable_id

    def _probe(self) -> Optional[DiskResult]:
        by_id = f"/dev/disk/by-id/{self.stable_id}"
        if os.path.exists(by_id):
            device_path = os.path.realpath(by_id)
            return DiskResult(stable_id=self.stable_id, device_path=device_path)
        return None

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
        return NodeResult.pending_reboot()

    def remove(self, mode: ResolveMode) -> NodeResult[DiskResult]:
        return NodeResult.success()
