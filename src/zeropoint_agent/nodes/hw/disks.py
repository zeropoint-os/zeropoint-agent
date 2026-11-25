"""Disks — live root node that produces the current block-device graph."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict

from zeropoint_agent.hw_probe import HWProbe
from zeropoint_agent.inode import INode, NodeResult, ResolveMode
from zeropoint_agent.nodes.hw.disk import Disk
from zeropoint_agent.nodes.hw.format import Format
from zeropoint_agent.nodes.hw.mount import Mount
from zeropoint_agent.nodes.hw.partition import Partition


def _node_segment(value: str) -> str:
    readable = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    digest = hashlib.sha256(value.encode()).hexdigest()[:10]
    return f"{readable[:64]}_{digest}" if readable else digest


@dataclass
class DisksResult:
    disks: Dict[str, str] = field(default_factory=dict)


@dataclass
class Disks(INode[None, DisksResult]):
    """Enumerates all disks and produces their current storage subtrees."""

    def resolve(self, input, mode: ResolveMode) -> NodeResult[DisksResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(DisksResult())

        result = NodeResult.success(DisksResult())
        for disk in HWProbe.get_disks():
            disk_id = f"{self.id}/{_node_segment(disk.id)}"
            result.output.disks[disk.id] = disk_id
            result.produce(
                disk_id,
                Disk(stable_id=disk.id),
                parents=[self.id],
                perms="r--",
                tags=["hardware", "disk"],
            )
            for partition in disk.partitions:
                partition_id = (
                    f"{disk_id}/partition_"
                    f"{partition.number or _node_segment(partition.id)}"
                )
                result.produce(
                    partition_id,
                    Partition(
                        number=partition.number,
                        size_mb=max(1, partition.size // (1024 * 1024)),
                        type=partition.parttype or "83",
                        label=partition.label,
                        stable_id=partition.id,
                    ),
                    parents=[disk_id],
                    perms="rwd",
                    tags=["hardware", "partition"],
                )
                if not partition.filesystem:
                    continue
                format_id = f"{partition_id}/format"
                result.produce(
                    format_id,
                    Format(
                        filesystem=partition.filesystem,
                        label=partition.label,
                    ),
                    parents=[partition_id],
                    perms="rwd",
                    tags=["hardware", "filesystem"],
                )
                for mountpoint in partition.mountpoints:
                    result.produce(
                        f"{format_id}/mount_{_node_segment(mountpoint)}",
                        Mount(
                            mountpoint=mountpoint,
                            options=partition.mount_options.get(
                                mountpoint,
                                "defaults",
                            ),
                        ),
                        parents=[format_id],
                        perms="rwd",
                        tags=["hardware", "mount"],
                    )
        return result

    def verify(self, mode: ResolveMode) -> NodeResult[DisksResult]:
        return NodeResult()

    def remove(self, mode: ResolveMode) -> NodeResult[DisksResult]:
        return NodeResult.success()
