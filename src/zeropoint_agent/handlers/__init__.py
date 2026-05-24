"""Shared utilities for API handlers."""

from dataclasses import asdict
from typing import Dict, Any, List, Optional

from pydantic import BaseModel

from zeropoint_agent import (
    ShellScript,
    Network, Docker, NvidiaGpu, AmdGpu, SystemEnvoy,
    Disk, Partition, Format, Mount, MountDir,
    Var, NamespacedVar, OutputVar, DirectoryVar, Namespace,
    Terraform, Endpoint,
)


NODE_REGISTRY = {
    "shell": ShellScript,
    "network": Network,
    "docker": Docker,
    "nvidia": NvidiaGpu,
    "amd": AmdGpu,
    "envoy": SystemEnvoy,
    "disk": Disk,
    "partition": Partition,
    "format": Format,
    "mount": Mount,
    "mountdir": MountDir,
    "var": Var,
    "namespaced": NamespacedVar,
    "output": OutputVar,
    "dir": DirectoryVar,
    "namespace": Namespace,
    "terraform": Terraform,
    "endpoint": Endpoint,
}


class NodeSpec(BaseModel):
    """Specification for a node to add to the graph."""
    id: str
    type: str
    config: Dict[str, Any]
    parents: List[str] = []
    perms: str = "***"


class ResolveRequest(BaseModel):
    """Request to resolve the graph."""
    mode: str = "mock"


def node_to_dict(nid: str, entry, dag=None) -> dict:
    """Convert a node entry to a JSON-friendly dict.

    If `dag` is provided, include `effective_perms` (resolved across
    instance, namespace chain, and type default).
    """
    result = {
        "id": nid,
        "type": type(entry.node).__name__,
        "status": entry.status.value,
        "config": {k: v for k, v in entry.node.__dict__.items()
                   if not k.startswith("_")},
        "parents": entry.parents,
        "error": entry.error,
        "path": entry.path,
        "perms": entry.perms,
    }
    if dag is not None:
        try:
            result["effective_perms"] = dag.effective_perms(nid)
        except Exception:
            result["effective_perms"] = entry.perms
    if entry.output is not None:
        if hasattr(entry.output, "__dataclass_fields__"):
            result["output"] = asdict(entry.output)
        elif isinstance(entry.output, dict):
            # Rehydrated from store as a dict.
            result["output"] = entry.output
    return result
