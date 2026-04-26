"""LinkNode — binds outputs from one module as inputs to another."""

import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

from zeropoint_agent.inode import INode
from zeropoint_agent.nodes.user.module import ModuleResult

logger = logging.getLogger(__name__)


@dataclass
class LinkResult:
    """Contract for a link node (inter-module variable binding)."""
    from_module: str
    to_module: str
    bindings: Dict[str, str] = field(default_factory=dict)
    resolved_bindings: Dict[str, Any] = field(default_factory=dict)


class LinkNode(INode[ModuleResult, LinkResult]):
    """Binds outputs from one module as inputs to another. In-process."""

    def __init__(self, from_module: str, to_module: str,
                 bindings: Optional[Dict[str, str]] = None):
        self.from_module = from_module
        self.to_module = to_module
        self.bindings = bindings or {}

    def resolve(self, input: ModuleResult) -> LinkResult:
        logger.info(f"LinkNode.resolve() — linking {self.from_module} → {self.to_module}")
        return LinkResult(from_module=self.from_module, to_module=self.to_module,
                          bindings=self.bindings)

    def mock_resolve(self, input: ModuleResult) -> LinkResult:
        resolved = {k: f"mock-{v}" for k, v in self.bindings.items()}
        return LinkResult(from_module=self.from_module, to_module=self.to_module,
                          bindings=self.bindings, resolved_bindings=resolved)

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True
