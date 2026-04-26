"""Shared utilities for API handlers."""

from dataclasses import asdict
from typing import Dict, Any, List, Optional

from pydantic import BaseModel

from zeropoint_agent import (
    NetworkNode, DockerNode, DriverNode,
    DiskNode, PartitionNode, FormatNode, MountNode,
    PathNode, VarNode, ModuleNode, LinkNode, ExposureNode,
)


NODE_REGISTRY = {
    "network": NetworkNode,
    "docker": DockerNode,
    "driver": DriverNode,
    "disk": DiskNode,
    "partition": PartitionNode,
    "format": FormatNode,
    "mount": MountNode,
    "path": PathNode,
    "var": VarNode,
    "module": ModuleNode,
    "link": LinkNode,
    "exposure": ExposureNode,
}


class NodeSpec(BaseModel):
    """Specification for a node to add to the graph."""
    id: str
    type: str
    config: Dict[str, Any]
    parents: List[str] = []


class GraphBuildRequest(BaseModel):
    """Build a complete graph in one call."""
    nodes: List[NodeSpec]


class ResolveRequest(BaseModel):
    """Request to resolve the graph."""
    mode: str = "mock"


def node_to_dict(nid: str, entry) -> dict:
    """Convert a node entry to a JSON-friendly dict."""
    result = {
        "id": nid,
        "type": type(entry.node).__name__,
        "status": entry.status.value,
        "config": {k: v for k, v in entry.node.__dict__.items()
                   if not k.startswith("_")},
        "parents": entry.parents,
        "error": entry.error,
    }
    if entry.output and hasattr(entry.output, "__dataclass_fields__"):
        result["output"] = asdict(entry.output)
    return result
