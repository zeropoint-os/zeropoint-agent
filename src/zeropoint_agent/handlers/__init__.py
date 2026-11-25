"""Shared utilities for API handlers."""

from dataclasses import asdict
from typing import Dict, Any, List, Optional

from pydantic import BaseModel

from zeropoint_agent import (
    ShellScript,
    SystemDocker, DockerNetwork, NvidiaGpu, AmdGpu, SystemEnvoy, Reboot,
    Disks, HardwareDevice, HardwareInventory,
    Disk, Partition, Format, Mount, MountDir,
    Var, NamespacedVar, OutputVar, DirectoryVar, Namespace,
    Module, Terraform, Exposure, Service,
)


NODE_REGISTRY = {
    "shell": ShellScript,
    "docker": SystemDocker,
    "docker_network": DockerNetwork,
    "nvidia": NvidiaGpu,
    "amd": AmdGpu,
    "envoy": SystemEnvoy,
    "reboot": Reboot,
    "disks": Disks,
    "hardware_device": HardwareDevice,
    "hardware_inventory": HardwareInventory,
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
    "module": Module,
    "terraform": Terraform,
    "exposure": Exposure,
    "service": Service,
}


class NodeSpec(BaseModel):
    """Specification for a node to add to the graph."""
    id: str
    type: str
    config: Dict[str, Any]
    parents: List[str] = []
    perms: str = "***"
    tags: List[str] = []


class ResolveRequest(BaseModel):
    """Request to resolve the graph."""
    mode: Optional[str] = None


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
        "tags": sorted(entry.tags),
        "producer_id": entry.producer_id,
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
