"""DriverNode — installs/verifies kernel drivers."""

import logging
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode
from zeropoint_agent.nodes._systemd import systemd_unit, AGENT_BIN

logger = logging.getLogger(__name__)


@dataclass
class DriverResult:
    """Contract for a driver node."""
    driver: str
    version: Optional[str] = None
    loaded: bool = False


class DriverNode(INode[None, DriverResult]):
    """Installs/verifies a driver. Emits systemd unit."""

    def __init__(self, driver: str, version: Optional[str] = None):
        self.driver = driver
        self.version = version

    def resolve(self, input: None) -> DriverResult:
        logger.info(f"DriverNode.resolve() — installing {self.driver}")
        return DriverResult(driver=self.driver, version=self.version)

    def mock_resolve(self, input: None) -> DriverResult:
        return DriverResult(driver=self.driver, version=self.version or "535.104",
                            loaded=True)

    def verify(self) -> bool:
        return False

    def remove(self) -> bool:
        return True

    def systemd_unit(self, node_id: str, parent_ids: list) -> Optional[str]:
        cmd = f"/sbin/modprobe {self.driver}"
        return systemd_unit(
            node_id, f"Load driver {self.driver}",
            parent_ids, exec_start=cmd, exec_verify=cmd,
        )
