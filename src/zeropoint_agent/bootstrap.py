"""Bootstrap — ensures core nodes exist in the graph on startup.

Runs every time the agent starts. Idempotent — dag.add() skips
existing nodes. All nodes are always added — resolve handles
skipping (e.g. DriverNode returns SKIPPED if no GPU, children
auto-skip).

This IS the boot process. There's no separate boot command.
"""

import os
import logging
import subprocess
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

    def add(node_id, node, parents=None):
        before = len(dag.nodes)
        dag.add(node_id, node, parents=parents)
        actions[node_id] = "added" if len(dag.nodes) > before else "exists"

    # --- Network ---
    interface = os.environ.get("ZP_NETWORK_INTERFACE", detect_default_interface())
    add("network", NetworkNode(interface=interface))

    # --- Docker ---
    add("docker", DockerNode(), parents=["network"])

    # --- GPU detection + install chains ---
    # detect returns SUCCESS_SKIP if driver working → children skip
    # detect returns SUCCESS if GPU found but no driver → children run
    # detect returns SKIPPED if no GPU → children skip

    add("nvidia", NvidiaGpuNode())
    add("nvidia-install", ShellScriptNode(
        exec="apt-get install -y nvidia-driver nvidia-container-toolkit && nvidia-ctk runtime configure --runtime=docker",
        verify="nvidia-smi > /dev/null 2>&1",
        description="Install NVIDIA driver + container toolkit",
        timeout=600,
    ), parents=["nvidia"])
    add("nvidia-reboot", ShellScriptNode(
        exec="echo 'NVIDIA kernel module requires reboot'",
        verify="lsmod | grep -q nvidia",
        description="Reboot for NVIDIA kernel module",
        timeout=30,
    ), parents=["nvidia-install"])
    add("nvidia-verify", ShellScriptNode(
        exec="nvidia-smi && docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi",
        verify="nvidia-smi > /dev/null 2>&1",
        description="Verify NVIDIA driver + Docker GPU runtime",
        timeout=300,
    ), parents=["nvidia-reboot"])

    add("amd", AmdGpuNode())
    add("amd-install", ShellScriptNode(
        exec="apt-get install -y rocm-dkms",
        verify="rocm-smi > /dev/null 2>&1",
        description="Install AMD ROCm drivers",
        timeout=600,
    ), parents=["amd"])
    add("amd-reboot", ShellScriptNode(
        exec="echo 'ROCm kernel module requires reboot'",
        verify="lsmod | grep -q amdgpu",
        description="Reboot for AMD kernel module",
        timeout=30,
    ), parents=["amd-install"])
    add("amd-verify", ShellScriptNode(
        exec="rocm-smi",
        verify="rocm-smi > /dev/null 2>&1",
        description="Verify AMD ROCm drivers",
        timeout=300,
    ), parents=["amd-reboot"])



    # --- Storage ---
    storage_path = os.environ.get("ZP_MODULE_STORAGE", "/var/lib/zeropoint")
    add("storage", VarNode(name="ZP_MODULE_STORAGE", value=storage_path))
    os.makedirs(storage_path, exist_ok=True)

    # --- Marker directory ---
    marker_dir = os.environ.get("ZP_MARKER_DIR", "/etc/zeropoint")
    add("marker-dir", VarNode(name="ZP_MARKER_DIR", value=marker_dir))
    os.makedirs(marker_dir, exist_ok=True)

    return actions
