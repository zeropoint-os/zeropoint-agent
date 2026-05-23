"""NvidiaGpu — detects NVIDIA GPU and driver status."""

import subprocess
from dataclasses import dataclass, field
from typing import Optional, List

from zeropoint_agent.inode import INode, ResolveMode, NodeResult


@dataclass
class GpuInfo:
    name: str
    memory_mb: int = 0
    uuid: str = ""


@dataclass
class NvidiaGpuResult:
    detected: bool = False
    driver_loaded: bool = False
    version: Optional[str] = None
    gpus: List[GpuInfo] = field(default_factory=list)


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


def _query() -> tuple:
    """Query driver version and GPU list."""
    version = None
    gpus = []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version,name,memory.total,uuid",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            for line in out.stdout.strip().split("\n"):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    if not version:
                        version = parts[0]
                    gpus.append(GpuInfo(
                        name=parts[1],
                        memory_mb=int(float(parts[2])) if parts[2] else 0,
                        uuid=parts[3],
                    ))
    except Exception:
        pass
    return version, gpus


class NvidiaGpu(INode[None, NvidiaGpuResult]):

    def resolve(self, input: None, mode: ResolveMode) -> NodeResult[NvidiaGpuResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(NvidiaGpuResult(True, True, "mock",
                [GpuInfo("Mock GPU", 8192, "GPU-mock-uuid")]))
        if not _detect():
            return NodeResult.skipped("No NVIDIA GPU")
        if _driver_ok():
            version, gpus = _query()
            return NodeResult.success_skip(NvidiaGpuResult(True, True, version, gpus))
        return NodeResult.success(NvidiaGpuResult(True, False))

    def verify(self, mode: ResolveMode) -> NodeResult[NvidiaGpuResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(NvidiaGpuResult(True, True, "mock",
                [GpuInfo("Mock GPU", 8192, "GPU-mock-uuid")]))
        if not _detect():
            return NodeResult.skipped("No NVIDIA GPU")
        if _driver_ok():
            version, gpus = _query()
            return NodeResult.success_skip(NvidiaGpuResult(True, True, version, gpus))
        return NodeResult.pending_reboot(NvidiaGpuResult(True, False))

    def remove(self, mode: ResolveMode) -> NodeResult[NvidiaGpuResult]:
        return NodeResult.success()
