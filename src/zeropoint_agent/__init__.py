"""Zeropoint Agent — graph-based infrastructure language."""

from .inode import INode, ResolveMode, NodeStatus, NodeResult, SystemdUnit
from .dag import DAG

# Node types — re-exported from nodes package
from .nodes import (
    ShellScriptNode,
    NetworkNode, DockerNode, NvidiaGpuNode, AmdGpuNode,
    DiskNode, PartitionNode, FormatNode, MountNode, MountDir,
    VarNode, DirectoryVar, NamespaceNode,
    ModuleNode, TerraformNode, LinkNode, ExposureNode,
)

# Result contracts — re-exported from each node module
from .nodes.core import ShellScriptResult
from .nodes.system import NetworkResult, DockerResult, NvidiaGpuResult, AmdGpuResult
from .nodes.hw import DiskResult, PartitionResult, FormatResult, MountResult, MountDirResult
from .nodes.config import VarResult, NamespaceResult
from .nodes.user import ModuleResult, TerraformResult, LinkResult, ExposureResult

__all__ = [
    "INode", "ResolveMode", "NodeStatus", "NodeResult", "SystemdUnit", "DAG",
    "ShellScriptNode", "ShellScriptResult",
    "NetworkNode", "DockerNode", "NvidiaGpuNode", "AmdGpuNode",
    "DiskNode", "PartitionNode", "FormatNode", "MountNode", "MountDir",
    "VarNode", "DirectoryVar", "NamespaceNode",
    "ModuleNode", "TerraformNode", "LinkNode", "ExposureNode",
    "NetworkResult", "DockerResult", "NvidiaGpuResult", "AmdGpuResult",
    "DiskResult", "PartitionResult", "FormatResult", "MountResult", "MountDirResult",
    "VarResult", "NamespaceResult",
    "ModuleResult", "TerraformResult", "LinkResult", "ExposureResult",
]
