"""Mount — mounts a formatted partition."""

import logging
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode, ResolveMode, NodeResult
from zeropoint_agent.nodes.hw.format import FormatResult
from zeropoint_agent.nodes.hw import _ops

logger = logging.getLogger(__name__)


@dataclass
class MountResult:
    """Contract for a mount node."""
    mountpoint: str
    stable_id: str = ""
    device_path: str = ""
    options: str = "defaults"


@dataclass
class Mount(INode[FormatResult, MountResult]):
    """Mounts a formatted partition at a mountpoint."""

    mountpoint: str
    options: str = "defaults"

    def resolve(self, input: FormatResult, mode: ResolveMode) -> NodeResult[MountResult]:
        result = MountResult(
            mountpoint=self.mountpoint, stable_id=input.stable_id,
            device_path=input.device_path, options=self.options)

        if mode == ResolveMode.MOCK:
            return NodeResult.success(result)

        device = input.device_path or _ops.device_for(input.stable_id)
        if not device:
            return NodeResult.failed(
                f"formatted device not found: {input.stable_id}", result)
        result.device_path = device

        uuid = input.uuid or _ops.fs_uuid(device)

        if not _ops.is_mounted(self.mountpoint):
            proc = _ops.mount(device, self.mountpoint, self.options)
            if not proc.ok:
                return NodeResult.failed(
                    f"mount failed (exit {proc.returncode}): {proc.stderr}",
                    result)

        # Persist across reboots via fstab (keyed by UUID). Idempotent.
        if uuid:
            _ops.ensure_fstab(uuid, self.mountpoint, input.filesystem, self.options)
        return NodeResult.success(result)

    def verify(self, mode: ResolveMode) -> NodeResult[MountResult]:
        if mode == ResolveMode.MOCK:
            # Defer to resolve() in mock so it can populate device/stable_id.
            return NodeResult()

        if _ops.is_mounted(self.mountpoint):
            return NodeResult.success(MountResult(
                mountpoint=self.mountpoint, options=self.options))
        return NodeResult.pending_reboot(MountResult(mountpoint=self.mountpoint))

    def remove(self, mode: ResolveMode) -> NodeResult[MountResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success()

        if _ops.is_mounted(self.mountpoint):
            _ops.umount(self.mountpoint)
        _ops.remove_fstab(self.mountpoint)
        return NodeResult.success()
