"""DriverNode — detects, installs, and verifies kernel drivers.

One node handles the full lifecycle:
  1. Detect hardware (lspci) — SKIPPED if not present
  2. Check if driver loaded (lsmod) — SUCCESS if yes
  3. Install driver (apt/modprobe) — PENDING_REBOOT if needs reboot
  4. Verify (nvidia-smi etc.) — SUCCESS or PENDING_REBOOT
"""

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
    detected: bool = False
    installed: bool = False
    loaded: bool = False
    version: Optional[str] = None


class DriverNode(INode[None, DriverResult]):
    """Detects, installs, and verifies a kernel driver."""

    def __init__(self, driver: str, version: Optional[str] = None):
        self.driver = driver
        self.version = version

    def _detect_hardware(self) -> bool:
        """Check if the hardware requiring this driver is present."""
        if self.driver == "nvidia":
            # Check lspci first (bare metal)
            try:
                out = subprocess.run(
                    ["lspci"], capture_output=True, text=True, timeout=5)
                if "NVIDIA" in out.stdout:
                    return True
            except Exception:
                pass
            # Fallback: check nvidia-smi (GPU passthrough / containers)
            try:
                out = subprocess.run(
                    ["nvidia-smi"], capture_output=True, text=True, timeout=5)
                return out.returncode == 0
            except Exception:
                return False
        return True

    def _is_loaded(self) -> bool:
        """Check if the driver is functional."""
        try:
            if self.driver == "nvidia":
                # In containers with GPU passthrough, lsmod won't show nvidia
                # nvidia-smi is the definitive check
                out = subprocess.run(
                    ["nvidia-smi"], capture_output=True, text=True, timeout=5)
                return out.returncode == 0
            out = subprocess.run(
                ["lsmod"], capture_output=True, text=True, timeout=5)
            return self.driver in out.stdout
        except Exception:
            return False

    def _get_version(self) -> Optional[str]:
        """Get the driver version."""
        try:
            if self.driver == "nvidia":
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=driver_version",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5)
                if out.returncode == 0:
                    return out.stdout.strip().split("\n")[0]
            return None
        except Exception:
            return None

    def _full_probe(self) -> DriverResult:
        """Full probe: detect, check loaded, get version."""
        result = DriverResult(driver=self.driver)
        result.detected = self._detect_hardware()
        if not result.detected:
            return result
        result.loaded = self._is_loaded()
        if result.loaded:
            result.installed = True
            result.version = self._get_version()
        return result

    def resolve(self, input: None, mode: ResolveMode) -> NodeResult[DriverResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(DriverResult(
                driver=self.driver, detected=True, installed=True,
                loaded=True, version=self.version or "mock"))

        result = self._full_probe()

        # No hardware → skip this node and all children
        if not result.detected:
            return NodeResult.skipped(f"No {self.driver} hardware detected")

        # Already loaded → success
        if result.loaded:
            logger.info(f"Driver {self.driver} already loaded (version={result.version})")
            return NodeResult.success(result)

        # Hardware present but driver not loaded → needs install or modprobe
        logger.info(f"Driver {self.driver}: hardware detected but driver not loaded")
        return (NodeResult
            .pending_reboot(result)
            .add_unit(SystemdUnit(
                name=f"zeropoint-driver-{self.driver}",
                description=f"Load {self.driver} driver",
                exec_start=f"/sbin/modprobe {self.driver}",
                timeout_sec=60,
            )))

    def verify(self, mode: ResolveMode) -> NodeResult[DriverResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(DriverResult(
                driver=self.driver, detected=True, installed=True,
                loaded=True, version="mock"))

        result = self._full_probe()

        if not result.detected:
            return NodeResult.skipped(f"No {self.driver} hardware detected")

        if result.loaded:
            return NodeResult.success(result)

        return NodeResult.pending_reboot(result)

    def remove(self, mode: ResolveMode) -> NodeResult[DriverResult]:
        return NodeResult.success()
