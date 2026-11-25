"""Format — verifies or creates a filesystem on a parent partition."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Optional

from zeropoint_agent.inode import INode, NodeResult, ResolveMode, SystemdUnit
from zeropoint_agent.jobs import JobContext, JobSpec
from zeropoint_agent.nodes.hw import _ops
from zeropoint_agent.nodes.hw._graph import field, own_output
from zeropoint_agent.nodes.hw.partition import PartitionResult


@dataclass
class FormatResult:
    filesystem: str = "ext4"
    stable_id: str = ""
    device_path: str = ""
    label: Optional[str] = None
    uuid: Optional[str] = None


@dataclass
class Format(INode[PartitionResult, FormatResult]):
    filesystem: str = "ext4"
    label: Optional[str] = None

    def _device(self) -> str:
        output = own_output(self)
        device = field(output, "device_path")
        if device:
            return str(device)
        stable_id = field(output, "stable_id")
        return _ops.device_for(str(stable_id)) or "" if stable_id else ""

    def resolve(
        self,
        input: PartitionResult,
        mode: ResolveMode,
    ) -> NodeResult[FormatResult]:
        result = FormatResult(
            filesystem=self.filesystem,
            stable_id=input.stable_id,
            device_path=input.device_path,
            label=self.label,
        )
        if mode == ResolveMode.MOCK:
            result.uuid = "mock-uuid-1234"
            return NodeResult.success(result)
        device = input.device_path or _ops.device_for(input.stable_id)
        if not device:
            return NodeResult.failed(
                f"partition not found: {input.stable_id}",
                result,
            )
        result.device_path = device
        existing = _ops.fs_type(device)
        if existing == self.filesystem:
            result.uuid = _ops.fs_uuid(device)
            return NodeResult.success(result)
        if existing:
            return NodeResult.failed(
                f"refusing to reformat: {device} already holds {existing!r}",
                result,
            )
        operation = _ops.make_filesystem(device, self.filesystem, self.label)
        if not operation.ok:
            return NodeResult.failed(
                f"mkfs.{self.filesystem} failed "
                f"(exit {operation.returncode}): {operation.stderr}",
                result,
            )
        result.uuid = _ops.fs_uuid(device)
        return NodeResult.success(result)

    def resolve_promise(self, input, mode, context):
        if mode != ResolveMode.LIVE:
            return super().resolve_promise(input, mode, context)
        return context.jobs.submit(
            context,
            JobSpec(
                kind="node.format",
                arguments={
                    "config": {
                        "filesystem": self.filesystem,
                        "label": self.label,
                    },
                    "input": asdict(input),
                },
                resources=[f"block:{input.stable_id}"],
            ),
        )

    def verify(self, mode: ResolveMode) -> NodeResult[FormatResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult()
        device = self._device()
        if device and _ops.fs_type(device) == self.filesystem:
            return NodeResult.success(FormatResult(
                filesystem=self.filesystem,
                device_path=device,
                label=self.label,
                uuid=_ops.fs_uuid(device),
                stable_id=str(field(own_output(self), "stable_id") or ""),
            ))
        return NodeResult()

    def remove(self, mode: ResolveMode) -> NodeResult[FormatResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success()
        device = self._device()
        if not device or not _ops.fs_type(device):
            return NodeResult.success()
        return NodeResult.pending_reboot().add_unit(SystemdUnit(
            name="zeropoint-wipe-fs-" + (self.id or self.filesystem).replace("/", "-"),
            description=f"Wipe filesystem on {device}",
            exec_start=f"wipefs -a {device}",
        ))

    def remove_promise(self, input, mode, context):
        if mode != ResolveMode.LIVE:
            return super().remove_promise(input, mode, context)
        device = self._device() or input.device_path
        return context.jobs.submit(
            context,
            JobSpec(
                kind="node.format.remove",
                arguments={
                    "device": device,
                    "stable_id": input.stable_id,
                },
                resources=[f"block:{input.stable_id or device}"],
            ),
        )


def execute_format_job(
    context: JobContext,
    arguments: Dict[str, object],
) -> NodeResult[FormatResult]:
    context.progress(0.1, {"phase": "probe"})
    context.cancellation.raise_if_requested()
    node = Format(**dict(arguments["config"]))
    result = node.resolve(
        PartitionResult(**dict(arguments["input"])),
        ResolveMode.LIVE,
    )
    context.progress(1.0, {"phase": "complete"})
    return result


def execute_format_remove_job(
    context: JobContext,
    arguments: Dict[str, object],
) -> NodeResult[FormatResult]:
    device = str(arguments.get("device") or "")
    stable_id = str(arguments.get("stable_id") or "")
    device = device or _ops.device_for(stable_id) or ""
    if not device or not _ops.fs_type(device):
        return NodeResult.success()
    context.progress(0.2, {"phase": "wipe"})
    operation = _ops.wipe(device)
    if not operation.ok:
        return NodeResult.failed(
            f"wipefs failed (exit {operation.returncode}): {operation.stderr}"
        )
    context.progress(1.0, {"phase": "complete"})
    return NodeResult.success()
