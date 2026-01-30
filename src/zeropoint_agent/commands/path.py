"""Path commands: Add, Edit, Delete.

Path operations:
- AddPath: Create a directory with permissions
- EditPath: Modify directory permissions
- DeletePath: Remove a directory
"""

from typing import Dict, Any
from . import Command, CommandResult, CommandStatus


class PathCommand(Command):
    """Base class for path operations."""

    def __init__(self, name: str):
        super().__init__(f"path:{name}")


class AddPath(PathCommand):
    """Add a path (directory)."""

    def __init__(self):
        super().__init__("add")

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Add a path.
        
        Expects resource with:
        - id: path ID
        - mount_id: ID of the mount (for reference/ordering)
        - path: /mnt/media/photos, /var/data/cache, etc.
        - mode: permissions (0755, 0777, etc.)
        """
        path = resource.get("path")
        mode = resource.get("mode", "0755")

        if not path:
            return CommandResult(
                status=CommandStatus.FAILED,
                error="path is required"
            )

        try:
            # Create directory
            self._run_bash(f"mkdir -p {path}", check=True)

            # Set permissions
            self._run_bash(f"chmod {mode} {path}", check=True)

            return CommandResult(
                status=CommandStatus.APPLIED,
                output={"path": path, "mode": mode}
            )

        except Exception as e:
            return CommandResult(
                status=CommandStatus.FAILED,
                error=str(e)
            )


class EditPath(PathCommand):
    """Edit path permissions."""

    def __init__(self):
        super().__init__("edit")

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Edit path permissions."""
        path = resource.get("path")
        mode = resource.get("mode", "0755")

        if not path:
            return CommandResult(
                status=CommandStatus.FAILED,
                error="path is required"
            )

        try:
            # Check if path exists
            self._run_bash(f"test -d {path}", check=True)

            # Update permissions
            self._run_bash(f"chmod {mode} {path}", check=True)

            return CommandResult(
                status=CommandStatus.APPLIED,
                output={"path": path, "mode": mode}
            )

        except Exception as e:
            return CommandResult(
                status=CommandStatus.FAILED,
                error=str(e)
            )


class DeletePath(PathCommand):
    """Delete a path (directory)."""

    def __init__(self):
        super().__init__("delete")

    def execute(self, resource: Dict[str, Any]) -> CommandResult:
        """Delete a path."""
        path = resource.get("path")

        if not path:
            return CommandResult(
                status=CommandStatus.FAILED,
                error="path is required"
            )

        try:
            # Check if path exists
            exists = self._run_bash(
                f"test -d {path} && echo 1 || echo 0",
                check=False
            ).strip()

            if exists != "1":
                # Path doesn't exist, nothing to do
                return CommandResult(status=CommandStatus.APPLIED)

            # Check if it's empty or has contents
            # For safety, only delete if empty
            is_empty = self._run_bash(
                f"[ -z \"$(ls -A {path})\" ] && echo 1 || echo 0",
                check=False
            ).strip()

            if is_empty != "1":
                return CommandResult(
                    status=CommandStatus.FAILED,
                    error=f"Directory {path} is not empty"
                )

            # Delete
            self._run_bash(f"rmdir {path}", check=True)

            return CommandResult(status=CommandStatus.APPLIED)

        except Exception as e:
            return CommandResult(
                status=CommandStatus.FAILED,
                error=str(e)
            )
