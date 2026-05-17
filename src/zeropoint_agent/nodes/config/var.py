"""VarNode — a named value.

A VarNode has either:
  - A literal value (no parent)
  - A value passed through from a parent VarNode (no value provided)

The same VarNode class works as a root node (literal value) or a child
node (passthrough from parent).
"""

import logging
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode, ResolveMode, NodeResult

logger = logging.getLogger(__name__)


@dataclass
class VarResult:
    """Contract for a variable node."""
    name: str
    value: str


class VarNode(INode[VarResult, VarResult]):
    """A named value. Literal (root) or passthrough (with parent VarNode).

    - Root: VarNode("MODULE_STORAGE", "/path/to/modules")
    - Passthrough: VarNode("ollama.zp_module_storage") — value from parent
    """

    def __init__(self, name: str, value: Optional[str] = None):
        self.name = name
        self.value = value

    def resolve(self, input: Optional[VarResult], mode: ResolveMode) -> NodeResult[VarResult]:
        # Passthrough: use parent's value
        if input is not None:
            return NodeResult.success(VarResult(name=self.name, value=input.value))
        # Literal: use our own value
        if self.value is None:
            return NodeResult.failed(f"VarNode {self.name} has no value and no parent")
        return NodeResult.success(VarResult(name=self.name, value=self.value))

    def verify(self, mode: ResolveMode) -> NodeResult[VarResult]:
        # Literal: always verified
        if self.value is not None:
            return NodeResult.success(VarResult(name=self.name, value=self.value))
        # Passthrough: not verified, needs resolve to pull from parent
        return NodeResult.pending_reboot(VarResult(name=self.name, value=""))

    def remove(self, mode: ResolveMode) -> NodeResult[VarResult]:
        return NodeResult.success()
