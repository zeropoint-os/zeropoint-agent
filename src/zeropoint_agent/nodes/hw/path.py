"""PathNode — creates a directory on a mounted filesystem."""

import logging
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode
from zeropoint_agent.nodes._systemd import systemd_unit, AGENT_BIN
from zeropoint_agent.nodes.hw.mount import MountResult

logger = logging.getLogger(__name__)


@dataclass
class PathResult:
    """Contract for a path/directory node."""
    path: str
    mode: str = "0755"
    is_dir: bool = True


class PathNode(INode[MountResult, PathResult]):
    """Creates a directory on a mounted filesystem."""

    def __init__(self, path: str, mode: str = "0755"):
        self.path = path
        self.mode = mode

    def resolve(self, input: MountResult) -> PathResult:
        logger.info(f"PathNode.resolve() — mkdir {self.path}")
        return PathResult(path=self.path, mode=self.mode)

    def mock_resolve(self, input: MountResult) -> PathResult:
        return PathResult(path=self.path, mode=self.mode)

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        return systemd_unit(
            node_id, f"Create directory {self.path}",
            parent_ids,
            exec_start=f"/bin/mkdir -p {self.path}",
            exec_verify=f"/usr/bin/test -d {self.path}",
        )
