"""Hardware probing via libparted.

Discovers actual hardware state: disks, partitions, filesystems.
Uses /dev/by-id stable identifiers for disk IDs.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import subprocess
import json
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Partition:
    """Partition information."""
    id: str  # Stable ID basename from /dev/disk/by-id/ (e.g. ata-QEMU_HARDDISK_QM00001-part1)
    device: str  # /dev/sda1, /dev/nvme0n1p1, etc.
    size: int  # bytes
    free: Optional[int] = None  # Free space in bytes if mounted
    filesystem: Optional[str] = None  # ext4, swap, etc.
    flags: List[str] = field(default_factory=list)  # boot, raid, lvm, etc.
    number: int = 0
    uuid: Optional[str] = None
    label: Optional[str] = None
    partuuid: Optional[str] = None
    parttype: Optional[str] = None
    mountpoints: List[str] = field(default_factory=list)
    mount_options: Dict[str, str] = field(default_factory=dict)
    is_root: bool = False
    is_boot: bool = False
    holders: List[str] = field(default_factory=list)


@dataclass
class Disk:
    """Disk information."""
    id: str  # Stable ID basename from /dev/disk/by-id/ (e.g. ata-QEMU_HARDDISK_QM00001)
    device: str  # /dev/sda, /dev/nvme0n1, etc.
    size: int  # bytes
    free: Optional[int] = None  # Unallocated free space in bytes
    sector_size: int = 512  # bytes
    partitions: List[Partition] = field(default_factory=list)
    removable: bool = False
    read_only: bool = False
    is_root: bool = False
    is_boot: bool = False
    holders: List[str] = field(default_factory=list)


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
            logger.debug("Executing lsblk to enumerate block devices")
            output = subprocess.run(
                [
                    "lsblk", "-J", "-b", "-o",
                    "NAME,PATH,SIZE,TYPE,FSTYPE,UUID,LABEL,PARTUUID,"
                    "PARTTYPE,MOUNTPOINTS,LOG-SEC,RO,RM",
                ],
                capture_output=True,
                text=True,
                check=True
            )
            
            data = json.loads(output.stdout)
            
            mount_options = HWProbe._mount_options()
            root_device = HWProbe._mount_device("/")
            boot_devices = {
                device for device in (
                    HWProbe._mount_device("/boot"),
                    HWProbe._mount_device("/boot/efi"),
                )
                if device
            }

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
                    sector_size=int(dev.get("log-sec") or 512),
                    removable=bool(dev.get("rm")),
                    read_only=bool(dev.get("ro")),
                    holders=HWProbe._holders(dev["name"]),
                )
                logger.debug(f"Discovered disk: {device_name} (ID: {id_basename}, size: {disk.size} bytes)")
                
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
                            number=HWProbe._partition_number(child["name"]),
                            uuid=child.get("uuid") or None,
                            label=child.get("label") or None,
                            partuuid=child.get("partuuid") or None,
                            parttype=child.get("parttype") or None,
                            holders=HWProbe._holders(child["name"]),
                        )
                        partition.flags = sorted(
                            HWProbe._descendant_types(child)
                        )

                        raw_mountpoints = child.get("mountpoints") or []
                        if isinstance(raw_mountpoints, str):
                            raw_mountpoints = [raw_mountpoints]
                        partition.mountpoints = [
                            str(mountpoint)
                            for mountpoint in raw_mountpoints
                            if mountpoint
                        ]
                        partition.mount_options = {
                            mountpoint: mount_options.get(
                                os.path.realpath(partition_device), {}
                            ).get(mountpoint, "defaults")
                            for mountpoint in partition.mountpoints
                        }
                        partition.free = (
                            HWProbe._get_filesystem_free(
                                partition.mountpoints[0]
                            )
                            if partition.mountpoints else None
                        )
                        real_partition = os.path.realpath(partition_device)
                        partition.is_root = real_partition == root_device
                        partition.is_boot = real_partition in boot_devices
                        disk.is_root = disk.is_root or partition.is_root
                        disk.is_boot = disk.is_boot or partition.is_boot
                        logger.debug(f"  Partition: {partition_device} (ID: {partition_id_basename}, size: {partition_size}, fs: {partition.filesystem})")
                        
                        disk.partitions.append(partition)
                
                disk.free = max(0, disk.size - allocated_size)
                
                disks.append(disk)
        
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to probe disks: {e}", exc_info=True)
            raise RuntimeError(f"Failed to probe disks: {e}")
        
        logger.info(f"Disk discovery complete: found {len(disks)} disks")
        return disks

    @staticmethod
    def get_disk(disk_id: str) -> Optional[Disk]:
        """Get a specific disk by ID.
        
        Args:
            disk_id: Stable ID from /dev/by-id or device path
            
        Returns:
            Disk object or None if not found
        """
        logger.debug(f"Looking up disk: {disk_id}")
        disks = HWProbe.get_disks()
        
        for disk in disks:
            if disk.id == disk_id or disk.device == disk_id:
                logger.debug(f"Found disk {disk_id}: {disk.device}")
                return disk
        
        logger.warning(f"Disk not found: {disk_id}")
        return None

    @staticmethod
    def _get_by_id_map(kind: str = "disk") -> Dict[str, str]:
        """Build mapping from device paths to /dev/disk/by-id/ stable IDs.
        
        Args:
            kind: "disk" for disks, "part" for partitions
            
        Returns dict: {"/dev/sda": "/dev/disk/by-id/ata-QEMU_HARDDISK_QM00001"}
        """
        logger.debug(f"Building {kind} ID mapping from /dev/disk/by-id/")
        mapping = {}
        
        try:
            # Use find to get all symlinks in /dev/disk/by-id/
            output = subprocess.run(
                ["find", "/dev/disk/by-id/", "-type", "l"],
                capture_output=True,
                text=True,
                check=False
            )
            
            for symlink in sorted(output.stdout.strip().split("\n")):
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
                    mapping.setdefault(device_path, symlink)
                except Exception:
                    pass
        
        except Exception as e:
            logger.debug(f"Error building {kind} ID map: {e}")
            pass  # If by-id not available, fall back to device names
        
        logger.debug(f"Built {kind} ID map with {len(mapping)} entries")
        return mapping

    @staticmethod
    def _get_filesystem_free(mountpoint: str) -> Optional[int]:
        """Get free space for a mounted filesystem.
        
        Args:
            device: Device path (e.g. /dev/sda1)
            
        Returns:
            Free space in bytes, or None if not mounted or error
        """
        try:
            stat = os.statvfs(mountpoint)
            free_bytes = stat.f_bavail * stat.f_frsize
            logger.debug(
                "Filesystem %s: %d bytes free",
                mountpoint,
                free_bytes,
            )
            return free_bytes
        except Exception as e:
            logger.debug(f"Failed to get free space for {device}: {e}")
            pass
        
        return None

    @staticmethod
    def _partition_number(name: str) -> int:
        try:
            with open(
                f"/sys/class/block/{name}/partition",
                "r",
                encoding="utf-8",
            ) as file:
                return int(file.read().strip())
        except (OSError, ValueError):
            return 0

    @staticmethod
    def _holders(name: str) -> List[str]:
        try:
            return sorted(
                path.name
                for path in Path(f"/sys/class/block/{name}/holders").iterdir()
            )
        except OSError:
            return []

    @staticmethod
    def _descendant_types(device: Dict[str, Any]) -> set[str]:
        result: set[str] = set()
        for child in device.get("children") or []:
            child_type = str(child.get("type") or "")
            if child_type:
                result.add(child_type)
            result.update(HWProbe._descendant_types(child))
        return result

    @staticmethod
    def _mount_device(target: str) -> Optional[str]:
        if not os.path.exists(target):
            return None
        result = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE,TARGET", "--target", target],
            capture_output=True,
            text=True,
            check=False,
        )
        parts = result.stdout.strip().split(maxsplit=1)
        if result.returncode != 0 or len(parts) != 2:
            return None
        source, mounted_at = parts
        if (
            not source.startswith("/dev/")
            or os.path.realpath(mounted_at) != os.path.realpath(target)
        ):
            return None
        return os.path.realpath(source)

    @staticmethod
    def _mount_options() -> Dict[str, Dict[str, str]]:
        result: Dict[str, Dict[str, str]] = {}
        output = subprocess.run(
            ["findmnt", "-J", "-o", "SOURCE,TARGET,OPTIONS"],
            capture_output=True,
            text=True,
            check=False,
        )
        if output.returncode != 0:
            return result
        try:
            filesystems = json.loads(output.stdout).get("filesystems", [])
        except json.JSONDecodeError:
            return result

        def walk(items: List[Dict[str, Any]]) -> None:
            for item in items:
                source = str(item.get("source") or "")
                target = str(item.get("target") or "")
                if source.startswith("/dev/") and target:
                    device = os.path.realpath(source)
                    result.setdefault(device, {})[target] = str(
                        item.get("options") or "defaults"
                    )
                walk(item.get("children") or [])

        walk(filesystems)
        return result

    @staticmethod
    def get_gpus() -> List[GPU]:
        """Get all available GPUs.
        
        Detects NVIDIA and AMD GPUs. Returns list of GPU objects.
        """
        logger.debug("Starting GPU discovery")
        gpus = []
        
        # Try NVIDIA GPUs first
        try:
            nvidia_gpus = HWProbe._get_nvidia_gpus()
            gpus.extend(nvidia_gpus)
            if nvidia_gpus:
                logger.info(f"Found {len(nvidia_gpus)} NVIDIA GPUs")
        except Exception as e:
            logger.debug(f"NVIDIA GPU detection failed: {e}")
        
        # Try AMD GPUs
        try:
            amd_gpus = HWProbe._get_amd_gpus()
            gpus.extend(amd_gpus)
            if amd_gpus:
                logger.info(f"Found {len(amd_gpus)} AMD GPUs")
        except Exception as e:
            logger.debug(f"AMD GPU detection failed: {e}")
        
        logger.info(f"GPU discovery complete: found {len(gpus)} total GPUs")
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
