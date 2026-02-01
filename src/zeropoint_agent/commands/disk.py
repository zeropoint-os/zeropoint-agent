"""Managed Disk Commands.

Commands for managing disk partitions, formats, and layouts:
- AddManagedDisk: Register a disk for management
- RemoveManagedDisk: Unregister a disk
- PartitionManagedDisk: Define partition layout
- UpdatePartitionsManagedDisk: Patch partition layout
- AutoPartitionManagedDisk: Generate standard partition layout
- ResetPartitionsManagedDisk: Capture current layout as desired state
- FormatManagedDisk: Define filesystem for a partition
"""

from typing import Dict, Any, List
import logging
from . import Command, CommandResult, CommandStatus
from ..state_store import StateStore
from ..hw_probe import HWProbe

logger = logging.getLogger(__name__)


class DiskCommand(Command):
    """Base class for disk operations."""

    def __init__(self, name: str):
        super().__init__(f"disk:{name}")


class AddManagedDisk(DiskCommand):
    """Register a disk for management."""

    def __init__(self):
        super().__init__("add-managed")

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Add a disk to management.
        
        Expects resource with:
        - disk_id: stable disk ID (e.g., nvme-Samsung...)
        """
        disk_id = resource.get("disk_id")
        
        if not disk_id:
            return CommandResult(status=CommandStatus.FAILED, error="disk_id is required")
        
        try:
            store = StateStore.get_instance()
            store.add_managed_disk(disk_id)
            
            # Get the persisted disk from store
            disks = store.get_desired_resources("disks")
            disk = next((d for d in disks if d["id"] == disk_id), None)
            
            logger.info(f"Added managed disk: {disk_id}")
            return CommandResult(
                status=CommandStatus.APPLIED,
                output={"disk": disk}
            )
        except Exception as e:
            logger.error(f"Failed to add managed disk {disk_id}: {e}")
            return CommandResult(status=CommandStatus.FAILED, error=str(e))


class RemoveManagedDisk(DiskCommand):
    """Unregister a disk from management."""

    def __init__(self):
        super().__init__("remove-managed")

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Remove a disk from management.
        
        Expects resource with:
        - disk_id: disk ID to remove
        """
        disk_id = resource.get("disk_id")
        
        if not disk_id:
            return CommandResult(status=CommandStatus.FAILED, error="disk_id is required")
        
        try:
            store = StateStore.get_instance()
            store.remove_managed_disk(disk_id)
            
            logger.info(f"Removed managed disk: {disk_id}")
            return CommandResult(
                status=CommandStatus.APPLIED,
                output={"disk_id": disk_id}
            )
        except Exception as e:
            logger.error(f"Failed to remove managed disk {disk_id}: {e}")
            return CommandResult(status=CommandStatus.FAILED, error=str(e))


class PartitionManagedDisk(DiskCommand):
    """Set partition layout for a disk (replaces existing)."""

    def __init__(self):
        super().__init__("partition-managed")

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Set partitions for a disk.
        
        Expects resource with:
        - disk_id: disk ID
        - partitions: list of {index, size_mb, type, label}
        """
        disk_id = resource.get("disk_id")
        partitions = resource.get("partitions", [])
        
        if not disk_id:
            return CommandResult(status=CommandStatus.FAILED, error="disk_id is required")
        
        if not partitions:
            return CommandResult(status=CommandStatus.FAILED, error="partitions list is required")
        
        try:
            store = StateStore.get_instance()
            store.write_partitions(disk_id, partitions)
            
            # Return persisted state
            persisted = store.get_partitions(disk_id)
            
            logger.info(f"Set {len(partitions)} partition(s) for {disk_id}")
            return CommandResult(
                status=CommandStatus.BLOCKED,  # Requires daemon to actually create
                reason="pending_partitions",
                output={"disk_id": disk_id, "partitions": persisted}
            )
        except Exception as e:
            logger.error(f"Failed to set partitions for {disk_id}: {e}")
            return CommandResult(status=CommandStatus.FAILED, error=str(e))


class UpdatePartitionsManagedDisk(DiskCommand):
    """Update specific partitions in layout (PATCH operation)."""

    def __init__(self):
        super().__init__("update-partitions-managed")

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Update specific partitions.
        
        Expects resource with:
        - disk_id: disk ID
        - updates: dict {partition_index: {size_mb, type, label, ...}}
        """
        disk_id = resource.get("disk_id")
        updates = resource.get("updates", {})
        
        if not disk_id:
            return CommandResult(status=CommandStatus.FAILED, error="disk_id is required")
        
        if not updates:
            return CommandResult(status=CommandStatus.FAILED, error="updates dict is required")
        
        try:
            store = StateStore.get_instance()
            store.update_partitions(disk_id, updates)
            
            persisted = store.get_partitions(disk_id)
            
            logger.info(f"Updated {len(updates)} partition(s) for {disk_id}")
            return CommandResult(
                status=CommandStatus.BLOCKED,
                reason="pending_partitions",
                output={"disk_id": disk_id, "partitions": persisted}
            )
        except Exception as e:
            logger.error(f"Failed to update partitions for {disk_id}: {e}")
            return CommandResult(status=CommandStatus.FAILED, error=str(e))


class AutoPartitionManagedDisk(DiskCommand):
    """Generate standard partition layout for a disk."""

    def __init__(self):
        super().__init__("auto-partition-managed")

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Auto-partition a disk with standard layout.
        
        Expects resource with:
        - disk_id: disk ID
        - boot_size_mb: size for /boot partition (default: 512)
        """
        disk_id = resource.get("disk_id")
        boot_size = resource.get("boot_size_mb", 512)
        
        if not disk_id:
            return CommandResult(status=CommandStatus.FAILED, error="disk_id is required")
        
        try:
            # Get disk from hardware
            disk = HWProbe.get_disk(disk_id)
            if not disk:
                return CommandResult(status=CommandStatus.FAILED, error=f"Disk not found: {disk_id}")
            
            # Get disk size from lsblk
            size_mb = disk.size_mb if hasattr(disk, 'size_mb') else None
            if not size_mb:
                return CommandResult(status=CommandStatus.FAILED, error="Could not determine disk size")
            
            # Standard layout: boot + root
            partitions = [
                {"index": 0, "size_mb": boot_size, "type": "primary", "label": "boot"},
                {"index": 1, "size_mb": size_mb - boot_size, "type": "primary", "label": "root"}
            ]
            
            store = StateStore.get_instance()
            store.write_partitions(disk_id, partitions)
            
            persisted = store.get_partitions(disk_id)
            
            logger.info(f"Auto-partitioned {disk_id} with {len(partitions)} partition(s)")
            return CommandResult(
                status=CommandStatus.BLOCKED,
                reason="pending_partitions",
                output={"disk_id": disk_id, "partitions": persisted}
            )
        except Exception as e:
            logger.error(f"Failed to auto-partition {disk_id}: {e}")
            return CommandResult(status=CommandStatus.FAILED, error=str(e))


class ResetPartitionsManagedDisk(DiskCommand):
    """Capture current physical partition layout as desired state."""

    def __init__(self):
        super().__init__("reset-partitions-managed")

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Reset to current physical layout.
        
        Expects resource with:
        - disk_id: disk ID
        """
        disk_id = resource.get("disk_id")
        
        if not disk_id:
            return CommandResult(status=CommandStatus.FAILED, error="disk_id is required")
        
        try:
            # Get disk from hardware
            disk = HWProbe.get_disk(disk_id)
            if not disk:
                return CommandResult(status=CommandStatus.FAILED, error=f"Disk not found: {disk_id}")
            
            # Get current partitions from HWProbe
            partitions = []
            if hasattr(disk, 'partitions') and disk.partitions:
                for i, part in enumerate(disk.partitions):
                    partitions.append({
                        "index": i,
                        "size_mb": part.size_mb if hasattr(part, 'size_mb') else None,
                        "type": "primary",
                        "label": part.label if hasattr(part, 'label') else f"part{i}"
                    })
            
            if not partitions:
                return CommandResult(status=CommandStatus.FAILED, error="No partitions found on disk")
            
            store = StateStore.get_instance()
            store.write_partitions(disk_id, partitions)
            
            persisted = store.get_partitions(disk_id)
            
            logger.info(f"Reset {disk_id} to current layout: {len(partitions)} partition(s)")
            return CommandResult(
                status=CommandStatus.APPLIED,  # Current layout is already applied
                output={"disk_id": disk_id, "partitions": persisted}
            )
        except Exception as e:
            logger.error(f"Failed to reset partitions for {disk_id}: {e}")
            return CommandResult(status=CommandStatus.FAILED, error=str(e))


class FormatManagedDisk(DiskCommand):
    """Define filesystem for a partition."""

    def __init__(self):
        super().__init__("format-managed")

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Format a partition.
        
        Expects resource with:
        - disk_id: disk ID
        - partition_index: partition index (0, 1, etc.)
        - filesystem: ext4, xfs, etc.
        - confirm_wipe: true/false (true requires daemon execution)
        """
        disk_id = resource.get("disk_id")
        partition_index = resource.get("partition_index")
        filesystem = resource.get("filesystem", "ext4")
        confirm_wipe = resource.get("confirm_wipe", False)
        
        if not disk_id or partition_index is None:
            return CommandResult(status=CommandStatus.FAILED, error="disk_id and partition_index required")
        
        try:
            store = StateStore.get_instance()
            store.write_format(disk_id, partition_index, filesystem, confirm_wipe)
            
            formats = store.get_formats(disk_id)
            partition_format = formats.get(partition_index, {})
            
            status = CommandStatus.BLOCKED if confirm_wipe else CommandStatus.APPLIED
            reason = "pending_format_with_wipe" if confirm_wipe else None
            
            logger.info(f"Set format for {disk_id}:{partition_index} to {filesystem} (wipe={confirm_wipe})")
            return CommandResult(
                status=status,
                reason=reason,
                output={"disk_id": disk_id, "partition_index": partition_index, "format": partition_format}
            )
        except Exception as e:
            logger.error(f"Failed to format {disk_id}:{partition_index}: {e}")
            return CommandResult(status=CommandStatus.FAILED, error=str(e))
