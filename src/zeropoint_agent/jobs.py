"""Durable local job execution backed by SQLite and the outbox."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from zeropoint_agent.inode import NodeResult, ResolveMode
from zeropoint_agent.promise import Promise, ResultEnvelope
from zeropoint_agent.promise_controls import (
    UnsupportedCancellation,
    UnsupportedCheckpoint,
    UnsupportedPause,
    UnsupportedProgress,
)
from zeropoint_agent.sqlite_store import SQLiteGraphStore

logger = logging.getLogger(__name__)

JobExecutor = Callable[["JobContext", Dict[str, Any]], NodeResult[Any]]
PromiseCallback = Callable[[str, str], None]
ControlFactory = type


@dataclass(frozen=True)
class ExecutorDefinition:
    execute: JobExecutor
    cancellation: ControlFactory = UnsupportedCancellation
    pausing: ControlFactory = UnsupportedPause
    progress: ControlFactory = UnsupportedProgress
    checkpoint: ControlFactory = UnsupportedCheckpoint


def _timestamp_after(seconds: float) -> str:
    value = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class JobCancelled(RuntimeError):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def raise_if_requested(self) -> None:
        if self.requested:
            raise JobCancelled("job cancellation requested")


@dataclass(frozen=True)
class JobSpec:
    kind: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    resources: list[str] = field(default_factory=list)
    max_attempts: int = 1
    timeout_seconds: Optional[float] = None
    idempotency_key: Optional[str] = None

    def payload(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "arguments": self.arguments,
            "resources": self.resources,
        }


@dataclass(frozen=True)
class ResolveContext:
    graph_run_id: str
    node_id: str
    graph_revision: int
    desired_revision: int
    input_fingerprint: str
    phase: str
    mode: ResolveMode
    jobs: "JobManager"
    pattern: Optional[str] = None


class JobContext:
    def __init__(
        self,
        manager: "JobManager",
        job_id: str,
        worker_id: str,
        token: CancellationToken,
    ) -> None:
        self.manager = manager
        self.job_id = job_id
        self.worker_id = worker_id
        self.cancellation = token

    def progress(
        self,
        value: float,
        checkpoint: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.cancellation.raise_if_requested()
        self.manager.report_progress(
            self.job_id, self.worker_id, value, checkpoint
        )


class JobManager:
    """Single-writer job manager with at-least-once outbox dispatch."""

    def __init__(
        self,
        store: SQLiteGraphStore,
        *,
        max_workers: int = 4,
        poll_interval: float = 0.1,
        lease_seconds: float = 30.0,
    ) -> None:
        self.store = store
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self.owner_id = f"agent-{uuid.uuid4()}"
        self._executors: Dict[str, ExecutorDefinition] = {}
        self._tokens: Dict[str, CancellationToken] = {}
        self._timers: Dict[str, threading.Timer] = {}
        self._promise_callback: Optional[PromiseCallback] = None
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="zeropoint-job",
        )
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._dispatcher = threading.Thread(
            target=self._dispatch_loop,
            name="zeropoint-outbox",
            daemon=True,
        )
        self._started = False

    def register_executor(
        self,
        kind: str,
        executor: JobExecutor,
        *,
        cancellation: ControlFactory = UnsupportedCancellation,
        pausing: ControlFactory = UnsupportedPause,
        progress: ControlFactory = UnsupportedProgress,
        checkpoint: ControlFactory = UnsupportedCheckpoint,
    ) -> None:
        if kind in self._executors:
            raise ValueError(f"job executor already registered: {kind}")
        self._executors[kind] = ExecutorDefinition(
            execute=executor,
            cancellation=cancellation,
            pausing=pausing,
            progress=progress,
            checkpoint=checkpoint,
        )

    def set_promise_callback(self, callback: PromiseCallback) -> None:
        self._promise_callback = callback

    def start(self) -> None:
        if self._started:
            return
        self.store.recover_incomplete_jobs()
        self._started = True
        self._dispatcher.start()
        self._wake.set()

    def stop(self, wait: bool = True) -> None:
        if not self._started:
            return
        self._stop.set()
        self._wake.set()
        self._dispatcher.join(timeout=5 if wait else 0)
        for token in list(self._tokens.values()):
            token.request()
        for timer in list(self._timers.values()):
            timer.cancel()
        self._pool.shutdown(wait=wait, cancel_futures=False)
        self._started = False

    def submit(
        self,
        context: ResolveContext,
        spec: JobSpec,
    ) -> Promise[NodeResult[Any], Any, Any, Any, Any]:
        definition = self._executors.get(spec.kind)
        if definition is None:
            raise ValueError(f"unknown job executor: {spec.kind}")
        payload = spec.payload()
        key = (
            f"{context.mode.value}:{spec.idempotency_key}"
            if spec.idempotency_key is not None
            else self._idempotency_key(context, payload)
        )
        promise_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        actual_promise = self.store.create_job_request(
            job_id=job_id,
            promise_id=promise_id,
            run_id=context.graph_run_id,
            node_id=context.node_id,
            desired_revision=context.desired_revision,
            input_fingerprint=context.input_fingerprint,
            operation_phase=context.phase,
            idempotency_key=key,
            executor=spec.kind,
            cancellation_kind=definition.cancellation.key,
            pause_kind=definition.pausing.key,
            progress_kind=definition.progress.key,
            checkpoint_kind=definition.checkpoint.key,
            spec=payload,
            max_attempts=spec.max_attempts,
            timeout_seconds=spec.timeout_seconds,
            reuse_terminal=spec.idempotency_key is not None,
        )
        self._wake.set()
        return self._promise_handle(actual_promise, definition)

    def _promise_handle(
        self,
        promise_id: str,
        definition: ExecutorDefinition,
    ):
        return Promise.pending(
            promise_id,
            self,
            cancellation=definition.cancellation(promise_id, self),
            pausing=definition.pausing(promise_id, self),
            progress=definition.progress(promise_id, self),
            checkpoint=definition.checkpoint(promise_id, self),
        )

    def promise_handle(self, promise_id: str):
        record = self.store.get_promise(promise_id)
        if record is None:
            raise KeyError(f"promise not found: {promise_id}")
        definition = self._executors.get(str(record.get("producer") or ""))
        if definition is None:
            raise RuntimeError(
                f"promise producer is not registered: {record.get('producer')}"
            )
        return self._promise_handle(promise_id, definition)

    def cancel_job(self, job_id: str) -> str:
        status = self.store.request_job_cancellation(job_id)
        if status == "cancel_requested":
            token = self._tokens.get(job_id)
            if token is not None:
                token.request()
        self._wake.set()
        return status

    def cancel_promise(self, promise_id: str) -> str:
        job = self.store.get_job_for_promise(promise_id)
        if job is None:
            promise = self.store.get_promise(promise_id)
            if promise is None:
                raise KeyError(f"promise not found: {promise_id}")
            return str(promise["status"])
        return self.cancel_job(str(job["id"]))

    def promise(self, promise_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_promise(promise_id)

    @staticmethod
    def promise_result(record: Dict[str, Any]) -> NodeResult[Any]:
        return ResultEnvelope.from_dict(record["result"]).to_result()

    def promises(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[Dict[str, Any]]:
        return self.store.list_promises(status=status, limit=limit)

    def job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_job(job_id)

    def jobs(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[Dict[str, Any]]:
        return self.store.list_jobs(status=status, limit=limit)

    def promise_progress(self, promise_id: str) -> Dict[str, Any]:
        promise = self.store.get_promise(promise_id)
        if promise is None:
            raise KeyError(f"promise not found: {promise_id}")
        job = self.store.get_job_for_promise(promise_id)
        return {
            "promise_id": promise_id,
            "status": promise["status"],
            "progress": job.get("progress", 0) if job else 1,
            "checkpoint": job.get("checkpoint") if job else None,
            "controls": {
                "cancellation": promise.get("cancellation_kind"),
                "pause": promise.get("pause_kind"),
                "progress": promise.get("progress_kind"),
                "checkpoint": promise.get("checkpoint_kind"),
            },
        }

    def report_progress(
        self,
        job_id: str,
        worker_id: str,
        progress: float,
        checkpoint: Optional[Dict[str, Any]],
    ) -> None:
        progress = max(0.0, min(1.0, float(progress)))
        updated = self.store.update_job_progress(
            job_id,
            worker_id,
            progress,
            checkpoint,
            _timestamp_after(self.lease_seconds),
        )
        if not updated:
            raise RuntimeError(f"job lease lost: {job_id}")

    @staticmethod
    def _idempotency_key(
        context: ResolveContext,
        payload: Dict[str, Any],
    ) -> str:
        encoded = json.dumps(
            {
                "node_id": context.node_id,
                "desired_revision": context.desired_revision,
                "input_fingerprint": context.input_fingerprint,
                "phase": context.phase,
                "mode": context.mode.value,
                "job": payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.poll_interval)
            self._wake.clear()
            try:
                self.store.heartbeat_worker(
                    self.owner_id,
                    _timestamp_after(self.lease_seconds),
                )
                events = self.store.claim_outbox(
                    self.owner_id,
                    _timestamp_after(self.lease_seconds),
                )
                for event in events:
                    self._dispatch_event(event)
            except Exception:
                logger.exception("outbox dispatch failed")
                time.sleep(self.poll_interval)

    def _dispatch_event(self, event: Dict[str, Any]) -> None:
        event_type = str(event["event_type"])
        sequence = int(event["sequence"])
        aggregate_id = str(event["aggregate_id"])
        try:
            if event_type == "job.requested":
                claimed = self.store.claim_job(
                    aggregate_id,
                    self.owner_id,
                    _timestamp_after(self.lease_seconds),
                )
                if claimed is None:
                    job = self.store.get_job(aggregate_id)
                    if job is not None and job["status"] == "queued":
                        self.store.release_outbox_claim(
                            sequence,
                            self.owner_id,
                            _timestamp_after(self.poll_interval),
                        )
                        return
                else:
                    self._launch(claimed)
            elif event_type in {"job.cancel_requested", "job.superseded"}:
                token = self._tokens.get(aggregate_id)
                if token is not None:
                    token.request()
            elif event_type.startswith("promise."):
                if self._promise_callback is not None:
                    for run_id in self.store.get_runs_waiting_on_promise(
                        aggregate_id
                    ):
                        self._promise_callback(run_id, aggregate_id)
            self.store.mark_outbox_dispatched(sequence, self.owner_id)
        except Exception:
            logger.exception("failed to dispatch outbox event %s", event_type)
            self.store.release_outbox_claim(
                sequence,
                self.owner_id,
                _timestamp_after(self.poll_interval),
            )

    def _launch(self, job: Dict[str, Any]) -> None:
        job_id = str(job["id"])
        token = CancellationToken()
        self._tokens[job_id] = token
        timeout = job.get("timeout_seconds")
        if timeout is not None:
            timer = threading.Timer(float(timeout), self.cancel_job, args=(job_id,))
            timer.daemon = True
            self._timers[job_id] = timer
            timer.start()
        self._pool.submit(self._execute, job, token)

    def _execute(self, job: Dict[str, Any], token: CancellationToken) -> None:
        job_id = str(job["id"])
        try:
            definition = self._executors.get(str(job["executor"]))
            if definition is None:
                raise RuntimeError(f"unknown job executor: {job['executor']}")
            context = JobContext(self, job_id, self.owner_id, token)
            token.raise_if_requested()
            result = definition.execute(
                context,
                dict(job["spec"].get("arguments") or {}),
            )
            # Completion wins if the executor reached a truthful terminal
            # result before cancellation took effect.
            completion = self.store.complete_job(
                job_id,
                self.owner_id,
                ResultEnvelope.from_result(result).to_dict(),
            )
            logger.debug("job %s completed: %s", job_id, completion["status"])
        except JobCancelled as error:
            completion = self.store.fail_job(
                job_id,
                self.owner_id,
                {"type": "cancelled", "message": str(error)},
                retryable=False,
            )
            logger.debug("job %s cancelled: %s", job_id, completion["status"])
        except Exception as error:
            logger.exception("job %s failed", job_id)
            try:
                self.store.fail_job(
                    job_id,
                    self.owner_id,
                    {"type": type(error).__name__, "message": str(error)},
                    retryable=True,
                )
            except Exception:
                logger.exception("failed to persist failure for job %s", job_id)
        finally:
            timer = self._timers.pop(job_id, None)
            if timer is not None:
                timer.cancel()
            self._tokens.pop(job_id, None)
            self._wake.set()
