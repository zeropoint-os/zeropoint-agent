"""Typed completion values for durable node resolution."""

from __future__ import annotations

import asyncio
import importlib
import time
import types
from dataclasses import asdict, dataclass, fields, is_dataclass
from enum import Enum
from typing import (
    Any, Dict, Generic, Optional, TypeVar, Union,
    get_args, get_origin, get_type_hints,
)

from zeropoint_agent.inode import (
    NodeResult,
    NodeStatus,
    ProducedNode,
    SystemdUnit,
)
from zeropoint_agent.promise_controls import (
    UnsupportedCancellation,
    UnsupportedCheckpoint,
    UnsupportedPause,
    UnsupportedProgress,
)

T = TypeVar("T")
CancellationT = TypeVar("CancellationT")
PauseT = TypeVar("PauseT")
ProgressT = TypeVar("ProgressT")
CheckpointT = TypeVar("CheckpointT")


class PromiseRejected(RuntimeError):
    pass


class PromiseCancelled(RuntimeError):
    pass


class PromiseSuperseded(RuntimeError):
    pass


class PromiseStatus(str, Enum):
    PENDING = "pending"
    RESOLVED = "resolved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class GraphRunStatus(str, Enum):
    RUNNING = "running"
    WAITING = "waiting"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


TERMINAL_PROMISE_STATUSES = {
    PromiseStatus.RESOLVED,
    PromiseStatus.REJECTED,
    PromiseStatus.CANCELLED,
    PromiseStatus.SUPERSEDED,
}

TERMINAL_RUN_NODE_STATUSES = {
    "success", "success_skip", "error", "blocked", "skipped",
    "pending_reboot", "cancelled", "superseded",
}


class Promise(Generic[T, CancellationT, PauseT, ProgressT, CheckpointT]):
    """A typed future plus operation-specific control implementations."""

    def __init__(
        self,
        *,
        promise_id: Optional[str] = None,
        value: Optional[T] = None,
        manager: Any = None,
        cancellation: CancellationT,
        pausing: PauseT,
        progress: ProgressT,
        checkpoint: CheckpointT,
    ) -> None:
        self.id = promise_id
        self.value = value
        self._manager = manager
        self.cancellation = cancellation
        self.pausing = pausing
        self.progress = progress
        self.checkpoint = checkpoint

    @classmethod
    def resolved(cls, value: T):
        return cls(
            value=value,
            cancellation=UnsupportedCancellation(),
            pausing=UnsupportedPause(),
            progress=UnsupportedProgress(),
            checkpoint=UnsupportedCheckpoint(),
        )

    @classmethod
    def pending(
        cls,
        promise_id: str,
        manager: Any,
        *,
        cancellation: CancellationT,
        pausing: PauseT,
        progress: ProgressT,
        checkpoint: CheckpointT,
    ):
        return cls(
            promise_id=promise_id,
            manager=manager,
            cancellation=cancellation,
            pausing=pausing,
            progress=progress,
            checkpoint=checkpoint,
        )

    @property
    def is_pending(self) -> bool:
        return self.id is not None

    @property
    def is_resolved(self) -> bool:
        return self.id is None

    def result(self) -> T:
        if self.is_pending:
            raise RuntimeError(f"promise {self.id} is still pending")
        return self.value  # type: ignore[return-value]

    async def wait(
        self,
        *,
        timeout: Optional[float] = None,
        poll_interval: float = 0.05,
    ) -> T:
        if self.is_resolved:
            return self.result()
        if self._manager is None:
            raise RuntimeError(f"promise {self.id} has no manager")
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            record = self._manager.promise(self.id)
            if record is None:
                raise KeyError(f"promise not found: {self.id}")
            status = PromiseStatus(record["status"])
            if status == PromiseStatus.RESOLVED:
                return self._manager.promise_result(record)
            if status == PromiseStatus.REJECTED:
                error = record.get("error") or {}
                raise PromiseRejected(error.get("message", "Promise rejected"))
            if status == PromiseStatus.CANCELLED:
                raise PromiseCancelled(f"promise cancelled: {self.id}")
            if status == PromiseStatus.SUPERSEDED:
                raise PromiseSuperseded(f"promise superseded: {self.id}")
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"promise wait timed out: {self.id}")
            await asyncio.sleep(poll_interval)


@dataclass(frozen=True)
class ResultEnvelope:
    """JSON-safe representation of a typed NodeResult."""

    status: str
    output_type: Optional[str]
    output: Any
    error: Optional[str]
    systemd_units: list[Dict[str, Any]]
    produced_nodes: list[Dict[str, Any]]

    @classmethod
    def from_result(cls, result: NodeResult[Any]) -> "ResultEnvelope":
        output_type: Optional[str] = None
        output: Any = result.output
        if output is not None:
            output_type = f"{type(output).__module__}.{type(output).__qualname__}"
            if is_dataclass(output):
                output = asdict(output)
        return cls(
            status=result.status.value,
            output_type=output_type,
            output=output,
            error=result.error,
            systemd_units=[asdict(unit) for unit in result.systemd_units],
            produced_nodes=[asdict(node) for node in result.produced_nodes],
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ResultEnvelope":
        return cls(
            status=value["status"],
            output_type=value.get("output_type"),
            output=value.get("output"),
            error=value.get("error"),
            systemd_units=list(value.get("systemd_units") or []),
            produced_nodes=list(value.get("produced_nodes") or []),
        )

    def to_result(self) -> NodeResult[Any]:
        result = NodeResult()
        result.status = NodeStatus(self.status)
        result.output = decode_output(self.output_type, self.output)
        result.error = self.error
        result.systemd_units = [SystemdUnit(**unit) for unit in self.systemd_units]
        result.produced_nodes = [
            ProducedNode(**node) for node in self.produced_nodes
        ]
        return result


def decode_output(type_name: Optional[str], value: Any) -> Any:
    if type_name is None or value is None:
        return value
    if type_name.startswith("builtins."):
        return value
    module_name, _, qualname = type_name.rpartition(".")
    if not module_name:
        return value
    try:
        target: Any = importlib.import_module(module_name)
        for part in qualname.split("."):
            target = getattr(target, part)
        return _decode_typed(target, value)
    except (ImportError, AttributeError, TypeError, ValueError):
        return value


def _decode_typed(annotation: Any, value: Any) -> Any:
    if value is None or annotation is Any:
        return value
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (types.UnionType, Union):
        for candidate in args:
            if candidate is type(None):
                continue
            try:
                return _decode_typed(candidate, value)
            except (TypeError, ValueError):
                continue
        return value
    if origin in (list, tuple, set, frozenset):
        item_type = args[0] if args else Any
        decoded = [_decode_typed(item_type, item) for item in value]
        return origin(decoded) if origin is not tuple else tuple(decoded)
    if origin is dict:
        key_type, value_type = args if len(args) == 2 else (Any, Any)
        return {
            _decode_typed(key_type, key): _decode_typed(value_type, item)
            for key, item in value.items()
        }
    if isinstance(annotation, type) and is_dataclass(annotation) and isinstance(value, dict):
        hints = get_type_hints(annotation)
        allowed = {field.name for field in fields(annotation)}
        return annotation(**{
            key: _decode_typed(hints.get(key, Any), item)
            for key, item in value.items()
            if key in allowed
        })
    return value
