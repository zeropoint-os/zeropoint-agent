"""Audited operating-system operations for hardware graph nodes."""

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


def run(
    cmd: List[str],
    *,
    input_text: Optional[str] = None,
    timeout: int = 120,
) -> RunResult:
    logger.info("hw op: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return RunResult(
        returncode=proc.returncode,
        stdout=(proc.stdout or "").strip(),
        stderr=(proc.stderr or "").strip(),
    )


def by_id_path(stable_id: str) -> str:
    return f"{BY_ID_DIR}/{stable_id}"


def device_for(stable_id: str) -> Optional[str]:
    if not stable_id:
        return None
    if stable_id.startswith("/dev/") and os.path.exists(stable_id):
        return os.path.realpath(stable_id)
    path = by_id_path(stable_id)
    if os.path.exists(path):
        return os.path.realpath(path)
    fallback = f"/dev/{stable_id}"
    return os.path.realpath(fallback) if os.path.exists(fallback) else None


def blkid_value(device: str, tag: str) -> Optional[str]:
    if not device:
        return None
    result = run(["blkid", "-s", tag, "-o", "value", device], timeout=30)
    return result.stdout if result.ok and result.stdout else None


def fs_type(device: str) -> Optional[str]:
    return blkid_value(device, "TYPE")


def fs_uuid(device: str) -> Optional[str]:
    return blkid_value(device, "UUID")


def is_mounted(mountpoint: str) -> bool:
    if not mountpoint:
        return False
    target = os.path.realpath(mountpoint)
    try:
        with open("/proc/mounts", "r", encoding="utf-8") as mounts:
            return any(
                len(parts := line.split()) >= 2
                and os.path.realpath(parts[1]) == target
                for line in mounts
            )
    except OSError:
        return False


def create_partition(
    disk_device: str,
    size_mb: int,
    part_type: str,
    label: Optional[str] = None,
) -> RunResult:
    del label
    size_spec = f"{size_mb}M" if size_mb and size_mb > 0 else ""
    return run(
        ["sfdisk", "--append", disk_device],
        input_text=f",{size_spec},{part_type}\n",
    )


def delete_partition(disk_device: str, number: int) -> RunResult:
    return run(
        ["sfdisk", "--delete", disk_device, str(number)],
        timeout=120,
    )


def settle() -> None:
    for command in (["partprobe"], ["udevadm", "settle"]):
        try:
            run(command, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            logger.warning("settle step %s skipped: %s", command[0], error)


def make_filesystem(
    device: str,
    filesystem: str,
    label: Optional[str] = None,
) -> RunResult:
    command = [f"mkfs.{filesystem}"]
    if label:
        command += (
            ["-n", label] if filesystem in {"vfat", "fat"} else ["-L", label]
        )
    if filesystem.startswith("ext"):
        command.append("-F")
    command.append(device)
    return run(command, timeout=600)


def wipe(device: str) -> RunResult:
    return run(["wipefs", "-a", device])


def mount(device: str, mountpoint: str, options: str = "defaults") -> RunResult:
    os.makedirs(mountpoint, exist_ok=True)
    command = ["mount"]
    if options:
        command += ["-o", options]
    return run([*command, device, mountpoint], timeout=60)


def umount(mountpoint: str) -> RunResult:
    return run(["umount", mountpoint], timeout=60)


def ensure_fstab(
    uuid: str,
    mountpoint: str,
    filesystem: str,
    options: str = "defaults",
    path: str = FSTAB,
) -> None:
    if not uuid or not mountpoint:
        return
    line = (
        f"UUID={uuid} {mountpoint} {filesystem} {options or 'defaults'} "
        f"0 2  {FSTAB_MARKER}\n"
    )
    kept: List[str] = []
    try:
        with open(path, "r", encoding="utf-8") as file:
            for existing in file:
                parts = existing.split()
                if (
                    FSTAB_MARKER in existing
                    and len(parts) >= 2
                    and parts[1] == mountpoint
                ):
                    continue
                kept.append(existing)
    except FileNotFoundError:
        pass
    if kept and not kept[-1].endswith("\n"):
        kept[-1] += "\n"
    kept.append(line)
    with open(path, "w", encoding="utf-8") as file:
        file.writelines(kept)


def remove_fstab(mountpoint: str, path: str = FSTAB) -> None:
    try:
        with open(path, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except FileNotFoundError:
        return
    kept = [
        line for line in lines
        if not (
            FSTAB_MARKER in line
            and len(parts := line.split()) >= 2
            and parts[1] == mountpoint
        )
    ]
    if len(kept) != len(lines):
        with open(path, "w", encoding="utf-8") as file:
            file.writelines(kept)
