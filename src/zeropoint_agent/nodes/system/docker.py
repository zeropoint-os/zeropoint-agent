"""Docker — observes Docker daemon state."""

import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode, ResolveMode, NodeResult
from zeropoint_agent.nodes.system.network import NetworkResult

logger = logging.getLogger(__name__)


@dataclass
class DockerResult:
    """Contract for a Docker daemon node."""
    running: bool = False
    version: Optional[str] = None


class Docker(INode[NetworkResult, DockerResult]):
    """Observes Docker daemon state. Read-only."""

    def __init__(self):
        pass

    def _probe(self) -> DockerResult:
        try:
            out = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True, text=True, timeout=10
            )
            if out.returncode == 0:
                return DockerResult(running=True, version=out.stdout.strip())
            return DockerResult(running=False)
        except Exception as e:
            logger.debug(f"Docker probe failed: {e}")
            return DockerResult(running=False)

    def resolve(self, input: NetworkResult, mode: ResolveMode) -> NodeResult[DockerResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(DockerResult(running=True, version="24.0.7"))

        result = self._probe()
        if result.running:
            return NodeResult.success(result)
        return NodeResult.failed("Docker daemon not running", result)

    def verify(self, mode: ResolveMode) -> NodeResult[DockerResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(DockerResult(running=True, version="24.0.7"))

        result = self._probe()
        if result.running:
            return NodeResult.success(result)
        return NodeResult.pending_reboot(result)

    def remove(self, mode: ResolveMode) -> NodeResult[DockerResult]:
        return NodeResult.success()
