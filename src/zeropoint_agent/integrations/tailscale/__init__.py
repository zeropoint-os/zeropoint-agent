"""Tailscale integration: one machine runtime and named per-app Services."""

from .nodes import (
    SystemTailscale,
    SystemTailscaleResult,
    TailnetExposure,
    TailnetExposureResult,
)

__all__ = [
    "SystemTailscale",
    "SystemTailscaleResult",
    "TailnetExposure",
    "TailnetExposureResult",
]
