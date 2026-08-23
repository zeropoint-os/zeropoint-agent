"""Shared constants for systemd integration."""

import shutil
import sys

MARKER_DIR = "/etc/zeropoint"


def _resolve_agent_bin() -> str:
    """Locate the installed `zeropoint-agent` entry point.

    Deferred-reboot systemd units invoke the agent by absolute path, so a
    hardcoded `/usr/bin/zeropoint-agent` breaks whenever the package lands
    elsewhere (pip --user installs to ~/.local/bin; the devcontainer uses
    /home/vscode/.local/bin). Resolve it at import time from PATH, then
    fall back to argv[0] if this process *is* the console script, and only
    then to the conventional system path.
    """
    found = shutil.which("zeropoint-agent")
    if found:
        return found
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0.endswith("zeropoint-agent"):
        return argv0
    return "/usr/bin/zeropoint-agent"


AGENT_BIN = _resolve_agent_bin()
