"""Hardware probing via libparted.

Discovers actual hardware state: disks, partitions, filesystems.
Uses /dev/by-id stable identifiers for disk IDs.
"""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import subprocess
import json


@dataclass
class Partition:
    """Partition information."""
    id: str  # Stable ID basename from /dev/disk/by-id/ (e.g. ata-QEMU_HARDDISK_QM00001-part1)
    device: str  # /dev/sda1, /dev/nvme0n1p1, etc.
    size: int  # bytes
    filesystem: Optional[str]  # ext4, swap, etc.
    flags: List[str]  # boot, raid, lvm, etc.


@dataclass
class Disk:
    """Disk information."""
    id: str  # Stable ID basename from /dev/disk/by-id/ (e.g. ata-QEMU_HARDDISK_QM00001)
    device: str  # /dev/sda, /dev/nvme0n1, etc.
    size: int  # bytes
    sector_size: int  # bytes
    partitions: List[Partition]


class HWProbe:
    """Hardware probing interface."""

    @staticmethod
    def get_disks() -> List[Disk]:
        """Get all available disks with partition info.
        
        Uses lsblk and by-id mappings to provide stable disk/partition IDs.
        Returns list of Disk objects.
        """
        disks = []
        
        # Get by-id mapping for disks and partitions
        disk_id_map = HWProbe._get_by_id_map("disk")
        part_id_map = HWProbe._get_by_id_map("part")
        
        # Parse lsblk output for all block devices
        try:
            output = subprocess.run(
                ["lsblk", "-J", "-b", "-o", "NAME,SIZE,TYPE,FSTYPE"],
                capture_output=True,
                text=True,
                check=True
            )
            
            data = json.loads(output.stdout)
            
            # Process block devices
            for dev in data.get("blockdevices", []):
                if dev.get("type") != "disk":
                    continue
                
                device_name = f"/dev/{dev['name']}"
                stable_id = disk_id_map.get(device_name, device_name)
                # Extract basename from /dev/disk/by-id/ path
                id_basename = stable_id.split("/")[-1] if stable_id.startswith("/") else stable_id
                
                disk = Disk(
                    id=id_basename,
                    device=device_name,
                    size=int(dev.get("size", 0)),
                    sector_size=512,  # TODO: read from sysfs
                    partitions=[]
                )
                
                # Parse partitions
                for child in dev.get("children", []):
                    if child.get("type") == "part":
                        partition_device = f"/dev/{child['name']}"
                        partition_stable_id = part_id_map.get(partition_device, partition_device)
                        # Extract basename from /dev/disk/by-id/ path
                        partition_id_basename = partition_stable_id.split("/")[-1] if partition_stable_id.startswith("/") else partition_stable_id
                        
                        partition = Partition(
                            id=partition_id_basename,
                            device=partition_device,
                            size=int(child.get("size", 0)),
                            filesystem=child.get("fstype") or None,
                            flags=[]
                        )
                        disk.partitions.append(partition)
                
                disks.append(disk)
        
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
            raise RuntimeError(f"Failed to probe disks: {e}")
        
        return disks

    @staticmethod
    def get_disk(disk_id: str) -> Optional[Disk]:
        """Get a specific disk by ID.
        
        Args:
            disk_id: Stable ID from /dev/by-id or device path
            
        Returns:
            Disk object or None if not found
        """
        disks = HWProbe.get_disks()
        
        for disk in disks:
            if disk.id == disk_id or disk.device == disk_id:
                return disk
        
        return None

    @staticmethod
    def _get_by_id_map(kind: str = "disk") -> Dict[str, str]:
        """Build mapping from device paths to /dev/disk/by-id/ stable IDs.
        
        Args:
            kind: "disk" for disks, "part" for partitions
            
        Returns dict: {"/dev/sda": "/dev/disk/by-id/ata-QEMU_HARDDISK_QM00001"}
        """
        mapping = {}
        
        try:
            # Use find to get all symlinks in /dev/disk/by-id/
            output = subprocess.run(
                ["find", "/dev/disk/by-id/", "-type", "l"],
                capture_output=True,
                text=True,
                check=False
            )
            
            for symlink in output.stdout.strip().split("\n"):
                if not symlink:
                    continue
                
                # Filter by kind
                if kind == "disk" and "-part" in symlink:
                    continue
                if kind == "part" and "-part" not in symlink:
                    continue
                
                # Resolve symlink to actual device
                try:
                    resolved = subprocess.run(
                        ["readlink", "-f", symlink],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    device_path = resolved.stdout.strip()
                    mapping[device_path] = symlink
                except Exception:
                    pass
        
        except Exception:
            pass  # If by-id not available, fall back to device names
        
        return mapping
