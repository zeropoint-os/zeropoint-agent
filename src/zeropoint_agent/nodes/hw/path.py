"""PathNode — creates a directory on a mounted filesystem."""

import logging
import os
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode, ResolveMode, NodeResult
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

    def resolve(self, input: MountResult, mode: ResolveMode) -> NodeResult[PathResult]:
        result = PathResult(path=self.path, mode=self.mode)
        if mode == ResolveMode.MOCK:
            return NodeResult.success(result)

        try:
            os.makedirs(self.path, mode=int(self.mode, 8), exist_ok=True)
            return NodeResult.success(result)
        except Exception as e:
            return NodeResult.failed(str(e), result)

    def verify(self, mode: ResolveMode) -> NodeResult[PathResult]:
        result = PathResult(path=self.path, mode=self.mode)
        if mode == ResolveMode.MOCK:
            return NodeResult.success(result)
        if os.path.isdir(self.path):
            return NodeResult.success(result)
        return NodeResult.pending_reboot(result)

    def remove(self, mode: ResolveMode) -> NodeResult[PathResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success()
        # Don't rm -rf from a node
        return NodeResult.success()
