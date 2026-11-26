"""Durable interactive Job executors for the Tailscale integration."""

from __future__ import annotations

from typing import Any, Dict

from zeropoint_agent.inode import NodeResult

from . import manager
from .nodes import system_tailscale_result


def execute_tailscale_enroll_job(context, arguments: Dict[str, Any]):
    status = manager.enroll_interactively(
        context,
        str(arguments["machine_name"]),
        dict(arguments["envoy"]),
        str(arguments.get("image") or manager.IMAGE),
    )
    return NodeResult.success(system_tailscale_result(status))
