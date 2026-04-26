"""VarNode — sets a variable/config value."""

import logging

from dataclasses import dataclass

from zeropoint_agent.inode import INode, ResolveMode, NodeResult

logger = logging.getLogger(__name__)


@dataclass
class VarResult:
    """Contract for a variable/config node."""
    name: str
    value: str


class VarNode(INode[None, VarResult]):
    """Sets a variable/config value. Root node. In-process."""

    def __init__(self, name: str, value: str):
        self.name = name
        self.value = value

    def resolve(self, input: None, mode: ResolveMode) -> NodeResult[VarResult]:
        result = VarResult(name=self.name, value=self.value)
        if mode == ResolveMode.MOCK:
            return NodeResult.success(result)
        # Live: could write to a config file, env, etc.
        return NodeResult.success(result)

    def verify(self, mode: ResolveMode) -> NodeResult[VarResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(VarResult(name=self.name, value=self.value))
        # Live: check if the value is set correctly
        return NodeResult.success(VarResult(name=self.name, value=self.value))

    def remove(self, mode: ResolveMode) -> NodeResult[VarResult]:
        return NodeResult.success()
