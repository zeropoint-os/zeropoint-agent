"""Zeropoint Agent — graph-based infrastructure language."""

from .inode import INode, ResolveMode, NodeStatus, NodeResult, SystemdUnit
from .dag import DAG

# Node types — re-exported from nodes package
from .nodes import (
    ShellScript,
    Network, Docker, NvidiaGpu, AmdGpu,
    Disk, Partition, Format, Mount, MountDir,
    Var, NamespacedVar, OutputVar, DirectoryVar, Namespace,
    Terraform, Link, Exposure,
)

# Result contracts — re-exported from each node module
from .nodes.core import ShellScriptResult
from .nodes.system import NetworkResult, DockerResult, NvidiaGpuResult, AmdGpuResult
from .nodes.hw import DiskResult, PartitionResult, FormatResult, MountResult, MountDirResult
from .nodes.config import VarResult, NamespaceResult
from .nodes.user import TerraformResult, LinkResult, ExposureResult

__all__ = [
    "INode", "ResolveMode", "NodeStatus", "NodeResult", "SystemdUnit", "DAG",
    "ShellScript", "ShellScriptResult",
    "Network", "Docker", "NvidiaGpu", "AmdGpu",
    "Disk", "Partition", "Format", "Mount", "MountDir",
    "Var", "NamespacedVar", "OutputVar", "DirectoryVar", "Namespace",
    "Terraform", "Link", "Exposure",
    "NetworkResult", "DockerResult", "NvidiaGpuResult", "AmdGpuResult",
    "DiskResult", "PartitionResult", "FormatResult", "MountResult", "MountDirResult",
    "VarResult", "NamespaceResult",
    "TerraformResult", "LinkResult", "ExposureResult",
]
