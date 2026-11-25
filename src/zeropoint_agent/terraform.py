"""Terraform executor — thin wrapper around the terraform CLI.

Mirrors internal/terraform/executor.go from the Go agent, but invokes
the terraform binary via subprocess (no Python equivalent of
terraform-exec needed for our limited use).
"""

import json
import logging
import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)


class TerraformError(RuntimeError):
    """Raised when a terraform command fails."""


def _find_terraform() -> str:
    path = shutil.which("terraform")
    if not path:
        raise TerraformError("terraform binary not found on PATH")
    return path


def _vars_args(variables: Mapping[str, str]) -> list:
    args = []
    for k, v in variables.items():
        args.extend(["-var", f"{k}={v}"])
    return args


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=3)


def _run(
    cmd: list,
    cwd: Path,
    check: bool = True,
    capture: bool = True,
    cancellation=None,
) -> subprocess.CompletedProcess:
    logger.debug("terraform: %s (cwd=%s)", " ".join(cmd), cwd)
    if cancellation is None:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            text=True,
            capture_output=capture,
        )
    else:
        process = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            start_new_session=True,
        )
        try:
            while True:
                cancellation.raise_if_requested()
                try:
                    stdout, stderr = process.communicate(timeout=0.1)
                    proc = subprocess.CompletedProcess(
                        cmd, process.returncode, stdout, stderr
                    )
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            _terminate(process)
            raise
    if check and proc.returncode != 0:
        raise TerraformError(
            f"{' '.join(cmd)} failed (exit {proc.returncode}):\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
    return proc


class TerraformExecutor:
    """Run terraform commands in a module directory."""

    def __init__(self, module_dir: str | Path, cancellation=None):
        self.module_dir = Path(module_dir).resolve()
        self.terraform = _find_terraform()
        self.cancellation = cancellation

    def init(self) -> None:
        _run(
            [self.terraform, "init", "-input=false", "-no-color"],
            self.module_dir,
            cancellation=self.cancellation,
        )

    def apply(self, variables: Mapping[str, str]) -> None:
        cmd = [self.terraform, "apply", "-auto-approve", "-input=false", "-no-color"]
        cmd.extend(_vars_args(variables))
        # Capture so that on failure the TerraformError carries the
        # actual stderr/stdout from terraform — otherwise diagnostics
        # become impossible from the agent log alone.
        _run(
            cmd,
            self.module_dir,
            capture=True,
            cancellation=self.cancellation,
        )

    def destroy(self, variables: Mapping[str, str]) -> None:
        cmd = [self.terraform, "destroy", "-auto-approve", "-input=false", "-no-color"]
        cmd.extend(_vars_args(variables))
        _run(
            cmd,
            self.module_dir,
            capture=True,
            cancellation=self.cancellation,
        )

    def plan(self, variables: Mapping[str, str]) -> Tuple[bool, str]:
        """Run terraform plan -detailed-exitcode.

        Returns (changes_needed, output).
        exit 0 = no changes, 2 = changes, anything else = error.
        """
        cmd = [self.terraform, "plan", "-detailed-exitcode",
               "-input=false", "-no-color"]
        cmd.extend(_vars_args(variables))
        proc = _run(
            cmd,
            self.module_dir,
            check=False,
            cancellation=self.cancellation,
        )
        if proc.returncode == 0:
            return False, proc.stdout
        if proc.returncode == 2:
            return True, proc.stdout
        raise TerraformError(
            f"terraform plan failed (exit {proc.returncode}):\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}")

    def output(self) -> Dict[str, dict]:
        """Read terraform outputs as a dict of {name: {value, type, sensitive}}."""
        proc = _run(
            [self.terraform, "output", "-json", "-no-color"],
            self.module_dir,
            cancellation=self.cancellation,
        )
        if not proc.stdout.strip():
            return {}
        return json.loads(proc.stdout)
