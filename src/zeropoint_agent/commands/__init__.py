"""Command base class and interfaces.

Commands execute state transitions and return status.
Each command:
- Takes input (resource dict)
- Executes via bash/systemd/terraform
- Returns status: applied, blocked, or failed
- Is idempotent (same input always produces same result)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any
import json
import subprocess


class CommandStatus(str, Enum):
    """Result of command execution."""
    APPLIED = "applied"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass
class CommandResult:
    """Result of executing a command."""
    status: CommandStatus
    reason: Optional[str] = None
    error: Optional[str] = None
    output: Optional[Dict[str, Any]] = None  # Serialized output (e.g., terraform outputs)


class Command:
    """Base class for all commands.
    
    Commands are idempotent state transitions. Each command:
    - Is named (e.g., 'add_disk', 'remove_mount')
    - Takes a resource dict as input
    - Returns CommandResult with status and optional error
    """

    def __init__(self, name: str):
        self.name = name

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Execute the command.
        
        Args:
            resource: Resource dict (e.g., disk, mount, path)
            
        Returns:
            CommandResult with status
        """
        raise NotImplementedError

    def _run_bash(self, script: str, check: bool = True) -> str:
        """Execute a bash script and return stdout.
        
        Args:
            script: Bash script to run
            check: If True, raise on non-zero exit
            
        Returns:
            Stdout as string
            
        Raises:
            RuntimeError if command fails and check=True
        """
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0 and check:
            raise RuntimeError(
                f"Command failed: {script}\nStderr: {result.stderr}\nStdout: {result.stdout}"
            )

        return result.stdout.strip()
