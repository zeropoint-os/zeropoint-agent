"""DockerNode — observes Docker daemon state."""

import logging
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode
from zeropoint_agent.nodes.system.network import NetworkResult
from zeropoint_agent.nodes._systemd import systemd_unit, AGENT_BIN

logger = logging.getLogger(__name__)


@dataclass
class DockerResult:
    """Contract for a Docker daemon node."""
    running: bool = False
    version: Optional[str] = None


class DockerNode(INode[NetworkResult, DockerResult]):
    """Observes Docker daemon state. Read-only."""

    def __init__(self):
        pass

    def resolve(self, input: NetworkResult) -> DockerResult:
        logger.info("DockerNode.resolve() — probing Docker")
        return DockerResult()

    def mock_resolve(self, input: NetworkResult) -> DockerResult:
        return DockerResult(running=True, version="24.0.7")

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        return systemd_unit(
            node_id, "Wait for Docker daemon",
            parent_ids,
            exec_start="/usr/bin/docker info",
            exec_verify="/usr/bin/docker info",
        )
