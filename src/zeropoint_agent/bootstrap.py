"""Bootstrap — ensures core nodes exist in the graph on startup.

Runs every time the agent starts. Idempotent — if nodes already exist,
they're skipped. If hardware isn't available, falls back to env vars.

This IS the boot process. There's no separate boot command.
"""

import os
import logging
import subprocess
from typing import Optional

from zeropoint_agent.dag import DAG
from zeropoint_agent.inode import ResolveMode, NodeStatus
from zeropoint_agent.nodes.system.network import NetworkNode
from zeropoint_agent.nodes.system.docker import DockerNode
from zeropoint_agent.nodes.system.driver import DriverNode
from zeropoint_agent.nodes.config.var import VarNode

logger = logging.getLogger(__name__)


def detect_default_interface() -> str:
    """Detect the default network interface."""
    try:
        out = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0 and "dev" in out.stdout:
            # "default via 172.17.0.1 dev eth0" → extract "eth0"
            parts = out.stdout.strip().split()
            dev_idx = parts.index("dev")
            return parts[dev_idx + 1]
    except Exception as e:
        logger.debug(f"Failed to detect default interface: {e}")
    return "eth0"


def detect_nvidia_gpu() -> bool:
    """Check if an NVIDIA GPU is present."""
    try:
        out = subprocess.run(
            ["lspci"], capture_output=True, text=True, timeout=5
        )
        return "NVIDIA" in out.stdout
    except Exception:
        return False


def detect_boot_disk() -> Optional[str]:
    """Detect boot disk stable ID from /dev/disk/by-id/."""
    try:
        # Find the device mounted at /
        out = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", "/"],
            capture_output=True, text=True, timeout=5
        )
        if out.returncode != 0:
            return None

        root_device = os.path.realpath(out.stdout.strip())

        # Find its stable ID in /dev/disk/by-id/
        by_id_dir = "/dev/disk/by-id"
        if not os.path.isdir(by_id_dir):
            return None

        for link in os.listdir(by_id_dir):
            full_path = os.path.join(by_id_dir, link)
            if os.path.islink(full_path):
                target = os.path.realpath(full_path)
                # Match the parent disk, not the partition
                # Strip partition suffix to get the disk
                if target == root_device or root_device.startswith(target):
                    if "-part" not in link:
                        return link

        return None
    except Exception as e:
        logger.debug(f"Failed to detect boot disk: {e}")
        return None


def bootstrap(dag: DAG, mode: ResolveMode) -> dict:
    """
    Ensure core nodes exist in the graph.

    Runs every startup. Idempotent — skips existing nodes.
    Probes hardware, falls back to env vars.

    Returns dict of node_id → action taken ("exists", "added", "skipped")
    """
    actions = {}
    existing = set(dag.nodes.keys())

    # --- Network ---
    if "network" not in existing:
        interface = os.environ.get("ZP_NETWORK_INTERFACE", detect_default_interface())
        dag.add("network", NetworkNode(interface=interface))
        actions["network"] = "added"
        logger.info(f"Bootstrap: added network node (interface={interface})")
    else:
        actions["network"] = "exists"

    # --- Docker ---
    if "docker" not in existing:
        dag.add("docker", DockerNode(), parents=["network"])
        actions["docker"] = "added"
        logger.info("Bootstrap: added docker node")
    else:
        actions["docker"] = "exists"

    # --- NVIDIA GPU (conditional) ---
    if "nvidia" not in existing:
        has_gpu = detect_nvidia_gpu() if mode != ResolveMode.MOCK else False
        if has_gpu:
            dag.add("nvidia", DriverNode(driver="nvidia"))
            actions["nvidia"] = "added"
            logger.info("Bootstrap: added nvidia driver node (GPU detected)")
        else:
            actions["nvidia"] = "skipped (no GPU)"
            logger.info("Bootstrap: skipping nvidia (no GPU detected)")
    else:
        actions["nvidia"] = "exists"

    # --- Storage (disk chain or env var fallback) ---
    if "storage" not in existing:
        storage_path = os.environ.get("ZP_MODULE_STORAGE")

        if storage_path:
            # Env var set — use it directly (devcontainer, existing Linux)
            dag.add("storage", VarNode(name="ZP_MODULE_STORAGE", value=storage_path))
            actions["storage"] = f"added (env var: {storage_path})"
            logger.info(f"Bootstrap: added storage var from env (ZP_MODULE_STORAGE={storage_path})")
        else:
            # Try to detect boot disk for the full chain
            boot_disk = detect_boot_disk() if mode != ResolveMode.MOCK else None

            if boot_disk:
                # TODO: add full disk chain (disk → partition → format → mount → path → var)
                # For now, just add the var with default path
                dag.add("storage", VarNode(
                    name="ZP_MODULE_STORAGE", value="/var/lib/zeropoint"))
                actions["storage"] = f"added (boot disk: {boot_disk}, default path)"
                logger.info(f"Bootstrap: added storage var (boot disk={boot_disk})")
            else:
                # Absolute fallback
                dag.add("storage", VarNode(
                    name="ZP_MODULE_STORAGE", value="/var/lib/zeropoint"))
                actions["storage"] = "added (fallback: /var/lib/zeropoint)"
                logger.info("Bootstrap: added storage var (fallback path)")

        # Ensure the storage directory exists
        storage_val = dag.get("storage").node.value if hasattr(dag.get("storage").node, "value") else "/var/lib/zeropoint"
        os.makedirs(storage_val, exist_ok=True)
    else:
        actions["storage"] = "exists"

    # --- Docker data root (depends on storage) ---
    if "docker-data" not in existing:
        docker_data = os.environ.get("ZP_DOCKER_DATA")
        if docker_data:
            dag.add("docker-data", VarNode(name="ZP_DOCKER_DATA", value=docker_data))
            actions["docker-data"] = f"added (env var: {docker_data})"
        else:
            actions["docker-data"] = "skipped (no ZP_DOCKER_DATA)"
    else:
        actions["docker-data"] = "exists"

    # --- Marker directory ---
    if "marker-dir" not in existing:
        marker_dir = os.environ.get("ZP_MARKER_DIR", "/etc/zeropoint")
        dag.add("marker-dir", VarNode(name="ZP_MARKER_DIR", value=marker_dir))
        os.makedirs(marker_dir, exist_ok=True)
        actions["marker-dir"] = f"added ({marker_dir})"
        logger.info(f"Bootstrap: added marker-dir var ({marker_dir})")
    else:
        actions["marker-dir"] = "exists"

    return actions
