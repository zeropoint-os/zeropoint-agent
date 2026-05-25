"""Zeropoint Agent — graph-based infrastructure language."""

from .inode import INode, ResolveMode, NodeStatus, NodeResult, SystemdUnit
from .dag import DAG

# Node types — re-exported from nodes package
from .nodes import (
    ShellScript,
    SystemDocker, DockerNetwork, NvidiaGpu, AmdGpu, SystemEnvoy,
    Disk, Partition, Format, Mount, MountDir,
    Var, NamespacedVar, OutputVar, DirectoryVar, Namespace,
    Terraform, Exposure, Service,
)

# Result contracts — re-exported from each node module
from .nodes.core import ShellScriptResult
from .nodes.system import (
    SystemDockerResult, DockerNetworkResult,
    NvidiaGpuResult, AmdGpuResult, SystemEnvoyResult,
)
from .nodes.hw import DiskResult, PartitionResult, FormatResult, MountResult, MountDirResult
from .nodes.config import VarResult, NamespaceResult
from .nodes.user import TerraformResult, ExposureResult, ServiceResult

__all__ = [
    "INode", "ResolveMode", "NodeStatus", "NodeResult", "SystemdUnit", "DAG",
    "ShellScript", "ShellScriptResult",
    "SystemDocker", "SystemDockerResult",
    "DockerNetwork", "DockerNetworkResult",
    "NvidiaGpu", "AmdGpu", "SystemEnvoy",
    "Disk", "Partition", "Format", "Mount", "MountDir",
    "Var", "NamespacedVar", "OutputVar", "DirectoryVar", "Namespace",
    "Terraform", "Exposure", "Service",
    "NvidiaGpuResult", "AmdGpuResult", "SystemEnvoyResult",
    "DiskResult", "PartitionResult", "FormatResult", "MountResult", "MountDirResult",
    "VarResult", "NamespaceResult",
    "TerraformResult", "ExposureResult", "ServiceResult",
]
