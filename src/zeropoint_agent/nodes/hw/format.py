"""Format — creates a filesystem on a parent partition."""

import logging
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode, ResolveMode, NodeResult, SystemdUnit
from zeropoint_agent.nodes.hw.partition import PartitionResult
from zeropoint_agent.nodes.hw import _ops
from zeropoint_agent.nodes.hw._graph import own_output, field

logger = logging.getLogger(__name__)


@dataclass
class FormatResult:
    """Contract for a filesystem format node."""
    filesystem: str = "ext4"
    stable_id: str = ""
    device_path: str = ""
    label: Optional[str] = None
    uuid: Optional[str] = None


@dataclass
class Format(INode[PartitionResult, FormatResult]):
    """Creates a filesystem on a parent partition."""

    filesystem: str = "ext4"
    label: Optional[str] = None

    def _device(self) -> str:
        """The device to operate on, from our own last-persisted output."""
        out = own_output(self)
        dev = field(out, "device_path")
        if dev:
            return dev
        stable = field(out, "stable_id")
        return (_ops.device_for(stable) or "") if stable else ""

    def resolve(self, input: PartitionResult, mode: ResolveMode) -> NodeResult[FormatResult]:
        result = FormatResult(
            filesystem=self.filesystem, stable_id=input.stable_id,
            device_path=input.device_path, label=self.label)

        if mode == ResolveMode.MOCK:
            result.uuid = "mock-uuid-1234"
            return NodeResult.success(result)

        device = input.device_path or _ops.device_for(input.stable_id)
        if not device:
            return NodeResult.failed(
                f"partition not found: {input.stable_id}", result)
        result.device_path = device

        existing = _ops.fs_type(device)
        if existing == self.filesystem:
            # Idempotent: already the desired filesystem — just record uuid.
            result.uuid = _ops.fs_uuid(device)
            return NodeResult.success(result)
        if existing:
            # A different filesystem is present. Refuse to reformat rather
            # than silently destroy data; require an explicit remove first.
            return NodeResult.failed(
                f"refusing to reformat: {device} already holds {existing!r} "
                f"(remove this node to wipe it first)", result)

        proc = _ops.make_filesystem(device, self.filesystem, self.label)
        if not proc.ok:
            return NodeResult.failed(
                f"mkfs.{self.filesystem} failed (exit {proc.returncode}): "
                f"{proc.stderr}", result)

        result.uuid = _ops.fs_uuid(device)
        return NodeResult.success(result)

    def verify(self, mode: ResolveMode) -> NodeResult[FormatResult]:
        if mode == ResolveMode.MOCK:
            # Defer to resolve() in mock — verify has no `input`, so it
            # can't fill device_path/uuid without probing real state.
            return NodeResult()

        device = self._device()
        if device and _ops.fs_type(device) == self.filesystem:
            return NodeResult.success(FormatResult(
                filesystem=self.filesystem, device_path=device,
                label=self.label, uuid=_ops.fs_uuid(device)))
        return NodeResult.pending_reboot(
            FormatResult(filesystem=self.filesystem, device_path=device))

    def remove(self, mode: ResolveMode) -> NodeResult[FormatResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success()

        device = self._device()
        if not device or not _ops.fs_type(device):
            return NodeResult.success()  # nothing to wipe

        # A live filesystem may be mounted; wipe on next boot.
        return NodeResult.pending_reboot().add_unit(SystemdUnit(
            name="zeropoint-wipe-fs-" + (self.id or self.filesystem).replace("/", "-"),
            description=f"Wipe filesystem on {device}",
            exec_start=f"wipefs -a {device}",
        ))
