"""MountDir — creates a directory on a mounted filesystem."""

import logging
import os
from dataclasses import asdict, dataclass
from typing import Dict

from zeropoint_agent.inode import INode, ResolveMode, NodeResult
from zeropoint_agent.jobs import JobContext, JobSpec
from zeropoint_agent.nodes.hw.mount import MountResult

logger = logging.getLogger(__name__)


@dataclass
class MountDirResult:
    """Contract for a path/directory node."""
    path: str
    mode: str = "0755"
    is_dir: bool = True


@dataclass
class MountDir(INode[MountResult, MountDirResult]):
    """Creates a directory on a mounted filesystem."""

    path: str
    mode: str = "0755"

    def resolve(self, input: MountResult, mode: ResolveMode) -> NodeResult[MountDirResult]:
        result = MountDirResult(path=self.path, mode=self.mode)
        if mode == ResolveMode.MOCK:
            return NodeResult.success(result)

        try:
            os.makedirs(self.path, mode=int(self.mode, 8), exist_ok=True)
            return NodeResult.success(result)
        except Exception as e:
            return NodeResult.failed(str(e), result)

    def resolve_promise(self, input, mode, context):
        if mode != ResolveMode.LIVE:
            return super().resolve_promise(input, mode, context)
        return context.jobs.submit(
            context,
            JobSpec(
                kind="node.mountdir",
                arguments={
                    "config": {"path": self.path, "mode": self.mode},
                    "input": asdict(input),
                },
                resources=[f"path:{self.path}"],
            ),
        )

    def verify(self, mode: ResolveMode) -> NodeResult[MountDirResult]:
        result = MountDirResult(path=self.path, mode=self.mode)
        if mode == ResolveMode.MOCK:
            return NodeResult.success(result)
        if os.path.isdir(self.path):
            return NodeResult.success(result)
        return NodeResult()

    def remove(self, mode: ResolveMode) -> NodeResult[MountDirResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success()
        # Don't rm -rf from a node
        return NodeResult.success()


def execute_mountdir_job(
    context: JobContext,
    arguments: Dict[str, object],
) -> NodeResult[MountDirResult]:
    context.progress(0.1, {"phase": "create"})
    context.cancellation.raise_if_requested()
    node = MountDir(**dict(arguments["config"]))
    result = node.resolve(
        MountResult(**dict(arguments["input"])),
        ResolveMode.LIVE,
    )
    context.progress(1.0, {"phase": "complete"})
    return result
