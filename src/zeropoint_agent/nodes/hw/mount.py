"""Mount — verifies or mounts a formatted partition."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict

from zeropoint_agent.inode import INode, NodeResult, ResolveMode
from zeropoint_agent.jobs import JobContext, JobSpec
from zeropoint_agent.nodes.hw import _ops
from zeropoint_agent.nodes.hw.format import FormatResult


@dataclass
class MountResult:
    mountpoint: str
    stable_id: str = ""
    device_path: str = ""
    options: str = "defaults"


@dataclass
class Mount(INode[FormatResult, MountResult]):
    mountpoint: str
    options: str = "defaults"

    def resolve(
        self,
        input: FormatResult,
        mode: ResolveMode,
    ) -> NodeResult[MountResult]:
        result = MountResult(
            mountpoint=self.mountpoint,
            stable_id=input.stable_id,
            device_path=input.device_path,
            options=self.options,
        )
        if mode == ResolveMode.MOCK:
            return NodeResult.success(result)
        device = input.device_path or _ops.device_for(input.stable_id)
        if not device:
            return NodeResult.failed(
                f"formatted device not found: {input.stable_id}",
                result,
            )
        result.device_path = device
        if not _ops.is_mounted(self.mountpoint):
            operation = _ops.mount(device, self.mountpoint, self.options)
            if not operation.ok:
                return NodeResult.failed(
                    f"mount failed (exit {operation.returncode}): "
                    f"{operation.stderr}",
                    result,
                )
        uuid = input.uuid or _ops.fs_uuid(device)
        if uuid:
            _ops.ensure_fstab(
                uuid,
                self.mountpoint,
                input.filesystem,
                self.options,
            )
        return NodeResult.success(result)

    def resolve_promise(self, input, mode, context):
        if mode != ResolveMode.LIVE:
            return super().resolve_promise(input, mode, context)
        return context.jobs.submit(
            context,
            JobSpec(
                kind="node.mount",
                arguments={
                    "config": {
                        "mountpoint": self.mountpoint,
                        "options": self.options,
                    },
                    "input": asdict(input),
                },
                resources=[f"mount:{self.mountpoint}"],
            ),
        )

    def verify(self, mode: ResolveMode) -> NodeResult[MountResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult()
        if _ops.is_mounted(self.mountpoint):
            return NodeResult.success(MountResult(
                mountpoint=self.mountpoint,
                options=self.options,
            ))
        return NodeResult()

    def remove(self, mode: ResolveMode) -> NodeResult[MountResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success()
        if _ops.is_mounted(self.mountpoint):
            operation = _ops.umount(self.mountpoint)
            if not operation.ok:
                return NodeResult.failed(
                    f"umount failed (exit {operation.returncode}): "
                    f"{operation.stderr}"
                )
        _ops.remove_fstab(self.mountpoint)
        return NodeResult.success()

    def remove_promise(self, input, mode, context):
        if mode != ResolveMode.LIVE:
            return super().remove_promise(input, mode, context)
        return context.jobs.submit(
            context,
            JobSpec(
                kind="node.mount.remove",
                arguments={
                    "mountpoint": self.mountpoint,
                },
                resources=[f"mount:{self.mountpoint}"],
            ),
        )


def execute_mount_job(
    context: JobContext,
    arguments: Dict[str, object],
) -> NodeResult[MountResult]:
    context.progress(0.1, {"phase": "probe"})
    context.cancellation.raise_if_requested()
    node = Mount(**dict(arguments["config"]))
    result = node.resolve(
        FormatResult(**dict(arguments["input"])),
        ResolveMode.LIVE,
    )
    context.progress(1.0, {"phase": "complete"})
    return result


def execute_mount_remove_job(
    context: JobContext,
    arguments: Dict[str, object],
) -> NodeResult[MountResult]:
    mountpoint = str(arguments["mountpoint"])
    context.progress(0.2, {"phase": "unmount"})
    if _ops.is_mounted(mountpoint):
        operation = _ops.umount(mountpoint)
        if not operation.ok:
            return NodeResult.failed(
                f"umount failed (exit {operation.returncode}): "
                f"{operation.stderr}"
            )
    _ops.remove_fstab(mountpoint)
    context.progress(1.0, {"phase": "complete"})
    return NodeResult.success()
