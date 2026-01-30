"""Hardware probing via libparted.

Discovers actual hardware state: disks, partitions, filesystems.
Uses /dev/by-id stable identifiers for disk IDs.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import subprocess
import json
import os


@dataclass
class Partition:
    """Partition information."""
    id: str  # Stable ID basename from /dev/disk/by-id/ (e.g. ata-QEMU_HARDDISK_QM00001-part1)
    device: str  # /dev/sda1, /dev/nvme0n1p1, etc.
    size: int  # bytes
    free: Optional[int] = None  # Free space in bytes if mounted
    filesystem: Optional[str] = None  # ext4, swap, etc.
    flags: List[str] = field(default_factory=list)  # boot, raid, lvm, etc.


@dataclass
class Disk:
    """Disk information."""
    id: str  # Stable ID basename from /dev/disk/by-id/ (e.g. ata-QEMU_HARDDISK_QM00001)
    device: str  # /dev/sda, /dev/nvme0n1, etc.
    size: int  # bytes
    free: Optional[int] = None  # Unallocated free space in bytes
    sector_size: int = 512  # bytes
    partitions: List[Partition] = field(default_factory=list)


@dataclass
class GPU:
    """GPU information."""
    id: str  # Basename identifier (e.g. nvidia-0 or amd-0)
    device: str  # Device path if available (e.g. /dev/nvidia0)
    name: str  # GPU name (e.g. NVIDIA A100 PCIe 40GB)
    memory_total: int  # Total memory in bytes
    memory_free: Optional[int] = None  # Free memory in bytes
    driver_version: Optional[str] = None  # Driver version (e.g. 535.104.05)
    compute_capability: Optional[str] = None  # Compute capability (e.g. 8.0) for NVIDIA


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
                )
                
                # Parse partitions and calculate free space
                allocated_size = 0
                for child in dev.get("children", []):
                    if child.get("type") == "part":
                        partition_device = f"/dev/{child['name']}"
                        partition_stable_id = part_id_map.get(partition_device, partition_device)
                        # Extract basename from /dev/disk/by-id/ path
                        partition_id_basename = partition_stable_id.split("/")[-1] if partition_stable_id.startswith("/") else partition_stable_id
                        
                        partition_size = int(child.get("size", 0))
                        allocated_size += partition_size
                        
                        partition = Partition(
                            id=partition_id_basename,
                            device=partition_device,
                            size=partition_size,
                            filesystem=child.get("fstype") or None,
                        )
                        
                        # Get free space for mounted filesystem
                        partition.free = HWProbe._get_filesystem_free(partition_device)
                        
                        disk.partitions.append(partition)
                
                # Calculate unallocated disk space (not in any partition)
                disk.free = max(0, disk.size - allocated_size)
                
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

    @staticmethod
    def _get_filesystem_free(device: str) -> Optional[int]:
        """Get free space for a mounted filesystem.
        
        Args:
            device: Device path (e.g. /dev/sda1)
            
        Returns:
            Free space in bytes, or None if not mounted or error
        """
        try:
            # Use df to get filesystem free space
            output = subprocess.run(
                ["df", "-B1", device],
                capture_output=True,
                text=True,
                check=False
            )
            
            # Parse df output: last line, 4th column is available space
            if output.returncode == 0:
                lines = output.stdout.strip().split("\n")
                if len(lines) > 1:
                    parts = lines[1].split()
                    if len(parts) > 3:
                        return int(parts[3])
        except Exception:
            pass
        
        return None

    @staticmethod
    def get_gpus() -> List[GPU]:
        """Get all available GPUs.
        
        Detects NVIDIA and AMD GPUs. Returns list of GPU objects.
        """
        gpus = []
        
        # Try NVIDIA GPUs first
        try:
            gpus.extend(HWProbe._get_nvidia_gpus())
        except Exception:
            pass
        
        # Try AMD GPUs
        try:
            gpus.extend(HWProbe._get_amd_gpus())
        except Exception:
            pass
        
        return gpus

    @staticmethod
    def _get_nvidia_gpus() -> List[GPU]:
        """Get NVIDIA GPUs using nvidia-smi."""
        gpus = []
        
        try:
            # Query nvidia-smi for GPU info
            output = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.total,memory.free,driver_version,compute_cap",
                    "--format=csv,noheader,nounits"
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=5
            )
            
            for line in output.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    try:
                        index = parts[0]
                        name = parts[1] if len(parts) > 1 else f"NVIDIA GPU {index}"
                        memory_total = int(float(parts[2]) * 1024 * 1024 * 1024) if len(parts) > 2 else 0  # GB to bytes
                        memory_free = int(float(parts[3]) * 1024 * 1024 * 1024) if len(parts) > 3 else None  # GB to bytes
                        driver_version = parts[4].strip() if len(parts) > 4 else None
                        compute_capability = parts[5].strip() if len(parts) > 5 else None
                        
                        gpu = GPU(
                            id=f"nvidia-{index}",
                            device=f"/dev/nvidia{index}",
                            name=name,
                            memory_total=memory_total,
                            memory_free=memory_free,
                            driver_version=driver_version,
                            compute_capability=compute_capability,
                        )
                        gpus.append(gpu)
                    except (ValueError, IndexError):
                        continue
        
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        return gpus

    @staticmethod
    def _get_amd_gpus() -> List[GPU]:
        """Get AMD GPUs using rocm-smi or lspci."""
        gpus = []
        
        try:
            # Try rocm-smi first
            output = subprocess.run(
                ["rocm-smi", "--showproductname", "--showmeminfo", "all"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5
            )
            
            # Parse rocm-smi output (format varies)
            lines = output.stdout.strip().split("\n")
            gpu_index = 0
            
            for line in lines:
                if "GPU" in line and ":" in line:
                    parts = line.split(":")
                    if len(parts) > 1:
                        name = parts[1].strip()
                        gpu = GPU(
                            id=f"amd-{gpu_index}",
                            device=f"/dev/kfd{gpu_index}",
                            name=name,
                            memory_total=0,  # Would need to parse separately
                            memory_free=None,
                        )
                        gpus.append(gpu)
                        gpu_index += 1
        
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        return gpus


