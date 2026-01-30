"""Disk commands: Add, Edit, Release.

Disk operations:
- AddDisk: Partition and format a new disk (may require reboot)
- EditDisk: Modify disk settings
- ReleaseDisk: Wipe and release a disk (may require reboot)
"""

from typing import Dict, Any
from . import Command, CommandResult, CommandStatus


class DiskCommand(Command):
    """Base class for disk operations."""

    def __init__(self, name: str):
        super().__init__(f"disk:{name}")


class AddDisk(DiskCommand):
    """Add a new disk: partition, format, prepare."""

    def __init__(self):
        super().__init__("add")

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Add a disk.
        
        Expects resource with:
        - id: disk ID
        - device: /dev/sdb, /dev/nvme0n1, etc.
        - partition: partition number (optional)
        - filesystem: ext4, xfs, etc.
        """
        disk_id = resource.get("id")
        device = resource.get("device")
        partition = resource.get("partition", "1")
        filesystem = resource.get("filesystem", "ext4")

        if not device:
            return CommandResult(
                status=CommandStatus.FAILED,
                error="device is required"
            )

        try:
            # Check if device exists
            self._run_bash(f"lsblk -d {device} > /dev/null 2>&1", check=True)

            # TODO: Actually partition and format
            # This would be:
            # - sfdisk or fdisk to create partition
            # - mkfs.{filesystem} to format
            # - For now, just return success

            return CommandResult(
                status=CommandStatus.APPLIED,
                output={"device": device, "partition": partition, "filesystem": filesystem}
            )

        except Exception as e:
            return CommandResult(
                status=CommandStatus.FAILED,
                error=str(e)
            )


class EditDisk(DiskCommand):
    """Edit disk settings (minimal for now)."""

    def __init__(self):
        super().__init__("edit")

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Edit a disk (mostly a no-op unless filesystem changes)."""
        # TODO: Implement disk edit
        return CommandResult(status=CommandStatus.APPLIED)


class ReleaseDisk(DiskCommand):
    """Release and wipe a disk."""

    def __init__(self):
        super().__init__("release")

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Release a disk (wipe it)."""
        device = resource.get("device")

        if not device:
            return CommandResult(
                status=CommandStatus.FAILED,
                error="device is required"
            )

        try:
            # Check if device is mounted
            mounted = self._run_bash(
                f"mountpoint -q {device} && echo 1 || echo 0",
                check=False
            )

            if mounted.strip() == "1":
                return CommandResult(
                    status=CommandStatus.FAILED,
                    error=f"{device} is still mounted"
                )

            # TODO: Actually wipe the disk
            # self._run_bash(f"dd if=/dev/zero of={device} bs=1M count=10")

            return CommandResult(status=CommandStatus.APPLIED)

        except Exception as e:
            return CommandResult(
                status=CommandStatus.FAILED,
                error=str(e)
            )
