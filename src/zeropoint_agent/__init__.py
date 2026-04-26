"""Zeropoint Agent — graph-based infrastructure language."""

from .inode import INode, ResolveMode, NodeStatus, NodeResult, SystemdUnit
from .dag import DAG

# Node types — re-exported from nodes package
from .nodes import (
    NetworkNode, DockerNode, DriverNode,
    DiskNode, PartitionNode, FormatNode, MountNode, PathNode,
    VarNode,
    ModuleNode, LinkNode, ExposureNode,
)

# Result contracts — re-exported from each node module
from .nodes.system import NetworkResult, DockerResult, DriverResult
from .nodes.hw import DiskResult, PartitionResult, FormatResult, MountResult, PathResult
from .nodes.config import VarResult
from .nodes.user import ModuleResult, LinkResult, ExposureResult

__all__ = [
    "INode", "ResolveMode", "NodeStatus", "NodeResult", "SystemdUnit", "DAG",
    "NetworkNode", "DockerNode", "DriverNode",
    "DiskNode", "PartitionNode", "FormatNode", "MountNode", "PathNode",
    "VarNode",
    "ModuleNode", "LinkNode", "ExposureNode",
    "NetworkResult", "DockerResult", "DriverResult",
    "DiskResult", "PartitionResult", "FormatResult", "MountResult", "PathResult",
    "VarResult",
    "ModuleResult", "LinkResult", "ExposureResult",
]
