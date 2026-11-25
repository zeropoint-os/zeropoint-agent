"""Partition — verifies or creates a partition on a parent disk."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from zeropoint_agent.hw_probe import HWProbe
from zeropoint_agent.inode import INode, NodeResult, ResolveMode, SystemdUnit
from zeropoint_agent.jobs import JobContext, JobSpec
from zeropoint_agent.nodes._systemd import AGENT_BIN
from zeropoint_agent.nodes.hw import _ops
from zeropoint_agent.nodes.hw._graph import field as output_field, own_output
from zeropoint_agent.nodes.hw.disk import DiskResult


@dataclass
class PartitionResult:
    number: int
    size_mb: int
    stable_id: str = ""
    device_path: str = ""
    type: str = "83"
    label: Optional[str] = None
    partuuid: Optional[str] = None
    is_root: bool = False
    is_boot: bool = False
    holders: List[str] = field(default_factory=list)


@dataclass
class Partition(INode[DiskResult, PartitionResult]):
    number: int
    size_mb: int
    type: str = "83"
    label: Optional[str] = None
    stable_id: Optional[str] = None

    def _stable_id(self) -> str:
        return (
            self.stable_id
            or str(output_field(own_output(self), "stable_id") or "")
        )

    def _probe(self):
        stable_id = self._stable_id()
        for disk in HWProbe.get_disks():
            for partition in disk.partitions:
                if partition.id == stable_id:
                    return partition
        return None

    def resolve(
        self,
        input: DiskResult,
        mode: ResolveMode,
    ) -> NodeResult[PartitionResult]:
        stable_id = self.stable_id or f"{input.stable_id}-part{self.number}"
        result = PartitionResult(
            number=self.number,
            size_mb=self.size_mb,
            stable_id=stable_id,
            type=self.type,
            label=self.label,
        )
        if mode == ResolveMode.MOCK:
            result.device_path = f"/dev/disk/by-id/{stable_id}"
            return NodeResult.success(result)

        existing = _ops.device_for(stable_id)
        if existing:
            result.device_path = existing
            probed = self._probe()
            if probed is not None:
                result.partuuid = probed.partuuid
                result.is_root = probed.is_root
                result.is_boot = probed.is_boot
                result.holders = list(probed.holders)
            return NodeResult.success(result)
        disk_device = input.device_path or _ops.device_for(input.stable_id)
        if not disk_device:
            return NodeResult.failed(
                f"parent disk not found: {input.stable_id}",
                result,
            )
        operation = _ops.create_partition(
            disk_device,
            self.size_mb,
            self.type,
            self.label,
        )
        if not operation.ok:
            return NodeResult.failed(
                f"sfdisk failed (exit {operation.returncode}): "
                f"{operation.stderr}",
                result,
            )
        _ops.settle()
        device = _ops.device_for(stable_id)
        if device:
            result.device_path = device
            return NodeResult.success(result)
        return NodeResult.pending_reboot(result).add_unit(SystemdUnit(
            name="zeropoint-verify-" + (self.id or stable_id).replace("/", "-"),
            description=f"Verify partition {self.number}",
            exec_start=f"{AGENT_BIN} verify-node {self.id or stable_id}",
        ))

    def resolve_promise(self, input, mode, context):
        if mode != ResolveMode.LIVE:
            return super().resolve_promise(input, mode, context)
        return context.jobs.submit(
            context,
            JobSpec(
                kind="node.partition",
                arguments={
                    "config": {
                        "number": self.number,
                        "size_mb": self.size_mb,
                        "type": self.type,
                        "label": self.label,
                        "stable_id": self.stable_id,
                    },
                    "input": asdict(input),
                },
                resources=[f"disk:{input.stable_id}"],
            ),
        )

    def verify(self, mode: ResolveMode) -> NodeResult[PartitionResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult()
        stable_id = self._stable_id()
        device = _ops.device_for(stable_id)
        if device:
            probed = self._probe()
            return NodeResult.success(PartitionResult(
                number=self.number,
                size_mb=self.size_mb,
                stable_id=stable_id,
                device_path=device,
                type=self.type,
                label=self.label,
                partuuid=probed.partuuid if probed else None,
                is_root=probed.is_root if probed else False,
                is_boot=probed.is_boot if probed else False,
                holders=list(probed.holders) if probed else [],
            ))
        return NodeResult()

    def remove(self, mode: ResolveMode) -> NodeResult[PartitionResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success()
        device = _ops.device_for(self._stable_id())
        if not device:
            return NodeResult.success()
        return NodeResult.pending_reboot().add_unit(SystemdUnit(
            name="zeropoint-wipe-" + (self.id or self._stable_id()).replace("/", "-"),
            description=f"Wipe partition {self.number}",
            exec_start=f"wipefs -a {device}",
        ))

    def remove_promise(self, input, mode, context):
        if mode != ResolveMode.LIVE:
            return super().remove_promise(input, mode, context)
        return context.jobs.submit(
            context,
            JobSpec(
                kind="node.partition.remove",
                arguments={
                    "config": {
                        "number": self.number,
                        "size_mb": self.size_mb,
                        "type": self.type,
                        "label": self.label,
                        "stable_id": self.stable_id,
                    },
                    "input": asdict(input),
                },
                resources=[f"disk:{input.stable_id}"],
            ),
        )


def execute_partition_job(
    context: JobContext,
    arguments: Dict[str, object],
) -> NodeResult[PartitionResult]:
    context.progress(0.1, {"phase": "probe"})
    context.cancellation.raise_if_requested()
    node = Partition(**dict(arguments["config"]))
    result = node.resolve(
        DiskResult(**dict(arguments["input"])),
        ResolveMode.LIVE,
    )
    context.progress(1.0, {"phase": "complete"})
    return result


def execute_partition_remove_job(
    context: JobContext,
    arguments: Dict[str, object],
) -> NodeResult[PartitionResult]:
    node = Partition(**dict(arguments["config"]))
    parent = DiskResult(**dict(arguments["input"]))
    stable_id = node.stable_id or f"{parent.stable_id}-part{node.number}"
    if not _ops.device_for(stable_id):
        return NodeResult.success()
    disk_device = parent.device_path or _ops.device_for(parent.stable_id)
    if not disk_device:
        return NodeResult.failed(f"parent disk not found: {parent.stable_id}")
    context.progress(0.2, {"phase": "delete_partition"})
    operation = _ops.delete_partition(disk_device, node.number)
    if not operation.ok:
        return NodeResult.failed(
            f"sfdisk delete failed (exit {operation.returncode}): "
            f"{operation.stderr}"
        )
    _ops.settle()
    context.progress(1.0, {"phase": "complete"})
    if _ops.device_for(stable_id):
        return NodeResult.pending_reboot()
    return NodeResult.success()
