"""VarNode — sets a variable/config value."""

import logging
from dataclasses import dataclass

from zeropoint_agent.inode import INode

logger = logging.getLogger(__name__)


@dataclass
class VarResult:
    """Contract for a variable/config node."""
    name: str
    value: str


class VarNode(INode[None, VarResult]):
    """Sets a variable/config value. Root node. In-process, no systemd unit."""

    def __init__(self, name: str, value: str):
        self.name = name
        self.value = value

    def resolve(self, input: None) -> VarResult:
        return VarResult(name=self.name, value=self.value)

    def mock_resolve(self, input: None) -> VarResult:
        return VarResult(name=self.name, value=self.value)

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True
