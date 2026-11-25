"""Detection endpoints — small helpers for boot scripts.

Used by `zeropoint-agent detect <kind>` and by bash bootstrap scripts to
populate system Vars with host-derived values (arch, GPU vendor).

Detection logic lives here so the agent and any boot script agree on
the same probes.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/detect", tags=["detect"])


def detect_arch() -> str:
    """Normalized CPU architecture name (amd64 / arm64 / raw machine name)."""
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "amd64"
    if m in ("aarch64", "arm64"):
        return "arm64"
    return m


def detect_gpu_vendor() -> str:
    """Best-effort GPU vendor detection (nvidia / amd / empty)."""
    # nvidia-smi is the cheap, definitive probe when the toolkit is installed.
    if shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(["nvidia-smi", "-L"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return "nvidia"
        except Exception:
            pass
    # Device node fallbacks (work inside containers without lspci).
    if any(Path("/dev").glob("nvidia*")):
        return "nvidia"
    if Path("/dev/kfd").exists():
        return "amd"
    # Last resort: lspci.
    if shutil.which("lspci"):
        try:
            out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5)
            if out.returncode == 0:
                text = out.stdout.lower()
                if "nvidia" in text:
                    return "nvidia"
                if "amd/ati" in text or "advanced micro devices" in text:
                    return "amd"
        except Exception:
            pass
    return ""


_DETECTORS = {
    "arch": detect_arch,
    "gpu-vendor": detect_gpu_vendor,
    "gpu_vendor": detect_gpu_vendor,
}


@router.get("/{kind}")
async def detect(kind: str):
    """Return a host-derived value for the requested probe.

    Supported kinds:
      - `arch`       → CPU arch (amd64, arm64, …)
      - `gpu-vendor` → nvidia / amd / "" if none
    """
    fn = _DETECTORS.get(kind)
    if fn is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown detector {kind!r}; supported: "
                   f"{sorted(set(_DETECTORS))}")
    return {"kind": kind, "value": fn()}
