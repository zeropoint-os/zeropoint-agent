"""Reboot — schedules a reboot and verifies completion by kernel boot ID."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from zeropoint_agent.inode import INode, NodeResult, NodeStatus, ResolveMode
from zeropoint_agent.jobs import JobContext, JobSpec


def current_boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        return ""


@dataclass
class RebootResult:
    request_id: str
    requested_boot_id: str
    current_boot_id: str
    scheduled: bool = False


@dataclass
class Reboot(INode[None, RebootResult]):
    request_id: str = ""
    requested_boot_id: str = ""

    def resolve(self, input, mode: ResolveMode) -> NodeResult[RebootResult]:
        current = current_boot_id()
        result = RebootResult(
            request_id=self.request_id,
            requested_boot_id=self.requested_boot_id,
            current_boot_id=current,
            scheduled=bool(self.request_id),
        )
        if not self.request_id or (
            self.requested_boot_id
            and current
            and current != self.requested_boot_id
        ):
            return NodeResult.success(result)
        if mode == ResolveMode.MOCK:
            return NodeResult.pending_reboot(result)
        return NodeResult.failed("live reboot must run through its Promise job")

    def resolve_promise(self, input, mode, context):
        if mode != ResolveMode.LIVE:
            return super().resolve_promise(input, mode, context)
        return context.jobs.submit(
            context,
            JobSpec(
                kind="system.reboot",
                arguments={
                    "request_id": self.request_id,
                    "requested_boot_id": self.requested_boot_id,
                },
                resources=["system:reboot"],
                idempotency_key=f"reboot:{self.request_id}",
            ),
        )

    def verify(self, mode: ResolveMode) -> NodeResult[RebootResult]:
        current = current_boot_id()
        result = RebootResult(
            request_id=self.request_id,
            requested_boot_id=self.requested_boot_id,
            current_boot_id=current,
        )
        if not self.request_id:
            return NodeResult.success(result)
        if (
            self.requested_boot_id
            and current
            and current != self.requested_boot_id
        ):
            return NodeResult.success(result)
        pending = NodeResult()
        pending.status = NodeStatus.PENDING
        pending.output = result
        return pending

    def remove(self, mode: ResolveMode) -> NodeResult[RebootResult]:
        return NodeResult.success()


def execute_reboot_job(
    context: JobContext,
    arguments: Dict[str, str],
) -> NodeResult[RebootResult]:
    request_id = str(arguments["request_id"])
    requested_boot_id = str(arguments["requested_boot_id"])
    current = current_boot_id()
    result = RebootResult(
        request_id=request_id,
        requested_boot_id=requested_boot_id,
        current_boot_id=current,
    )
    if requested_boot_id and current and current != requested_boot_id:
        return NodeResult.success(result)

    context.progress(0.25, {"phase": "schedule"})
    command = [
        "systemd-run",
        f"--unit=zeropoint-reboot-{request_id[:12]}",
        "--on-active=2s",
        "--collect",
        "/usr/bin/systemctl",
        "reboot",
    ]
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if process.returncode != 0:
        return NodeResult.failed(
            f"failed to schedule reboot: {process.stderr.strip()}"
        )
    result.scheduled = True
    context.progress(1.0, {"phase": "scheduled"})
    return NodeResult.pending_reboot(result)
