"""Operation-specific Promise control implementations."""

from __future__ import annotations

from typing import Any, Dict, Protocol, TypeVar

CheckpointT = TypeVar("CheckpointT")


class PromiseControls(Protocol):
    key: str


class CancellationControls(PromiseControls, Protocol):
    async def cancel(self) -> Dict[str, Any]: ...


class PauseControls(PromiseControls, Protocol[CheckpointT]):
    async def pause(self) -> Dict[str, Any]: ...
    async def resume(self) -> Dict[str, Any]: ...


class ProgressControls(PromiseControls, Protocol):
    async def current(self) -> Dict[str, Any]: ...


class CheckpointControls(PromiseControls, Protocol[CheckpointT]):
    async def current(self) -> CheckpointT: ...


class UnsupportedCancellation:
    key = "unsupported"

    def __init__(self, promise_id: str = "", manager: Any = None):
        self.promise_id = promise_id
        self.manager = manager


class UnsupportedPause:
    key = "unsupported"

    def __init__(self, promise_id: str = "", manager: Any = None):
        self.promise_id = promise_id
        self.manager = manager


class UnsupportedProgress:
    key = "unsupported"

    def __init__(self, promise_id: str = "", manager: Any = None):
        self.promise_id = promise_id
        self.manager = manager


class UnsupportedCheckpoint:
    key = "unsupported"

    def __init__(self, promise_id: str = "", manager: Any = None):
        self.promise_id = promise_id
        self.manager = manager


class JobCancellation:
    """Generic cancellation control for durable interactive Jobs."""

    key = "job-cancellation"

    def __init__(self, promise_id: str, manager: Any):
        self.promise_id = promise_id
        self.manager = manager

    async def cancel(self) -> Dict[str, Any]:
        status = self.manager.cancel_promise(self.promise_id)
        return {"promise_id": self.promise_id, "status": status}


class JobProgress:
    """Generic durable Job progress reader."""

    key = "job-progress"

    def __init__(self, promise_id: str, manager: Any):
        self.promise_id = promise_id
        self.manager = manager

    async def current(self) -> Dict[str, Any]:
        return self.manager.promise_progress(self.promise_id)


class JobCheckpoint:
    """Generic durable Job checkpoint reader."""

    key = "job-checkpoint"

    def __init__(self, promise_id: str, manager: Any):
        self.promise_id = promise_id
        self.manager = manager

    async def current(self) -> Dict[str, Any] | None:
        return self.manager.promise_progress(self.promise_id).get("checkpoint")


class ShellScriptCancellation:
    key = "shell-process-group"

    def __init__(self, promise_id: str, manager: Any):
        self.promise_id = promise_id
        self.manager = manager

    async def cancel(self) -> Dict[str, Any]:
        status = self.manager.cancel_promise(self.promise_id)
        return {"promise_id": self.promise_id, "status": status}


class ShellScriptProgress:
    key = "shell-phases"

    def __init__(self, promise_id: str, manager: Any):
        self.promise_id = promise_id
        self.manager = manager

    async def current(self) -> Dict[str, Any]:
        return self.manager.promise_progress(self.promise_id)


class TerraformCancellation:
    key = "terraform-interrupt"

    def __init__(self, promise_id: str, manager: Any):
        self.promise_id = promise_id
        self.manager = manager

    async def cancel(self) -> Dict[str, Any]:
        status = self.manager.cancel_promise(self.promise_id)
        return {"promise_id": self.promise_id, "status": status}


class TerraformProgress:
    key = "terraform-phases"

    def __init__(self, promise_id: str, manager: Any):
        self.promise_id = promise_id
        self.manager = manager

    async def current(self) -> Dict[str, Any]:
        return self.manager.promise_progress(self.promise_id)


class HardwareCancellation:
    key = "hardware-operation"

    def __init__(self, promise_id: str, manager: Any):
        self.promise_id = promise_id
        self.manager = manager

    async def cancel(self) -> Dict[str, Any]:
        status = self.manager.cancel_promise(self.promise_id)
        return {"promise_id": self.promise_id, "status": status}


class HardwareProgress:
    key = "hardware-phases"

    def __init__(self, promise_id: str, manager: Any):
        self.promise_id = promise_id
        self.manager = manager

    async def current(self) -> Dict[str, Any]:
        return self.manager.promise_progress(self.promise_id)


class GraphRunCancellation:
    key = "graph-run"

    def __init__(self, promise_id: str, runner: Any):
        self.promise_id = promise_id
        self.runner = runner

    async def cancel(self) -> Dict[str, Any]:
        status = self.runner.cancel(self.promise_id)
        return {"run_id": self.promise_id, "status": status}


class GraphRunPause:
    key = "scheduler-boundary"

    def __init__(self, promise_id: str, runner: Any):
        self.promise_id = promise_id
        self.runner = runner

    async def pause(self) -> Dict[str, Any]:
        status = self.runner.pause(self.promise_id)
        return {"run_id": self.promise_id, "status": status}

    async def resume(self) -> Dict[str, Any]:
        status = self.runner.resume(self.promise_id)
        return {"run_id": self.promise_id, "status": status}


class GraphRunProgress:
    key = "node-counts"

    def __init__(self, promise_id: str, runner: Any):
        self.promise_id = promise_id
        self.runner = runner

    async def current(self) -> Dict[str, Any]:
        return self.runner.describe(self.promise_id)


class GraphRunCheckpoint:
    key = "run-ledger"

    def __init__(self, promise_id: str, runner: Any):
        self.promise_id = promise_id
        self.runner = runner

    async def current(self) -> Dict[str, Any]:
        return self.runner.describe(self.promise_id)
