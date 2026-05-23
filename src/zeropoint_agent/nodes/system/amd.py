"""AmdGpu — detects AMD GPU and ROCm driver status."""

import subprocess
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode, ResolveMode, NodeResult


@dataclass
class AmdGpuResult:
    detected: bool = False
    driver_loaded: bool = False
    version: Optional[str] = None


def _detect() -> bool:
    try:
        out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5)
        return "AMD" in out.stdout and ("VGA" in out.stdout or "Display" in out.stdout)
    except Exception:
        return False


def _driver_ok() -> bool:
    try:
        return subprocess.run(["rocm-smi"], capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


def _version() -> Optional[str]:
    try:
        out = subprocess.run(["rocm-smi", "--showdriverversion"],
                             capture_output=True, text=True, timeout=5)
        for line in out.stdout.split("\n"):
            if "Driver" in line and ":" in line:
                return line.split(":")[-1].strip()
    except Exception:
        pass
    return None


class AmdGpu(INode[None, AmdGpuResult]):

    def resolve(self, input: None, mode: ResolveMode) -> NodeResult[AmdGpuResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(AmdGpuResult(True, True, "mock"))
        if not _detect():
            return NodeResult.skipped("No AMD GPU")
        if _driver_ok():
            return NodeResult.success_skip(AmdGpuResult(True, True, _version()))
        return NodeResult.success(AmdGpuResult(True, False))

    def verify(self, mode: ResolveMode) -> NodeResult[AmdGpuResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(AmdGpuResult(True, True, "mock"))
        if not _detect():
            return NodeResult.skipped("No AMD GPU")
        if _driver_ok():
            return NodeResult.success_skip(AmdGpuResult(True, True, _version()))
        return NodeResult.pending_reboot(AmdGpuResult(True, False))

    def remove(self, mode: ResolveMode) -> NodeResult[AmdGpuResult]:
        return NodeResult.success()
