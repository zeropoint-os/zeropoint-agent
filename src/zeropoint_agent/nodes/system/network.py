"""NetworkNode — observes network interface state."""

import json
import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode, ResolveMode, NodeResult

logger = logging.getLogger(__name__)


@dataclass
class NetworkResult:
    """Contract for a network interface node."""
    interface: str
    ip: Optional[str] = None
    up: bool = False


class NetworkNode(INode[None, NetworkResult]):
    """Observes network interface state. Read-only root node."""

    def __init__(self, interface: str = "eth0"):
        self.interface = interface

    def _probe(self) -> NetworkResult:
        """Probe the real interface state."""
        try:
            out = subprocess.run(
                ["ip", "-j", "addr", "show", self.interface],
                capture_output=True, text=True, timeout=5
            )
            if out.returncode != 0:
                return NetworkResult(interface=self.interface, up=False)

            data = json.loads(out.stdout)
            if not data:
                return NetworkResult(interface=self.interface, up=False)

            iface = data[0]
            up = "UP" in iface.get("flags", [])
            ip = None
            for addr in iface.get("addr_info", []):
                if addr.get("family") == "inet":
                    ip = addr.get("local")
                    break

            return NetworkResult(interface=self.interface, ip=ip, up=up)
        except Exception as e:
            logger.debug(f"Failed to probe {self.interface}: {e}")
            return NetworkResult(interface=self.interface, up=False)

    def resolve(self, input: None, mode: ResolveMode) -> NodeResult[NetworkResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(
                NetworkResult(interface=self.interface, ip="192.168.1.10", up=True))

        result = self._probe()
        if result.up:
            return NodeResult.success(result)
        return NodeResult.failed(f"Interface {self.interface} is down", result)

    def verify(self, mode: ResolveMode) -> NodeResult[NetworkResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(
                NetworkResult(interface=self.interface, ip="192.168.1.10", up=True))

        result = self._probe()
        if result.up:
            return NodeResult.success(result)
        return NodeResult.pending_reboot(result)

    def remove(self, mode: ResolveMode) -> NodeResult[NetworkResult]:
        return NodeResult.success()
