"""Concrete node implementations — the vocabulary of the language.

Each node is a typed I → O transform. The node's constructor fields
are the desired state. resolve() produces the runtime output.
verify() probes reality. mock_resolve() produces plausible output
without side effects.
"""

import logging
from typing import Optional, Dict, Any

from zeropoint_agent.inode import INode
from zeropoint_agent.entities import (
    DiskResult, PartitionResult, FormatResult, MountResult,
    PathResult, VarResult, ModuleResult, LinkResult, ExposureResult,
)

logger = logging.getLogger(__name__)


class DiskNode(INode[None, DiskResult]):
    """Discovers/registers a disk. Root node."""

    def __init__(self, device: str):
        self.device = device

    def resolve(self, input: None) -> DiskResult:
        logger.info(f"DiskNode.resolve() — discovering {self.device}")
        # TODO: probe via HWProbe
        return DiskResult(device=self.device)

    def mock_resolve(self, input: None) -> DiskResult:
        return DiskResult(device=self.device, id=f"mock-{self.device}",
                          size=1000000000, free=500000000)

    def verify(self) -> bool:
        # TODO: check device exists via lsblk
        return False

    def remove(self) -> bool:
        return True


class PartitionNode(INode[DiskResult, PartitionResult]):
    """Creates a partition on a parent disk."""

    def __init__(self, number: int, size_mb: int, type: str = "83"):
        self.number = number
        self.size_mb = size_mb
        self.type = type

    def resolve(self, input: DiskResult) -> PartitionResult:
        logger.info(f"PartitionNode.resolve() — partition {self.number} on {input.device}")
        # TODO: sfdisk
        return PartitionResult(
            number=self.number, size_mb=self.size_mb,
            device=f"{input.device}{self.number}", type=self.type,
        )

    def mock_resolve(self, input: DiskResult) -> PartitionResult:
        return PartitionResult(
            number=self.number, size_mb=self.size_mb,
            device=f"{input.device}{self.number}", type=self.type,
        )

    def verify(self) -> bool:
        # TODO: probe partition table
        return False

    def remove(self) -> bool:
        return True


class FormatNode(INode[PartitionResult, FormatResult]):
    """Creates a filesystem on a parent partition."""

    def __init__(self, filesystem: str = "ext4", label: Optional[str] = None):
        self.filesystem = filesystem
        self.label = label

    def resolve(self, input: PartitionResult) -> FormatResult:
        logger.info(f"FormatNode.resolve() — mkfs.{self.filesystem} on {input.device}")
        # TODO: mkfs
        return FormatResult(filesystem=self.filesystem, device=input.device,
                            label=self.label)

    def mock_resolve(self, input: PartitionResult) -> FormatResult:
        return FormatResult(filesystem=self.filesystem, device=input.device,
                            label=self.label, uuid="mock-uuid-1234")

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True


class MountNode(INode[FormatResult, MountResult]):
    """Mounts a formatted partition at a mountpoint."""

    def __init__(self, mountpoint: str, options: str = "defaults"):
        self.mountpoint = mountpoint
        self.options = options

    def resolve(self, input: FormatResult) -> MountResult:
        logger.info(f"MountNode.resolve() — mounting {input.device} at {self.mountpoint}")
        # TODO: mount
        return MountResult(mountpoint=self.mountpoint, device=input.device,
                           options=self.options)

    def mock_resolve(self, input: FormatResult) -> MountResult:
        return MountResult(mountpoint=self.mountpoint, device=input.device,
                           options=self.options)

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True


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


class VarNode(INode[None, VarResult]):
    """Sets a variable/config value. Root node."""

    def __init__(self, name: str, value: str):
        self.name = name
        self.value = value

    def resolve(self, input: None) -> VarResult:
        logger.info(f"VarNode.resolve() — setting {self.name}")
        return VarResult(name=self.name, value=self.value)

    def mock_resolve(self, input: None) -> VarResult:
        return VarResult(name=self.name, value=self.value)

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True


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


class LinkNode(INode[ModuleResult, LinkResult]):
    """Binds outputs from one module as inputs to another."""

    def __init__(self, from_module: str, to_module: str,
                 bindings: Optional[Dict[str, str]] = None):
        self.from_module = from_module
        self.to_module = to_module
        self.bindings = bindings or {}

    def resolve(self, input: ModuleResult) -> LinkResult:
        logger.info(f"LinkNode.resolve() — linking {self.from_module} → {self.to_module}")
        # TODO: resolve bindings, write tfvars, reapply target
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
        # TODO: push xDS config to Envoy
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
