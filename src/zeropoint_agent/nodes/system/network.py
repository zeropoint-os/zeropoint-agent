"""NetworkNode — observes network interface state."""

import logging
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode
from zeropoint_agent.nodes._systemd import systemd_unit, AGENT_BIN

logger = logging.getLogger(__name__)


@dataclass
class NetworkResult:
    """Contract for a network interface node."""
    interface: str
    ip: Optional[str] = None
    up: bool = False


class NetworkNode(INode[None, NetworkResult]):
    """Observes network interface state. Read-only."""

    def __init__(self, interface: str = "eth0"):
        self.interface = interface

    def resolve(self, input: None) -> NetworkResult:
        logger.info(f"NetworkNode.resolve() — probing {self.interface}")
        return NetworkResult(interface=self.interface)

    def mock_resolve(self, input: None) -> NetworkResult:
        return NetworkResult(interface=self.interface, ip="192.168.1.10", up=True)

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        return systemd_unit(
            node_id, f"Wait for network interface {self.interface}",
            parent_ids,
            exec_start=f"/usr/bin/ip link show {self.interface} up",
            exec_verify=f"/usr/bin/ip link show {self.interface}",
        )
