"""DriverNode — installs/verifies kernel drivers."""

import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode, ResolveMode, NodeResult, SystemdUnit

logger = logging.getLogger(__name__)


@dataclass
class DriverResult:
    """Contract for a driver node."""
    driver: str
    version: Optional[str] = None
    loaded: bool = False


class DriverNode(INode[None, DriverResult]):
    """Installs/verifies a driver."""

    def __init__(self, driver: str, version: Optional[str] = None):
        self.driver = driver
        self.version = version

    def _detect_hardware(self) -> bool:
        """Check if the hardware this driver supports is present."""
        try:
            if self.driver == "nvidia":
                out = subprocess.run(
                    ["lspci"], capture_output=True, text=True, timeout=5)
                return "NVIDIA" in out.stdout
            # Generic: check if driver module exists
            return True
        except Exception:
            return False

    def _is_loaded(self) -> bool:
        try:
            out = subprocess.run(
                ["lsmod"], capture_output=True, text=True, timeout=5
            )
            return self.driver in out.stdout
        except Exception:
            return False

    def resolve(self, input: None, mode: ResolveMode) -> NodeResult[DriverResult]:
        result = DriverResult(driver=self.driver, version=self.version)
        if mode == ResolveMode.MOCK:
            result.loaded = True
            return NodeResult.success(result)

        # Check if hardware is present
        if not self._detect_hardware():
            return NodeResult.skipped(f"No {self.driver} hardware detected")

        if self._is_loaded():
            result.loaded = True
            return NodeResult.success(result)

        # Driver not loaded — may need install + reboot
        return (NodeResult
            .pending_reboot(result)
            .add_unit(SystemdUnit(
                name=f"zeropoint-driver-{self.driver}",
                description=f"Load driver {self.driver}",
                exec_start=f"/sbin/modprobe {self.driver}",
            )))

    def verify(self, mode: ResolveMode) -> NodeResult[DriverResult]:
        result = DriverResult(driver=self.driver, version=self.version)
        if mode == ResolveMode.MOCK:
            result.loaded = True
            return NodeResult.success(result)

        result.loaded = self._is_loaded()
        if result.loaded:
            return NodeResult.success(result)
        return NodeResult.pending_reboot(result)

    def remove(self, mode: ResolveMode) -> NodeResult[DriverResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success()
        # Could rmmod, but usually don't
        return NodeResult.success()
