"""Partition — creates a partition on a parent disk."""

import logging
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode, ResolveMode, NodeResult, SystemdUnit
from zeropoint_agent.nodes.hw.disk import DiskResult
from zeropoint_agent.nodes.hw import _ops
from zeropoint_agent.nodes.hw._graph import own_output, field
from zeropoint_agent.nodes._systemd import AGENT_BIN

logger = logging.getLogger(__name__)


@dataclass
class PartitionResult:
    """Contract for a partition node. Keyed by stable ID."""
    number: int
    size_mb: int
    stable_id: str = ""
    device_path: str = ""
    type: str = "83"
    label: Optional[str] = None


@dataclass
class Partition(INode[DiskResult, PartitionResult]):
    """Creates a partition on a parent disk."""

    number: int
    size_mb: int
    type: str = "83"
    label: Optional[str] = None

    # --- helpers ---------------------------------------------------------

    def _unit_name(self, verb: str = "part") -> str:
        base = self.id or f"part-{self.number}"
        return f"zeropoint-{verb}-" + base.replace("/", "-")

    def _reentry_unit(self) -> SystemdUnit:
        """Unit that re-resolves this node after a reboot so the graph
        converges once the kernel has picked up the new partition."""
        target = self.id or f"part-{self.number}"
        return SystemdUnit(
            name=self._unit_name("verify"),
            description=f"Verify partition {self.number}",
            exec_start=f"{AGENT_BIN} verify-node {target}",
        )

    def _stable_id(self) -> str:
        """This partition's stable id, from our own last-persisted output.

        Empty until the first successful resolve records it; callers treat
        empty as "not yet converged" and let resolve (which has `input`)
        compute it fresh.
        """
        return field(own_output(self), "stable_id") or ""

    # --- lifecycle -------------------------------------------------------

    def resolve(self, input: DiskResult, mode: ResolveMode) -> NodeResult[PartitionResult]:
        stable_id = f"{input.stable_id}-part{self.number}"
        result = PartitionResult(
            number=self.number, size_mb=self.size_mb,
            stable_id=stable_id, type=self.type, label=self.label)

        if mode == ResolveMode.MOCK:
            result.device_path = f"/dev/disk/by-id/{stable_id}"
            return NodeResult.success(result)

        # Idempotent: if the partition already exists, don't repartition.
        existing = _ops.device_for(stable_id)
        if existing:
            result.device_path = existing
            return NodeResult.success(result)

        disk_device = input.device_path or _ops.device_for(input.stable_id)
        if not disk_device:
            return NodeResult.failed(
                f"parent disk not found: {input.stable_id}", result)

        proc = _ops.create_partition(disk_device, self.size_mb, self.type, self.label)
        if not proc.ok:
            return NodeResult.failed(
                f"sfdisk failed (exit {proc.returncode}): {proc.stderr}", result)

        _ops.settle()
        dev = _ops.device_for(stable_id)
        if dev:
            result.device_path = dev
            return NodeResult.success(result)

        # Table written but the device node hasn't appeared — finish on reboot.
        return NodeResult.pending_reboot(result).add_unit(self._reentry_unit())

    def verify(self, mode: ResolveMode) -> NodeResult[PartitionResult]:
        if mode == ResolveMode.MOCK:
            # No real state to probe in mock; report not-converged so the
            # runtime calls resolve(), which has `input` and can simulate
            # the full PartitionResult (stable_id needs the parent disk).
            return NodeResult()

        stable_id = self._stable_id()
        dev = _ops.device_for(stable_id)
        if dev:
            return NodeResult.success(PartitionResult(
                number=self.number, size_mb=self.size_mb,
                stable_id=stable_id, device_path=dev, type=self.type,
                label=self.label))
        return NodeResult.pending_reboot(
            PartitionResult(number=self.number, size_mb=self.size_mb,
                            stable_id=stable_id, type=self.type))

    def remove(self, mode: ResolveMode) -> NodeResult[PartitionResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success()

        dev = _ops.device_for(self._stable_id())
        if not dev:
            return NodeResult.success()  # already gone

        # Wiping a partition that may be in use is a boot-time operation.
        return NodeResult.pending_reboot().add_unit(SystemdUnit(
            name=self._unit_name("wipe"),
            description=f"Wipe partition {self.number}",
            exec_start=f"wipefs -a {dev}",
        ))
