"""NvidiaGpuNode — detects NVIDIA GPU and driver status."""

import subprocess
from dataclasses import dataclass
from typing import Optional

from zeropoint_agent.inode import INode, ResolveMode, NodeResult


@dataclass
class NvidiaGpuResult:
    detected: bool = False
    driver_loaded: bool = False
    version: Optional[str] = None


def _detect() -> bool:
    try:
        out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5)
        if "NVIDIA" in out.stdout:
            return True
    except Exception:
        pass
    try:
        return subprocess.run(["nvidia-smi"], capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


def _driver_ok() -> bool:
    try:
        return subprocess.run(["nvidia-smi"], capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


def _version() -> Optional[str]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        return out.stdout.strip().split("\n")[0] if out.returncode == 0 else None
    except Exception:
        return None


class NvidiaGpuNode(INode[None, NvidiaGpuResult]):

    def resolve(self, input: None, mode: ResolveMode) -> NodeResult[NvidiaGpuResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(NvidiaGpuResult(True, True, "mock"))
        if not _detect():
            return NodeResult.skipped("No NVIDIA GPU")
        if _driver_ok():
            return NodeResult.success_skip(NvidiaGpuResult(True, True, _version()))
        return NodeResult.success(NvidiaGpuResult(True, False))

    def verify(self, mode: ResolveMode) -> NodeResult[NvidiaGpuResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(NvidiaGpuResult(True, True, "mock"))
        if not _detect():
            return NodeResult.skipped("No NVIDIA GPU")
        if _driver_ok():
            return NodeResult.success_skip(NvidiaGpuResult(True, True, _version()))
        return NodeResult.pending_reboot(NvidiaGpuResult(True, False))

    def remove(self, mode: ResolveMode) -> NodeResult[NvidiaGpuResult]:
        return NodeResult.success()
