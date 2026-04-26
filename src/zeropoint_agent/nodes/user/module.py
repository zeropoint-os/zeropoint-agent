"""ModuleNode — manages a containerized module via Terraform."""

import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

from zeropoint_agent.inode import INode
from zeropoint_agent.nodes.hw.path import PathResult

logger = logging.getLogger(__name__)


@dataclass
class ModuleResult:
    """Contract for a module node (Terraform-managed container)."""
    source: str
    module_id: str
    variables: Dict[str, Any] = field(default_factory=dict)
    container_id: Optional[str] = None
    container_ip: Optional[str] = None
    ports: Dict[str, int] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)


class ModuleNode(INode[PathResult, ModuleResult]):
    """Manages a containerized module via Terraform. In-process."""

    def __init__(self, source: str, module_id: str,
                 variables: Optional[Dict[str, Any]] = None):
        self.source = source
        self.module_id = module_id
        self.variables = variables or {}

    def resolve(self, input: PathResult) -> ModuleResult:
        logger.info(f"ModuleNode.resolve() — terraform apply {self.module_id}")
        return ModuleResult(source=self.source, module_id=self.module_id,
                            variables=self.variables)

    def mock_resolve(self, input: PathResult) -> ModuleResult:
        return ModuleResult(
            source=self.source, module_id=self.module_id,
            variables=self.variables,
            container_id="mock-container-abc123",
            container_ip="172.17.0.5",
            ports={"11434": 11434},
        )

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True
