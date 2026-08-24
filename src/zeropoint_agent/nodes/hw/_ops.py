"""Audited OS operations for the hardware-provisioning nodes.

Every destructive disk command (sfdisk, mkfs, wipefs, mount, umount) and
every read-only probe (blkid, /proc/mounts, /dev/disk/by-id) lives here,
and nowhere else. Two reasons:

  1. **One audit surface.** Disk provisioning is irreversibly destructive.
     Keeping the exec calls in a single small module means the whole
     blast radius can be reviewed in one place instead of scattered
     across five node classes.
  2. **A test seam.** Nodes call these helpers; tests monkeypatch this
     module. That lets the Disk -> Partition -> Format -> Mount ->
     MountDir chain be exercised end to end without touching a real
     block device.

Nothing here runs in MOCK mode — the nodes short-circuit before reaching
these functions. These are the LIVE paths only.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

BY_ID_DIR = "/dev/disk/by-id"
FSTAB = "/etc/fstab"
FSTAB_MARKER = "# zeropoint-agent"


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(cmd: List[str], *, input_text: Optional[str] = None,
        timeout: int = 120) -> RunResult:
    """Run a command as an argv list (never shell=True for disk ops).

    Returns a RunResult regardless of exit code; callers decide what a
    non-zero code means. Raises only on the process failing to spawn.
    """
    logger.info("hw op: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd, input=input_text, capture_output=True, text=True, timeout=timeout,
    )
    return RunResult(
        returncode=proc.returncode,
        stdout=(proc.stdout or "").strip(),
        stderr=(proc.stderr or "").strip(),
    )


# --- read-only probes ----------------------------------------------------

def by_id_path(stable_id: str) -> str:
    """The /dev/disk/by-id path for a stable id (may not exist)."""
    return f"{BY_ID_DIR}/{stable_id}"


def device_for(stable_id: str) -> Optional[str]:
    """Resolve a stable id to its real /dev node, or None if absent."""
    if not stable_id:
        return None
    path = by_id_path(stable_id)
    if os.path.exists(path):
        return os.path.realpath(path)
    return None


def blkid_value(device: str, tag: str) -> Optional[str]:
    """Read a single blkid tag (TYPE, UUID, LABEL) for a device."""
    if not device:
        return None
    res = run(["blkid", "-s", tag, "-o", "value", device], timeout=30)
    if res.ok and res.stdout:
        return res.stdout
    return None


def fs_type(device: str) -> Optional[str]:
    """The filesystem type on a device, or None if unformatted/absent."""
    return blkid_value(device, "TYPE")


def fs_uuid(device: str) -> Optional[str]:
    return blkid_value(device, "UUID")


def is_mounted(mountpoint: str) -> bool:
    """True if anything is mounted at mountpoint (per /proc/mounts)."""
    if not mountpoint:
        return False
    target = os.path.realpath(mountpoint)
    try:
        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and os.path.realpath(parts[1]) == target:
                    return True
    except OSError:
        return False
    return False


# --- destructive operations ---------------------------------------------

def create_partition(disk_device: str, size_mb: int, part_type: str,
                     label: Optional[str] = None) -> RunResult:
    """Append one partition to a disk with sfdisk.

    Uses `sfdisk --append`, which adds a partition in the next free slot
    without rewriting existing entries. The script line is
    ``,{size},{type}``: empty start (next free), an explicit size in MiB
    (or empty for "rest of disk" when size_mb <= 0), and the partition
    type. Callers are expected to create partitions in ascending number
    order so sfdisk's slot assignment matches the node's declared number.
    """
    size_spec = f"{size_mb}M" if size_mb and size_mb > 0 else ""
    script = f",{size_spec},{part_type}\n"
    return run(["sfdisk", "--append", disk_device], input_text=script, timeout=120)


def settle() -> None:
    """Re-read partition tables and wait for udev to create device nodes.

    Best-effort: a missing partprobe/udevadm is not fatal — the caller
    falls back to a reboot when the device still doesn't appear.
    """
    for cmd in (["partprobe"], ["udevadm", "settle"]):
        try:
            run(cmd, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("settle step %s skipped: %s", cmd[0], e)


def make_filesystem(device: str, filesystem: str,
                    label: Optional[str] = None) -> RunResult:
    """Create a filesystem on a device with mkfs.<filesystem>."""
    cmd = [f"mkfs.{filesystem}"]
    if label:
        # ext*/xfs/btrfs all accept -L; vfat uses -n. Cover the common case.
        cmd += (["-n", label] if filesystem in ("vfat", "fat") else ["-L", label])
    # Force non-interactive for the ext family.
    if filesystem.startswith("ext"):
        cmd.append("-F")
    cmd.append(device)
    return run(cmd, timeout=600)


def wipe(device: str) -> RunResult:
    """Erase filesystem/partition signatures from a device."""
    return run(["wipefs", "-a", device], timeout=120)


def mount(device: str, mountpoint: str, options: str = "defaults") -> RunResult:
    os.makedirs(mountpoint, exist_ok=True)
    cmd = ["mount"]
    if options:
        cmd += ["-o", options]
    cmd += [device, mountpoint]
    return run(cmd, timeout=60)


def umount(mountpoint: str) -> RunResult:
    return run(["umount", mountpoint], timeout=60)


# --- fstab management ----------------------------------------------------

def _fstab_line(uuid: str, mountpoint: str, fstype: str, options: str) -> str:
    return (f"UUID={uuid} {mountpoint} {fstype} {options or 'defaults'} "
            f"0 2  {FSTAB_MARKER}\n")


def ensure_fstab(uuid: str, mountpoint: str, fstype: str,
                 options: str = "defaults", path: str = FSTAB) -> None:
    """Idempotently install an fstab entry for a mountpoint.

    Keyed by mountpoint: an existing zeropoint-managed line for the same
    mountpoint is replaced, so re-resolves don't accumulate duplicates.
    Only ever touches lines carrying our marker.
    """
    if not uuid or not mountpoint:
        return
    new_line = _fstab_line(uuid, mountpoint, fstype, options)
    kept: List[str] = []
    try:
        with open(path, "r") as f:
            for line in f:
                if FSTAB_MARKER in line and _line_mountpoint(line) == mountpoint:
                    continue  # drop the stale managed entry; we re-add below
                kept.append(line)
    except FileNotFoundError:
        kept = []
    if kept and not kept[-1].endswith("\n"):
        kept[-1] += "\n"
    kept.append(new_line)
    with open(path, "w") as f:
        f.writelines(kept)


def remove_fstab(mountpoint: str, path: str = FSTAB) -> None:
    """Remove our managed fstab entry for a mountpoint, if present."""
    if not mountpoint:
        return
    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return
    kept = [
        ln for ln in lines
        if not (FSTAB_MARKER in ln and _line_mountpoint(ln) == mountpoint)
    ]
    if len(kept) != len(lines):
        with open(path, "w") as f:
            f.writelines(kept)


def _line_mountpoint(line: str) -> Optional[str]:
    parts = line.split()
    return parts[1] if len(parts) >= 2 else None
