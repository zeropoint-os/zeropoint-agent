"""Mount commands: Add, Edit, Release.

Mount operations:
- AddMount: Create a mount point and mount a disk
- EditMount: Modify mount options
- ReleaseMount: Unmount a disk
"""

from typing import Dict, Any
from . import Command, CommandResult, CommandStatus


class MountCommand(Command):
    """Base class for mount operations."""

    def __init__(self, name: str):
        super().__init__(f"mount:{name}")


class AddMount(MountCommand):
    """Add a mount point."""

    def __init__(self):
        super().__init__("add")

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Add a mount.
        
        Expects resource with:
        - id: mount ID
        - disk_id: ID of the disk to mount
        - mountpoint: /mnt/media, /var/data, etc.
        - options: mount options (defaults, noatime, etc.)
        """
        mount_id = resource.get("id")
        mountpoint = resource.get("mountpoint")
        options = resource.get("options", "defaults")

        if not mountpoint:
            return CommandResult(
                status=CommandStatus.FAILED,
                error="mountpoint is required"
            )

        try:
            # Create mountpoint if it doesn't exist
            self._run_bash(f"mkdir -p {mountpoint}", check=True)

            # TODO: Actually mount the disk
            # Need to resolve disk_id to device first
            # self._run_bash(f"mount -o {options} /dev/XXX {mountpoint}", check=True)

            return CommandResult(
                status=CommandStatus.APPLIED,
                output={"mountpoint": mountpoint, "options": options}
            )

        except Exception as e:
            return CommandResult(
                status=CommandStatus.FAILED,
                error=str(e)
            )


class EditMount(MountCommand):
    """Edit mount options."""

    def __init__(self):
        super().__init__("edit")

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Edit mount options."""
        mountpoint = resource.get("mountpoint")
        options = resource.get("options", "defaults")

        if not mountpoint:
            return CommandResult(
                status=CommandStatus.FAILED,
                error="mountpoint is required"
            )

        try:
            # Remount with new options
            self._run_bash(f"mount -o remount,{options} {mountpoint}", check=True)

            return CommandResult(
                status=CommandStatus.APPLIED,
                output={"mountpoint": mountpoint, "options": options}
            )

        except Exception as e:
            return CommandResult(
                status=CommandStatus.FAILED,
                error=str(e)
            )


class ReleaseMount(MountCommand):
    """Unmount a mount point."""

    def __init__(self):
        super().__init__("release")

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Release a mount (unmount it)."""
        mountpoint = resource.get("mountpoint")

        if not mountpoint:
            return CommandResult(
                status=CommandStatus.FAILED,
                error="mountpoint is required"
            )

        try:
            # Check if mountpoint is actually mounted
            is_mounted = self._run_bash(
                f"mountpoint -q {mountpoint} && echo 1 || echo 0",
                check=False
            ).strip()

            if is_mounted != "1":
                # Not mounted, nothing to do
                return CommandResult(status=CommandStatus.APPLIED)

            # Unmount
            self._run_bash(f"umount {mountpoint}", check=True)

            return CommandResult(status=CommandStatus.APPLIED)

        except Exception as e:
            return CommandResult(
                status=CommandStatus.FAILED,
                error=str(e)
            )
