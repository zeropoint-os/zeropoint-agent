"""Zeropoint Agent — graph-based infrastructure language."""

from .inode import INode, ResolveMode, NodeStatus, NodeResult, SystemdUnit
from .dag import DAG
from .promise import Promise, PromiseStatus, ResultEnvelope
from .jobs import JobSpec, ResolveContext

# Node types — re-exported from nodes package
from .nodes import (
    ShellScript,
    SystemDocker, DockerNetwork, NvidiaGpu, AmdGpu, SystemEnvoy, Reboot,
    Disks, HardwareDevice, HardwareInventory,
    Disk, Partition, Format, Mount, MountDir,
    Var, NamespacedVar, OutputVar, DirectoryVar, Namespace,
    Module, Terraform, Exposure, Service,
)

# Result contracts — re-exported from each node module
from .nodes.core import ShellScriptResult
from .nodes.system import (
    SystemDockerResult, DockerNetworkResult,
    NvidiaGpuResult, AmdGpuResult, SystemEnvoyResult, RebootResult,
)
from .nodes.hw import (
    DisksResult,
    HardwareDeviceResult,
    HardwareInventoryResult,
    DiskResult,
    PartitionResult,
    FormatResult,
    MountResult,
    MountDirResult,
)
from .nodes.config import VarResult, NamespaceResult
from .nodes.user import TerraformResult, ExposureResult, ServiceResult

__all__ = [
    "INode", "ResolveMode", "NodeStatus", "NodeResult", "SystemdUnit", "DAG",
    "Promise", "PromiseStatus", "ResultEnvelope", "JobSpec", "ResolveContext",
    "ShellScript", "ShellScriptResult",
    "SystemDocker", "SystemDockerResult",
    "DockerNetwork", "DockerNetworkResult",
    "NvidiaGpu", "AmdGpu", "SystemEnvoy", "Reboot",
    "Disks", "HardwareDevice", "HardwareInventory",
    "Disk", "Partition", "Format", "Mount", "MountDir",
    "Var", "NamespacedVar", "OutputVar", "DirectoryVar", "Namespace",
    "Module", "Terraform", "Exposure", "Service",
    "NvidiaGpuResult", "AmdGpuResult", "SystemEnvoyResult", "RebootResult",
    "DisksResult", "HardwareDeviceResult", "HardwareInventoryResult",
    "DiskResult", "PartitionResult", "FormatResult",
    "MountResult", "MountDirResult",
    "VarResult", "NamespaceResult",
    "TerraformResult", "ExposureResult", "ServiceResult",
]
