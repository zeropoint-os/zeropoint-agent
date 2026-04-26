"""Concrete node implementations — the vocabulary of the language.

Each node is a typed I → O transform. Infra-layer nodes (disk, partition,
format, mount, driver) emit systemd units when they can't converge in-process.
"""

import logging
from typing import Optional, Dict, Any

from zeropoint_agent.inode import INode
from zeropoint_agent.entities import (
    DiskResult, PartitionResult, FormatResult, MountResult,
    PathResult, VarResult, ModuleResult, LinkResult, ExposureResult,
    NetworkResult, DockerResult, DriverResult,
)

logger = logging.getLogger(__name__)

MARKER_DIR = "/etc/zeropoint"
AGENT_BIN = "/usr/bin/zeropoint-agent"


def _systemd_unit(node_id: str, description: str, parent_ids: list,
                  exec_start: str, exec_verify: str) -> str:
    """Generate a systemd oneshot unit for a DAG node."""
    after = "\n".join(f"After=zeropoint-{pid}.service" for pid in parent_ids)
    requires = "\n".join(f"Requires=zeropoint-{pid}.service" for pid in parent_ids)

    return f"""[Unit]
Description=ZeroPoint: {description}
{after}
{requires}
DefaultDependencies=no

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart={exec_start}
ExecStartPost=/bin/touch {MARKER_DIR}/.zeropoint-{node_id}

[Install]
WantedBy=multi-user.target
"""


# --- Read-only system nodes ---

class NetworkNode(INode[None, NetworkResult]):
    """Observes network interface state. Read-only."""

    def __init__(self, interface: str = "eth0"):
        self.interface = interface

    def resolve(self, input: None) -> NetworkResult:
        logger.info(f"NetworkNode.resolve() — probing {self.interface}")
        # TODO: probe interface via ip/ifconfig
        return NetworkResult(interface=self.interface)

    def mock_resolve(self, input: None) -> NetworkResult:
        return NetworkResult(interface=self.interface, ip="192.168.1.10", up=True)

    def verify(self) -> bool:
        # TODO: check interface is up
        return False

    def remove(self) -> bool:
        return True  # can't remove, but no-op is fine

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        return _systemd_unit(
            node_id, f"Wait for network interface {self.interface}",
            parent_ids,
            exec_start=f"/usr/bin/ip link show {self.interface} up",
            exec_verify=f"/usr/bin/ip link show {self.interface}",
        )


class DockerNode(INode[NetworkResult, DockerResult]):
    """Observes Docker daemon state. Read-only."""

    def __init__(self):
        pass

    def resolve(self, input: NetworkResult) -> DockerResult:
        logger.info("DockerNode.resolve() — probing Docker")
        # TODO: docker info
        return DockerResult()

    def mock_resolve(self, input: NetworkResult) -> DockerResult:
        return DockerResult(running=True, version="24.0.7")

    def verify(self) -> bool:
        # TODO: docker info
        return False

    def remove(self) -> bool:
        return True

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        return _systemd_unit(
            node_id, "Wait for Docker daemon",
            parent_ids,
            exec_start="/usr/bin/docker info",
            exec_verify="/usr/bin/docker info",
        )


class DriverNode(INode[None, DriverResult]):
    """Installs/verifies a driver. Emits systemd unit."""

    def __init__(self, driver: str, version: Optional[str] = None):
        self.driver = driver
        self.version = version

    def resolve(self, input: None) -> DriverResult:
        logger.info(f"DriverNode.resolve() — installing {self.driver}")
        # TODO: modprobe / driver install
        return DriverResult(driver=self.driver, version=self.version)

    def mock_resolve(self, input: None) -> DriverResult:
        return DriverResult(driver=self.driver, version=self.version or "535.104",
                            loaded=True)

    def verify(self) -> bool:
        # TODO: lsmod | grep driver
        return False

    def remove(self) -> bool:
        return True

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        cmd = f"/sbin/modprobe {self.driver}"
        return _systemd_unit(
            node_id, f"Load driver {self.driver}",
            parent_ids, exec_start=cmd, exec_verify=cmd,
        )


# --- Infra chain: Disk → Partition → Format → Mount → Path ---
# All keyed by stable IDs from /dev/disk/by-id/

class DiskNode(INode[None, DiskResult]):
    """Discovers/registers a disk by stable ID. Root node.

    Takes a stable ID (e.g. ata-QEMU_HARDDISK_QM00001) as the key.
    Resolves the actual device path (/dev/sda) at runtime via HWProbe.
    """

    def __init__(self, stable_id: str):
        self.stable_id = stable_id

    def resolve(self, input: None) -> DiskResult:
        logger.info(f"DiskNode.resolve() — discovering {self.stable_id}")
        # TODO: resolve stable_id → device_path via HWProbe
        #   disk = HWProbe.get_disk(self.stable_id)
        #   return DiskResult(stable_id=self.stable_id, device_path=disk.device, ...)
        return DiskResult(stable_id=self.stable_id)

    def mock_resolve(self, input: None) -> DiskResult:
        return DiskResult(stable_id=self.stable_id,
                          device_path=f"/dev/disk/by-id/{self.stable_id}",
                          size=1000000000, free=500000000)

    def verify(self) -> bool:
        # TODO: check /dev/disk/by-id/{stable_id} exists
        return False

    def remove(self) -> bool:
        return True

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        return _systemd_unit(
            node_id, f"Discover disk {self.stable_id}",
            parent_ids,
            exec_start=f"/usr/bin/test -e /dev/disk/by-id/{self.stable_id}",
            exec_verify=f"/usr/bin/test -e /dev/disk/by-id/{self.stable_id}",
        )


class PartitionNode(INode[DiskResult, PartitionResult]):
    """Creates a partition on a parent disk.

    The partition's stable ID is derived from the parent disk's stable ID
    (e.g. ata-QEMU_HARDDISK_QM00001-part1). device_path is resolved at runtime.
    """

    def __init__(self, number: int, size_mb: int, type: str = "83"):
        self.number = number
        self.size_mb = size_mb
        self.type = type

    def resolve(self, input: DiskResult) -> PartitionResult:
        stable_id = f"{input.stable_id}-part{self.number}"
        logger.info(f"PartitionNode.resolve() — partition {self.number} on {input.stable_id}")
        # TODO: resolve device_path, sfdisk to create
        return PartitionResult(
            number=self.number, size_mb=self.size_mb,
            stable_id=stable_id,
            device_path=f"{input.device_path}{self.number}" if input.device_path else "",
            type=self.type,
        )

    def mock_resolve(self, input: DiskResult) -> PartitionResult:
        stable_id = f"{input.stable_id}-part{self.number}"
        return PartitionResult(
            number=self.number, size_mb=self.size_mb,
            stable_id=stable_id,
            device_path=f"/dev/disk/by-id/{stable_id}",
            type=self.type,
        )

    def verify(self) -> bool:
        # TODO: check /dev/disk/by-id/{stable_id} exists
        return False

    def remove(self) -> bool:
        return True

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        return _systemd_unit(
            node_id, f"Create partition {self.number}",
            parent_ids,
            exec_start=f"{AGENT_BIN} resolve-node {node_id}",
            exec_verify=f"{AGENT_BIN} verify-node {node_id}",
        )


class FormatNode(INode[PartitionResult, FormatResult]):
    """Creates a filesystem on a parent partition.

    Inherits the partition's stable ID. device_path resolved at runtime.
    """

    def __init__(self, filesystem: str = "ext4", label: Optional[str] = None):
        self.filesystem = filesystem
        self.label = label

    def resolve(self, input: PartitionResult) -> FormatResult:
        logger.info(f"FormatNode.resolve() — mkfs.{self.filesystem} on {input.stable_id}")
        # TODO: mkfs on resolved device_path
        return FormatResult(filesystem=self.filesystem,
                            stable_id=input.stable_id,
                            device_path=input.device_path,
                            label=self.label)

    def mock_resolve(self, input: PartitionResult) -> FormatResult:
        return FormatResult(filesystem=self.filesystem,
                            stable_id=input.stable_id,
                            device_path=input.device_path,
                            label=self.label, uuid="mock-uuid-1234")

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        return _systemd_unit(
            node_id, f"Format filesystem ({self.filesystem})",
            parent_ids,
            exec_start=f"{AGENT_BIN} resolve-node {node_id}",
            exec_verify=f"{AGENT_BIN} verify-node {node_id}",
        )


class MountNode(INode[FormatResult, MountResult]):
    """Mounts a formatted partition at a mountpoint.

    Uses the stable ID from the parent format/partition to mount,
    not the raw device path.
    """

    def __init__(self, mountpoint: str, options: str = "defaults"):
        self.mountpoint = mountpoint
        self.options = options

    def resolve(self, input: FormatResult) -> MountResult:
        logger.info(f"MountNode.resolve() — mounting {input.stable_id} at {self.mountpoint}")
        # TODO: mount /dev/disk/by-id/{stable_id} {mountpoint}
        return MountResult(mountpoint=self.mountpoint,
                           stable_id=input.stable_id,
                           device_path=input.device_path,
                           options=self.options)

    def mock_resolve(self, input: FormatResult) -> MountResult:
        return MountResult(mountpoint=self.mountpoint,
                           stable_id=input.stable_id,
                           device_path=input.device_path,
                           options=self.options)

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        return _systemd_unit(
            node_id, f"Mount {self.mountpoint}",
            parent_ids,
            exec_start=f"/bin/mount {self.mountpoint}",
            exec_verify=f"/bin/mountpoint -q {self.mountpoint}",
        )


class PathNode(INode[MountResult, PathResult]):
    """Creates a directory on a mounted filesystem."""

    def __init__(self, path: str, mode: str = "0755"):
        self.path = path
        self.mode = mode

    def resolve(self, input: MountResult) -> PathResult:
        logger.info(f"PathNode.resolve() — mkdir {self.path}")
        # TODO: os.makedirs
        return PathResult(path=self.path, mode=self.mode)

    def mock_resolve(self, input: MountResult) -> PathResult:
        return PathResult(path=self.path, mode=self.mode)

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        return _systemd_unit(
            node_id, f"Create directory {self.path}",
            parent_ids,
            exec_start=f"/bin/mkdir -p {self.path}",
            exec_verify=f"/usr/bin/test -d {self.path}",
        )


# --- Config ---

class VarNode(INode[None, VarResult]):
    """Sets a variable/config value. Root node. In-process, no systemd unit."""

    def __init__(self, name: str, value: str):
        self.name = name
        self.value = value

    def resolve(self, input: None) -> VarResult:
        return VarResult(name=self.name, value=self.value)

    def mock_resolve(self, input: None) -> VarResult:
        return VarResult(name=self.name, value=self.value)

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True


# --- Module (Terraform-managed container) — in-process, no systemd unit ---

class ModuleNode(INode[PathResult, ModuleResult]):
    """Manages a containerized module via Terraform."""

    def __init__(self, source: str, module_id: str,
                 variables: Optional[Dict[str, Any]] = None):
        self.source = source
        self.module_id = module_id
        self.variables = variables or {}

    def resolve(self, input: PathResult) -> ModuleResult:
        logger.info(f"ModuleNode.resolve() — terraform apply {self.module_id}")
        # TODO: write tfvars, terraform apply
        return ModuleResult(source=self.source, module_id=self.module_id,
                            variables=self.variables)

    def mock_resolve(self, input: PathResult) -> ModuleResult:
        return ModuleResult(
            source=self.source, module_id=self.module_id,
            variables=self.variables,
            container_id="mock-container-abc123",
            container_ip="172.17.0.5",
            ports={"11434": 11434},
        )

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True


# --- Link — in-process, no systemd unit ---

class LinkNode(INode[ModuleResult, LinkResult]):
    """Binds outputs from one module as inputs to another."""

    def __init__(self, from_module: str, to_module: str,
                 bindings: Optional[Dict[str, str]] = None):
        self.from_module = from_module
        self.to_module = to_module
        self.bindings = bindings or {}

    def resolve(self, input: ModuleResult) -> LinkResult:
        logger.info(f"LinkNode.resolve() — linking {self.from_module} → {self.to_module}")
        return LinkResult(from_module=self.from_module, to_module=self.to_module,
                          bindings=self.bindings)

    def mock_resolve(self, input: ModuleResult) -> LinkResult:
        resolved = {k: f"mock-{v}" for k, v in self.bindings.items()}
        return LinkResult(from_module=self.from_module, to_module=self.to_module,
                          bindings=self.bindings, resolved_bindings=resolved)

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True


# --- Exposure — in-process, no systemd unit ---

class ExposureNode(INode[ModuleResult, ExposureResult]):
    """Exposes a module's port via Envoy reverse proxy."""

    def __init__(self, module_id: str, port: int,
                 protocol: str = "http", path_prefix: str = "/",
                 description: Optional[str] = None):
        self.module_id = module_id
        self.port = port
        self.protocol = protocol
        self.path_prefix = path_prefix
        self.description = description

    def resolve(self, input: ModuleResult) -> ExposureResult:
        logger.info(f"ExposureNode.resolve() — exposing {self.module_id}:{self.port}")
        # TODO: push xDS config
        return ExposureResult(module_id=self.module_id, port=self.port,
                              protocol=self.protocol, path_prefix=self.path_prefix,
                              description=self.description)

    def mock_resolve(self, input: ModuleResult) -> ExposureResult:
        return ExposureResult(
            module_id=self.module_id, port=self.port,
            protocol=self.protocol, path_prefix=self.path_prefix,
            description=self.description,
            route_name=f"mock-route-{self.module_id}",
            external_url=f"http://localhost/{self.module_id}",
        )

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True
