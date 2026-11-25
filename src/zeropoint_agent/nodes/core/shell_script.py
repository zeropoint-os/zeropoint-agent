"""ShellScript — run cancellable commands as durable DAG jobs."""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from zeropoint_agent.inode import INode, NodeResult, ResolveMode, SystemdUnit
from zeropoint_agent.jobs import JobCancelled, JobContext, JobSpec
from zeropoint_agent.promise import Promise
from zeropoint_agent.promise_controls import (
    ShellScriptCancellation,
    ShellScriptProgress,
    UnsupportedCheckpoint,
    UnsupportedPause,
)
from zeropoint_agent.nodes._systemd import MARKER_DIR

logger = logging.getLogger(__name__)


@dataclass
class ShellScriptResult:
    exec_cmd: str
    verify_cmd: str
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    verified: bool = False


ShellScriptPromise = Promise[
    NodeResult[ShellScriptResult],
    ShellScriptCancellation,
    UnsupportedPause,
    ShellScriptProgress,
    UnsupportedCheckpoint,
]


def _safe_name(command: str) -> str:
    name = command.split("/")[-1].split()[0]
    return re.sub(r"[^a-zA-Z0-9-]", "-", name)


def _environment(extra: Dict[str, str]) -> Optional[Dict[str, str]]:
    return {**os.environ, **extra} if extra else None


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)


def _run_cancellable(
    command: str,
    *,
    timeout: int,
    env: Dict[str, str],
    context: JobContext,
) -> subprocess.CompletedProcess:
    process = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_environment(env),
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    try:
        while True:
            context.cancellation.raise_if_requested()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                return subprocess.CompletedProcess(
                    command,
                    process.returncode,
                    stdout,
                    stderr,
                )
            except subprocess.TimeoutExpired:
                # communicate() drains both pipes while it waits and can be
                # called again without losing buffered output.
                continue
    except BaseException:
        _terminate_process(process)
        raise


def execute_shell_script_job(
    context: JobContext,
    arguments: Dict[str, Any],
) -> NodeResult[ShellScriptResult]:
    exec_cmd = str(arguments["exec_cmd"])
    verify_cmd = str(arguments.get("verify_cmd") or "/bin/true")
    timeout = int(arguments.get("timeout") or 300)
    marker = arguments.get("marker")
    description = str(arguments.get("description") or exec_cmd)
    env = dict(arguments.get("env") or {})
    result = ShellScriptResult(exec_cmd=exec_cmd, verify_cmd=verify_cmd)

    try:
        context.progress(0.05, {"phase": "execute"})
        process = _run_cancellable(
            exec_cmd,
            timeout=timeout,
            env=env,
            context=context,
        )
        result.exit_code = process.returncode
        result.stdout = process.stdout.strip()[-500:] if process.stdout else ""
        result.stderr = process.stderr.strip()[-500:] if process.stderr else ""
        if process.returncode != 0:
            return NodeResult.failed(
                f"Command failed (exit {process.returncode}): {result.stderr}",
                result,
            )

        if marker:
            marker_path = f"{MARKER_DIR}/{marker}"
            os.makedirs(os.path.dirname(marker_path), exist_ok=True)
            with open(marker_path, "w", encoding="utf-8"):
                pass

        context.progress(0.8, {"phase": "verify"})
        verification = _run_cancellable(
            verify_cmd,
            timeout=timeout,
            env=env,
            context=context,
        )
        result.verified = verification.returncode == 0
        if result.verified:
            context.progress(1.0, {"phase": "complete"})
            return NodeResult.success(result)

        return (
            NodeResult.pending_reboot(result)
            .add_unit(SystemdUnit(
                name=f"zeropoint-{_safe_name(exec_cmd)}",
                description=f"ZeroPoint: {description}",
                exec_start=verify_cmd,
                timeout_sec=timeout,
                condition_path_not_exists=(
                    f"{MARKER_DIR}/.zeropoint-{_safe_name(exec_cmd)}"
                ),
            ))
        )
    except JobCancelled:
        raise
    except subprocess.TimeoutExpired:
        return NodeResult.failed(
            f"Command timed out after {timeout}s",
            result,
        )
    except Exception as error:
        return NodeResult.failed(str(error), result)


def execute_shell_script_verify_job(
    context: JobContext,
    arguments: Dict[str, Any],
) -> NodeResult[ShellScriptResult]:
    exec_cmd = str(arguments.get("exec_cmd") or "")
    verify_cmd = str(arguments.get("verify_cmd") or "/bin/true")
    timeout = int(arguments.get("timeout") or 300)
    env = dict(arguments.get("env") or {})
    result = ShellScriptResult(exec_cmd=exec_cmd, verify_cmd=verify_cmd)
    try:
        context.progress(0.1, {"phase": "verify"})
        process = _run_cancellable(
            verify_cmd,
            timeout=timeout,
            env=env,
            context=context,
        )
        result.exit_code = process.returncode
        result.stdout = process.stdout.strip()[-500:] if process.stdout else ""
        result.stderr = process.stderr.strip()[-500:] if process.stderr else ""
        result.verified = process.returncode == 0
        context.progress(1.0, {"phase": "complete"})
        return (
            NodeResult.success(result)
            if result.verified
            else NodeResult.pending_reboot(result)
        )
    except JobCancelled:
        raise
    except subprocess.TimeoutExpired:
        return NodeResult.failed(
            f"Verification timed out after {timeout}s",
            result,
        )
    except Exception as error:
        return NodeResult.failed(str(error), result)


@dataclass
class ShellScript(INode[None, ShellScriptResult]):
    """Run and verify a command, durably in LIVE mode."""

    exec_cmd: str = ""
    verify_cmd: str = "/bin/true"
    description: str = ""
    timeout: int = 300
    marker: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.description:
            self.description = self.exec_cmd

    def _run(self, command: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            env=_environment(self.env),
        )

    def _result(
        self,
        process: subprocess.CompletedProcess,
        *,
        verified: bool = False,
    ) -> ShellScriptResult:
        return ShellScriptResult(
            exec_cmd=self.exec_cmd,
            verify_cmd=self.verify_cmd,
            exit_code=process.returncode,
            stdout=process.stdout.strip()[-500:] if process.stdout else "",
            stderr=process.stderr.strip()[-500:] if process.stderr else "",
            verified=verified,
        )

    def resolve(self, input, mode: ResolveMode) -> NodeResult[ShellScriptResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(ShellScriptResult(
                exec_cmd=self.exec_cmd,
                verify_cmd=self.verify_cmd,
                exit_code=0,
                verified=True,
            ))
        process = self._run(self.exec_cmd)
        result = self._result(process)
        if process.returncode != 0:
            return NodeResult.failed(
                f"Command failed (exit {process.returncode}): {result.stderr}",
                result,
            )
        if self.marker:
            marker_path = f"{MARKER_DIR}/{self.marker}"
            os.makedirs(os.path.dirname(marker_path), exist_ok=True)
            with open(marker_path, "w", encoding="utf-8"):
                pass
        verification = self._run(self.verify_cmd)
        result.verified = verification.returncode == 0
        if result.verified:
            return NodeResult.success(result)
        return (
            NodeResult.pending_reboot(result)
            .add_unit(SystemdUnit(
                name=f"zeropoint-{_safe_name(self.exec_cmd)}",
                description=f"ZeroPoint: {self.description}",
                exec_start=self.verify_cmd,
                timeout_sec=self.timeout,
                condition_path_not_exists=(
                    f"{MARKER_DIR}/.zeropoint-{_safe_name(self.exec_cmd)}"
                ),
            ))
        )

    def resolve_promise(
        self,
        input,
        mode: ResolveMode,
        context,
    ) -> ShellScriptPromise:
        if mode != ResolveMode.LIVE:
            return super().resolve_promise(input, mode, context)
        return context.jobs.submit(
            context,
            JobSpec(
                kind="node.shell_script",
                arguments={
                    "exec_cmd": self.exec_cmd,
                    "verify_cmd": self.verify_cmd,
                    "description": self.description,
                    "timeout": self.timeout,
                    "marker": self.marker,
                    "env": self.env,
                },
                resources=[f"shell-script:{self.id}"],
                timeout_seconds=float(self.timeout) + 5,
            ),
        )

    def verify_promise(
        self,
        input,
        mode: ResolveMode,
        context,
    ) -> ShellScriptPromise:
        if mode != ResolveMode.LIVE:
            return super().verify_promise(input, mode, context)
        return context.jobs.submit(
            context,
            JobSpec(
                kind="node.shell_script.verify",
                arguments={
                    "exec_cmd": self.exec_cmd,
                    "verify_cmd": self.verify_cmd,
                    "timeout": self.timeout,
                    "env": self.env,
                },
                resources=[f"shell-script:{self.id}"],
                timeout_seconds=float(self.timeout) + 5,
            ),
        )

    def verify(self, mode: ResolveMode) -> NodeResult[ShellScriptResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(ShellScriptResult(
                exec_cmd=self.exec_cmd,
                verify_cmd=self.verify_cmd,
                verified=True,
            ))
        try:
            process = self._run(self.verify_cmd)
            result = self._result(
                process,
                verified=process.returncode == 0,
            )
            return (
                NodeResult.success(result)
                if result.verified
                else NodeResult.pending_reboot(result)
            )
        except Exception as error:
            return NodeResult.failed(str(error), ShellScriptResult(
                exec_cmd=self.exec_cmd,
                verify_cmd=self.verify_cmd,
            ))

    def remove(self, mode: ResolveMode) -> NodeResult[ShellScriptResult]:
        return NodeResult.success()
