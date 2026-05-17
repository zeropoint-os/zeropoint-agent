"""Bootstrap — ensures core nodes exist in the graph on startup.

Runs every time the agent starts. Idempotent — dag.add() skips
existing nodes. All nodes are always added — resolve handles
skipping (e.g. DriverNode returns SKIPPED if no GPU, children
auto-skip).

This IS the boot process. There's no separate boot command.
"""

import os
import logging
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from zeropoint_agent.dag import DAG
from zeropoint_agent.inode import ResolveMode
from zeropoint_agent.nodes.system.network import NetworkNode
from zeropoint_agent.nodes.system.docker import DockerNode
from zeropoint_agent.nodes.system.nvidia import NvidiaGpuNode
from zeropoint_agent.nodes.system.amd import AmdGpuNode
from zeropoint_agent.nodes.config.var import VarNode
from zeropoint_agent.nodes.core.shell_script import ShellScriptNode

logger = logging.getLogger(__name__)


def _detect_arch() -> str:
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "amd64"
    if m in ("aarch64", "arm64"):
        return "arm64"
    return m


def _detect_gpu_vendor() -> str:
    # Try nvidia-smi (works when the toolkit is installed; cheap probe)
    if shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(["nvidia-smi", "-L"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return "nvidia"
        except Exception:
            pass
    # Device node fallbacks (work in containers without /usr/bin/lspci)
    if any(Path("/dev").glob("nvidia*")):
        return "nvidia"
    if Path("/dev/kfd").exists():
        return "amd"
    # Last resort: lspci if available
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


def detect_default_interface() -> str:
    """Detect the default network interface."""
    try:
        out = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0 and "dev" in out.stdout:
            parts = out.stdout.strip().split()
            dev_idx = parts.index("dev")
            return parts[dev_idx + 1]
    except Exception as e:
        logger.debug(f"Failed to detect default interface: {e}")
    return "eth0"


def bootstrap(dag: DAG, mode: ResolveMode) -> dict:
    """
    Ensure core nodes exist in the graph.

    All nodes are always added — dag.add() skips duplicates.
    Nodes that aren't relevant return SKIPPED at resolve time,
    which auto-skips their children.

    Returns dict of node_id → action ("added" or "exists")
    """
    actions = {}

    def add(node_id, node, parents=None, perms="***"):
        before = len(dag.nodes)
        dag.add(node_id, node, parents=parents, perms=perms)
        actions[node_id] = "added" if len(dag.nodes) > before else "exists"

    # --- Network ---
    interface = os.environ.get("ZP_NETWORK_INTERFACE", detect_default_interface())
    add("network", NetworkNode(interface=interface), perms="r--")

    # --- Docker ---
    add("docker", DockerNode(), parents=["network"], perms="r--")

    # --- GPU detection + install chains ---
    # detect returns SUCCESS_SKIP if driver working → children skip
    # detect returns SUCCESS if GPU found but no driver → children run
    # detect returns SKIPPED if no GPU → children skip
    # All are system-managed; readable but not user-editable.

    add("nvidia", NvidiaGpuNode(), perms="r--")
    add("nvidia-install", ShellScriptNode(
        exec="apt-get install -y nvidia-driver nvidia-container-toolkit && nvidia-ctk runtime configure --runtime=docker",
        verify="nvidia-smi > /dev/null 2>&1",
        description="Install NVIDIA driver + container toolkit",
        timeout=600,
    ), parents=["nvidia"], perms="r--")
    add("nvidia-reboot", ShellScriptNode(
        exec="echo 'NVIDIA kernel module requires reboot'",
        verify="lsmod | grep -q nvidia",
        description="Reboot for NVIDIA kernel module",
        timeout=30,
    ), parents=["nvidia-install"], perms="r--")
    add("nvidia-verify", ShellScriptNode(
        exec="nvidia-smi && docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi",
        verify="nvidia-smi > /dev/null 2>&1",
        description="Verify NVIDIA driver + Docker GPU runtime",
        timeout=300,
    ), parents=["nvidia-reboot"], perms="r--")

    add("amd", AmdGpuNode(), perms="r--")
    add("amd-install", ShellScriptNode(
        exec="apt-get install -y rocm-dkms",
        verify="rocm-smi > /dev/null 2>&1",
        description="Install AMD ROCm drivers",
        timeout=600,
    ), parents=["amd"], perms="r--")
    add("amd-reboot", ShellScriptNode(
        exec="echo 'ROCm kernel module requires reboot'",
        verify="lsmod | grep -q amdgpu",
        description="Reboot for AMD kernel module",
        timeout=30,
    ), parents=["amd-install"], perms="r--")
    add("amd-verify", ShellScriptNode(
        exec="rocm-smi",
        verify="rocm-smi > /dev/null 2>&1",
        description="Verify AMD ROCm drivers",
        timeout=300,
    ), parents=["amd-reboot"], perms="r--")



    # --- System namespaces ---
    # global-settings: zp_* system VarNodes shared by all modules
    # modules:         parent namespace for all installed modules
    from zeropoint_agent.nodes.config.namespace import NamespaceNode
    add("global-settings", NamespaceNode(name="global-settings"), perms="r--")
    add("modules", NamespaceNode(name="modules"), perms="rw*")

    # --- System VarNodes (zp_*) under global-settings ---
    storage_path = os.environ.get("ZP_MODULE_STORAGE", "/var/lib/zeropoint")
    add("global-settings/zp_module_storage",
        VarNode(name="zp_module_storage", value=storage_path),
        parents=["global-settings"])
    os.makedirs(storage_path, exist_ok=True)

    add("global-settings/zp_arch",
        VarNode(name="zp_arch", value=_detect_arch()),
        parents=["global-settings"])
    add("global-settings/zp_gpu_vendor",
        VarNode(name="zp_gpu_vendor", value=_detect_gpu_vendor()),
        parents=["global-settings"])

    # --- Marker directory ---
    marker_dir = os.environ.get("ZP_MARKER_DIR", "/etc/zeropoint")
    add("global-settings/marker_dir",
        VarNode(name="zp_marker_dir", value=marker_dir),
        parents=["global-settings"])
    os.makedirs(marker_dir, exist_ok=True)

    return actions
