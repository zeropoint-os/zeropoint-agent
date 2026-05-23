"""Link — binds outputs from one module as inputs to another."""

import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

from zeropoint_agent.inode import INode, ResolveMode, NodeResult
from zeropoint_agent.nodes.user.module import TerraformResult

logger = logging.getLogger(__name__)


@dataclass
class LinkResult:
    """Contract for a link node (inter-module variable binding)."""
    from_module: str
    to_module: str
    bindings: Dict[str, str] = field(default_factory=dict)
    resolved_bindings: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Link(INode[TerraformResult, LinkResult]):
    """Binds outputs from one module as inputs to another. In-process."""

    from_module: str
    to_module: str
    bindings: Dict[str, str] = field(default_factory=dict)

    def resolve(self, input: TerraformResult, mode: ResolveMode) -> NodeResult[LinkResult]:
        result = LinkResult(from_module=self.from_module, to_module=self.to_module,
                            bindings=self.bindings)
        if mode == ResolveMode.MOCK:
            result.resolved_bindings = {k: f"mock-{v}" for k, v in self.bindings.items()}
            return NodeResult.success(result)
        # TODO: resolve bindings, write tfvars, reapply
        return NodeResult.pending_reboot(result)

    def verify(self, mode: ResolveMode) -> NodeResult[LinkResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(LinkResult(from_module=self.from_module,
                                                  to_module=self.to_module))
        return NodeResult.pending_reboot()

    def remove(self, mode: ResolveMode) -> NodeResult[LinkResult]:
        return NodeResult.success()
