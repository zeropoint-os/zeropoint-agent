"""Output contracts for the graph-based language.

Each dataclass is an output contract — the shape of what a node produces.
The same type serves as:
  - The contract (structural: "I produce a device, number, size")
  - The desired state (the value the user wants in that contract)
  - The resolved state (the runtime value after execution)

Nodes are typed I → O. These are the O types.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum


class NodeStatus(Enum):
    """Status of a node in the graph."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    BLOCKED = "blocked"
    PENDING_REBOOT = "pending_reboot"


# --- Output Contracts ---

# --- System / Read-only ---

@dataclass
class NetworkResult:
    """Contract for a network interface node."""
    interface: str
    ip: Optional[str] = None
    up: bool = False


@dataclass
class DockerResult:
    """Contract for a Docker daemon node."""
    running: bool = False
    version: Optional[str] = None


@dataclass
class DriverResult:
    """Contract for a driver node."""
    driver: str
    version: Optional[str] = None
    loaded: bool = False




@dataclass
class DiskResult:
    """Contract for a disk node.

    Keyed by stable ID from /dev/disk/by-id/ — survives reboot,
    disk reordering, and controller changes. device_path is resolved
    at runtime from the stable ID.
    """
    stable_id: str        # KEY: ata-QEMU_HARDDISK_QM00001, nvme-eui.0025385c2140105d
    device_path: str = "" # /dev/sda — resolved at runtime, may change across boots
    size: int = 0         # bytes
    free: int = 0         # unallocated bytes
    sector_size: int = 512


@dataclass
class PartitionResult:
    """Contract for a partition node.

    Keyed by stable partition ID from /dev/disk/by-id/ (e.g. ata-...-part1).
    """
    number: int
    size_mb: int
    stable_id: str = ""   # KEY: ata-QEMU_HARDDISK_QM00001-part1
    device_path: str = "" # /dev/sda1 — resolved at runtime
    type: str = "83"
    label: Optional[str] = None


@dataclass
class FormatResult:
    """Contract for a filesystem format node.

    Uses the partition's stable ID to locate the device at runtime.
    UUID is filled after mkfs.
    """
    filesystem: str = "ext4"
    stable_id: str = ""   # partition stable ID (from parent)
    device_path: str = "" # resolved from stable_id at runtime
    label: Optional[str] = None
    uuid: Optional[str] = None


@dataclass
class MountResult:
    """Contract for a mount node.

    Mounts by stable ID or UUID, not device path.
    """
    mountpoint: str
    stable_id: str = ""   # from parent format/partition
    device_path: str = "" # resolved at runtime
    options: str = "defaults"


@dataclass
class PathResult:
    """Contract for a path/directory node."""
    path: str
    mode: str = "0755"
    is_dir: bool = True


@dataclass
class VarResult:
    """Contract for a variable/config node."""
    name: str
    value: str


@dataclass
class ModuleResult:
    """Contract for a module node (Terraform-managed container)."""
    source: str
    module_id: str
    variables: Dict[str, Any] = field(default_factory=dict)
    container_id: Optional[str] = None
    container_ip: Optional[str] = None
    ports: Dict[str, int] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LinkResult:
    """Contract for a link node (inter-module variable binding)."""
    from_module: str
    to_module: str
    bindings: Dict[str, str] = field(default_factory=dict)
    resolved_bindings: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExposureResult:
    """Contract for an exposure node (Envoy xDS route)."""
    module_id: str
    port: int
    protocol: str = "http"
    path_prefix: str = "/"
    route_name: Optional[str] = None
    external_url: Optional[str] = None
    description: Optional[str] = None
