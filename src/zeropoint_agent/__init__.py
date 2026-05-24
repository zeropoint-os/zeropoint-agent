"""Zeropoint Agent — graph-based infrastructure language."""

from .inode import INode, ResolveMode, NodeStatus, NodeResult, SystemdUnit
from .dag import DAG

# Node types — re-exported from nodes package
from .nodes import (
    ShellScript,
    Network, Docker, NvidiaGpu, AmdGpu, SystemEnvoy,
    Disk, Partition, Format, Mount, MountDir,
    Var, NamespacedVar, OutputVar, DirectoryVar, Namespace,
    Terraform, Endpoint,
)

# Result contracts — re-exported from each node module
from .nodes.core import ShellScriptResult
from .nodes.system import NetworkResult, DockerResult, NvidiaGpuResult, AmdGpuResult, SystemEnvoyResult
from .nodes.hw import DiskResult, PartitionResult, FormatResult, MountResult, MountDirResult
from .nodes.config import VarResult, NamespaceResult
from .nodes.user import TerraformResult, EndpointResult

__all__ = [
    "INode", "ResolveMode", "NodeStatus", "NodeResult", "SystemdUnit", "DAG",
    "ShellScript", "ShellScriptResult",
    "Network", "Docker", "NvidiaGpu", "AmdGpu", "SystemEnvoy",
    "Disk", "Partition", "Format", "Mount", "MountDir",
    "Var", "NamespacedVar", "OutputVar", "DirectoryVar", "Namespace",
    "Terraform", "Endpoint",
    "NetworkResult", "DockerResult", "NvidiaGpuResult", "AmdGpuResult",
    "SystemEnvoyResult",
    "DiskResult", "PartitionResult", "FormatResult", "MountResult", "MountDirResult",
    "VarResult", "NamespaceResult",
    "TerraformResult", "EndpointResult",
]
