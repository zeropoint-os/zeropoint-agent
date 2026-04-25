"""Concrete node implementations — the vocabulary of the language.

Each node is a typed I → O transform. The node's constructor fields
are the desired state. resolve() produces the runtime output.
verify() probes reality against desired. remove() tears down.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

from zeropoint_agent.inode import INode
from zeropoint_agent.entities import (
    DiskResult, PartitionResult, FormatResult, MountResult,
    PathResult, VarResult, ModuleResult, LinkResult, ExposureResult,
)

logger = logging.getLogger(__name__)


# --- Root node (no input) ---

class DiskNode(INode[None, DiskResult]):
    """Discovers/registers a disk. Root node — no parent input."""

    def __init__(self, device: str):
        self.device = device

    def resolve(self, input: None) -> DiskResult:
        logger.info(f"DiskNode.resolve() — discovering {self.device}")
        # TODO: probe via HWProbe
        return DiskResult(device=self.device)

    def verify(self) -> bool:
        # TODO: check device exists
        return False

    def remove(self) -> bool:
        logger.info(f"DiskNode.remove() — unmanaging {self.device}")
        return True


# --- Infra chain: Disk → Partition → Format → Mount → Path ---

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
            number=self.number,
            size_mb=self.size_mb,
            device=f"{input.device}{self.number}",
            type=self.type,
        )

    def verify(self) -> bool:
        # TODO: probe partition table
        return False

    def remove(self) -> bool:
        logger.info(f"PartitionNode.remove() — deleting partition {self.number}")
        return True


class FormatNode(INode[PartitionResult, FormatResult]):
    """Creates a filesystem on a parent partition."""

    def __init__(self, filesystem: str = "ext4", label: Optional[str] = None):
        self.filesystem = filesystem
        self.label = label

    def resolve(self, input: PartitionResult) -> FormatResult:
        logger.info(f"FormatNode.resolve() — mkfs.{self.filesystem} on {input.device}")
        # TODO: mkfs
        return FormatResult(
            filesystem=self.filesystem,
            device=input.device,
            label=self.label,
        )

    def verify(self) -> bool:
        # TODO: blkid to check filesystem
        return False

    def remove(self) -> bool:
        logger.info(f"FormatNode.remove() — wiping filesystem")
        return True


class MountNode(INode[FormatResult, MountResult]):
    """Mounts a formatted partition at a mountpoint."""

    def __init__(self, mountpoint: str, options: str = "defaults"):
        self.mountpoint = mountpoint
        self.options = options

    def resolve(self, input: FormatResult) -> MountResult:
        logger.info(f"MountNode.resolve() — mounting {input.device} at {self.mountpoint}")
        # TODO: mount
        return MountResult(
            mountpoint=self.mountpoint,
            device=input.device,
            options=self.options,
        )

    def verify(self) -> bool:
        # TODO: check /proc/mounts
        return False

    def remove(self) -> bool:
        logger.info(f"MountNode.remove() — unmounting {self.mountpoint}")
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

    def verify(self) -> bool:
        # TODO: os.path.exists + stat
        return False

    def remove(self) -> bool:
        logger.info(f"PathNode.remove() — removing {self.path}")
        return True


# --- Config / Variables ---

class VarNode(INode[None, VarResult]):
    """Sets a variable/config value. Root node — no parent input."""

    def __init__(self, name: str, value: str):
        self.name = name
        self.value = value

    def resolve(self, input: None) -> VarResult:
        logger.info(f"VarNode.resolve() — setting {self.name}")
        return VarResult(name=self.name, value=self.value)

    def verify(self) -> bool:
        # TODO: check config file / env
        return False

    def remove(self) -> bool:
        logger.info(f"VarNode.remove() — unsetting {self.name}")
        return True


# --- Module (Terraform-managed container) ---

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
        return ModuleResult(
            source=self.source,
            module_id=self.module_id,
            variables=self.variables,
        )

    def verify(self) -> bool:
        # TODO: terraform plan -detailed-exitcode
        return False

    def remove(self) -> bool:
        logger.info(f"ModuleNode.remove() — terraform destroy {self.module_id}")
        return True


# --- Link (inter-module variable binding) ---

class LinkNode(INode[ModuleResult, LinkResult]):
    """Binds outputs from one module as inputs to another."""

    def __init__(self, from_module: str, to_module: str,
                 bindings: Optional[Dict[str, str]] = None):
        self.from_module = from_module
        self.to_module = to_module
        self.bindings = bindings or {}

    def resolve(self, input: ModuleResult) -> LinkResult:
        logger.info(f"LinkNode.resolve() — linking {self.from_module} → {self.to_module}")
        # TODO: resolve bindings from input.outputs, write tfvars, reapply target
        return LinkResult(
            from_module=self.from_module,
            to_module=self.to_module,
            bindings=self.bindings,
        )

    def verify(self) -> bool:
        # TODO: check bindings are current
        return False

    def remove(self) -> bool:
        logger.info(f"LinkNode.remove() — unlinking {self.from_module} → {self.to_module}")
        return True


# --- Exposure (Envoy xDS route) ---

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
        return ExposureResult(
            module_id=self.module_id,
            port=self.port,
            protocol=self.protocol,
            path_prefix=self.path_prefix,
            description=self.description,
        )

    def verify(self) -> bool:
        # TODO: check Envoy route exists
        return False

    def remove(self) -> bool:
        logger.info(f"ExposureNode.remove() — unexposing {self.module_id}:{self.port}")
        return True
