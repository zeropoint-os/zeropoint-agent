"""ShellScriptNode — wraps any shell command as a DAG node.

The escape hatch: any operation that's "run this command, check exit code"
doesn't need a custom node class. Wraps existing boot scripts, one-off
commands, or any process as a typed DAG node.

Usage:
    dag.add("resize-rootfs", ShellScriptNode(
        exec="/usr/local/bin/zeropoint-resize-rootfs.sh",
        verify="test -f /etc/zeropoint/.zeropoint-resize-rootfs",
        description="Expand root filesystem",
    ))
"""

import logging
import subprocess
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

from zeropoint_agent.inode import INode, ResolveMode, NodeResult, NodeStatus, SystemdUnit
from zeropoint_agent.nodes._systemd import MARKER_DIR

logger = logging.getLogger(__name__)


@dataclass
class ShellScriptResult:
    """Contract for a shell script node."""
    exec_cmd: str
    verify_cmd: str
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    verified: bool = False


class ShellScriptNode(INode[None, ShellScriptResult]):
    """
    Wraps a shell command as a DAG node.

    Takes any input type (defaults to None for root nodes).
    Runs exec_cmd to resolve, runs verify_cmd to check convergence.
    If verify fails after exec, returns PENDING_REBOOT with a systemd unit.

    Args:
        exec: Command to run for resolve (string, passed to shell)
        verify: Command to run for verify (string, exit 0 = converged)
        description: Human-readable description
        timeout: Seconds before the command is killed
        marker: If set, creates this marker file on success (relative to MARKER_DIR)
        env: Additional environment variables for the command
    """

    # Allow any input type — ShellScriptNode is flexible
    def __init__(self, exec_cmd: str = "", verify_cmd: str = "/bin/true",
                 description: str = "", timeout: int = 300,
                 marker: Optional[str] = None,
                 env: Optional[Dict[str, str]] = None,
                 # Legacy keyword aliases for backward compatibility:
                 exec: Optional[str] = None,
                 verify: Optional[str] = None):
        self.exec_cmd = exec if exec is not None else exec_cmd
        self.verify_cmd = verify if verify is not None else verify_cmd
        self.description = description or self.exec_cmd
        self.timeout = timeout
        self.marker = marker
        self.env = env or {}

    def _run(self, cmd: str) -> subprocess.CompletedProcess:
        """Run a shell command."""
        env = None
        if self.env:
            import os
            env = {**os.environ, **self.env}
        return subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=self.timeout, env=env
        )

    def _make_result(self, cmd: str, proc: subprocess.CompletedProcess,
                     verified: bool = False) -> ShellScriptResult:
        return ShellScriptResult(
            exec_cmd=self.exec_cmd,
            verify_cmd=self.verify_cmd,
            exit_code=proc.returncode,
            stdout=proc.stdout.strip()[-500:] if proc.stdout else "",
            stderr=proc.stderr.strip()[-500:] if proc.stderr else "",
            verified=verified,
        )

    def resolve(self, input, mode: ResolveMode) -> NodeResult[ShellScriptResult]:
        result = ShellScriptResult(
            exec_cmd=self.exec_cmd, verify_cmd=self.verify_cmd)

        if mode == ResolveMode.MOCK:
            result.exit_code = 0
            result.verified = True
            return NodeResult.success(result)

        try:
            logger.info(f"ShellScript: executing '{self.exec_cmd}'")
            proc = self._run(self.exec_cmd)
            result = self._make_result(self.exec_cmd, proc)

            if proc.returncode != 0:
                return NodeResult.failed(
                    f"Command failed (exit {proc.returncode}): {result.stderr}",
                    result)

            # Touch marker if configured
            if self.marker:
                import os
                marker_path = f"{MARKER_DIR}/{self.marker}"
                os.makedirs(os.path.dirname(marker_path), exist_ok=True)
                open(marker_path, "w").close()
                logger.debug(f"Touched marker: {marker_path}")

            # Verify after exec
            verify_proc = self._run(self.verify_cmd)
            result.verified = verify_proc.returncode == 0

            if result.verified:
                return NodeResult.success(result)

            # Exec succeeded but verify failed — needs reboot
            return (NodeResult
                .pending_reboot(result)
                .add_unit(SystemdUnit(
                    name=f"zeropoint-{self._safe_name()}",
                    description=f"ZeroPoint: {self.description}",
                    exec_start=self.verify_cmd,
                    timeout_sec=self.timeout,
                    condition_path_not_exists=f"{MARKER_DIR}/.zeropoint-{self._safe_name()}",
                )))

        except subprocess.TimeoutExpired:
            return NodeResult.failed(
                f"Command timed out after {self.timeout}s", result)
        except Exception as e:
            return NodeResult.failed(str(e), result)

    def verify(self, mode: ResolveMode) -> NodeResult[ShellScriptResult]:
        result = ShellScriptResult(
            exec_cmd=self.exec_cmd, verify_cmd=self.verify_cmd)

        if mode == ResolveMode.MOCK:
            result.verified = True
            return NodeResult.success(result)

        try:
            proc = self._run(self.verify_cmd)
            result = self._make_result(self.verify_cmd, proc,
                                       verified=proc.returncode == 0)
            if proc.returncode == 0:
                return NodeResult.success(result)
            return NodeResult.pending_reboot(result)
        except Exception as e:
            return NodeResult.failed(str(e), result)

    def remove(self, mode: ResolveMode) -> NodeResult[ShellScriptResult]:
        # Shell scripts generally don't have a reverse operation
        return NodeResult.success()

    def _safe_name(self) -> str:
        """Generate a safe name for systemd units from the exec command."""
        import re
        name = self.exec_cmd.split("/")[-1].split()[0]
        return re.sub(r'[^a-zA-Z0-9-]', '-', name)
