"""Typed entity contracts for DAG nodes.

Each node type declares:
- A Desired dataclass (what the user wants)
- A Result dataclass (what the node produces for children)

These flow through the graph via typed Inputs projections.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum


class NodeStatus(Enum):
    """Status of a node in the DAG."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    BLOCKED = "blocked"
    PENDING_REBOOT = "pending_reboot"


class NodeOperation(Enum):
    """Operation type for a node."""
    ADD = "add"
    REMOVE = "remove"
    MOVE = "move"
    COPY = "copy"


# --- Disk ---

@dataclass
class DiskDesired:
    """Desired state for a disk node."""
    device: str  # /dev/sda, /dev/nvme0n1

@dataclass
class DiskResult:
    """Result produced by a disk node."""
    id: str           # stable ID from /dev/disk/by-id/
    device: str       # /dev/sda
    size: int         # bytes
    free: int = 0     # unallocated bytes
    sector_size: int = 512


# --- Partition ---

@dataclass
class PartitionDesired:
    """Desired state for a partition node."""
    number: int
    size_mb: int
    type: str = "83"           # Linux partition type
    label: Optional[str] = None

@dataclass
class PartitionResult:
    """Result produced by a partition node."""
    id: str           # stable ID from /dev/disk/by-id/-partN
    device: str       # /dev/sda1
    number: int
    size: int         # bytes
    type: str = "83"


# --- Format ---

@dataclass
class FormatDesired:
    """Desired state for a filesystem format node."""
    filesystem: str = "ext4"
    label: Optional[str] = None
    confirm_wipe: bool = False

@dataclass
class FormatResult:
    """Result produced by a format node."""
    device: str       # /dev/sda1 (from parent partition)
    filesystem: str
    label: str = ""
    uuid: Optional[str] = None


# --- Mount ---

@dataclass
class MountDesired:
    """Desired state for a mount node."""
    mountpoint: str       # /mnt/data
    options: str = "defaults"

@dataclass
class MountResult:
    """Result produced by a mount node."""
    device: str           # /dev/sda1 (from parent)
    mountpoint: str
    options: str = "defaults"


# --- Path ---

@dataclass
class PathDesired:
    """Desired state for a path/directory node."""
    path: str             # absolute path
    mode: str = "0755"
    is_dir: bool = True

@dataclass
class PathResult:
    """Result produced by a path node."""
    path: str
    mode: str = "0755"
    is_dir: bool = True


# --- Var ---

@dataclass
class VarDesired:
    """Desired state for a variable/config node."""
    name: str
    value: str

@dataclass
class VarResult:
    """Result produced by a variable node."""
    name: str
    value: str


# --- Module ---

@dataclass
class ModuleDesired:
    """Desired state for a module (Terraform-managed container)."""
    source: str           # terraform module source
    module_id: str        # unique module identifier
    variables: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

@dataclass
class ModuleResult:
    """Result produced by a module node."""
    module_id: str
    container_id: Optional[str] = None
    container_ip: Optional[str] = None
    ports: Dict[str, int] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)


# --- Link ---

@dataclass
class LinkDesired:
    """Desired state for a link (inter-module variable binding)."""
    from_module: str      # source module ID
    to_module: str        # target module ID
    bindings: Dict[str, str] = field(default_factory=dict)  # output_var -> input_var

@dataclass
class LinkResult:
    """Result produced by a link node."""
    from_module: str
    to_module: str
    resolved_bindings: Dict[str, Any] = field(default_factory=dict)


# --- Exposure ---

@dataclass
class ExposureDesired:
    """Desired state for an exposure (Envoy xDS route)."""
    module_id: str
    port: int
    protocol: str = "http"
    path_prefix: str = "/"
    description: Optional[str] = None

@dataclass
class ExposureResult:
    """Result produced by an exposure node."""
    module_id: str
    port: int
    protocol: str = "http"
    route_name: Optional[str] = None
    external_url: Optional[str] = None
