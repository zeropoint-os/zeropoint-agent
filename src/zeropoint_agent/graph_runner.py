"""Persistent readiness scheduler for Promise-returning DAG nodes."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Iterable, Optional

from zeropoint_agent.dag import DAG
from zeropoint_agent.inode import NodeResult, NodeStatus, ResolveMode
from zeropoint_agent.jobs import JobManager, ResolveContext
from zeropoint_agent.promise import Promise, ResultEnvelope
from zeropoint_agent.promise_controls import (
    GraphRunCancellation,
    GraphRunCheckpoint,
    GraphRunPause,
    GraphRunProgress,
)
from zeropoint_agent.sqlite_store import SQLiteGraphStore
from zeropoint_agent.graph_transaction import graph_transaction
from zeropoint_agent.query import _get_all_descendants

logger = logging.getLogger(__name__)

GraphRunPromise = Promise[
    Dict[str, Any],
    GraphRunCancellation,
    GraphRunPause,
    GraphRunProgress,
    GraphRunCheckpoint,
]

_SUCCESS_STATUSES = {
    NodeStatus.SUCCESS.value,
    NodeStatus.PENDING_REBOOT.value,
    "dry_run",
}
_TERMINAL_STATUSES = {
    NodeStatus.SUCCESS.value,
    NodeStatus.SUCCESS_SKIP.value,
    NodeStatus.ERROR.value,
    NodeStatus.BLOCKED.value,
    NodeStatus.SKIPPED.value,
    NodeStatus.PENDING_REBOOT.value,
    "dry_run",
    "cancelled",
    "superseded",
}
_WAITING_STATUSES = {"waiting", NodeStatus.RUNNING.value}


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        _json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class GraphRunner:
    """Runs all currently-ready nodes and persists every scheduling decision."""

    def __init__(
        self,
        dag: DAG,
        store: SQLiteGraphStore,
        jobs: JobManager,
    ) -> None:
        self.dag = dag
        self.store = store
        self.jobs = jobs
        self._lock = threading.RLock()
        jobs.set_promise_callback(self.on_promise_terminal)

    def start(
        self,
        mode: ResolveMode,
        node_ids: Optional[Iterable[str]] = None,
        pattern: Optional[str] = None,
    ) -> GraphRunPromise:
        selected = set(node_ids) if node_ids is not None else set(self.dag.nodes)
        return self._start(mode, selected, pattern, operation="resolve")

    def start_remove(
        self,
        node_ids: Iterable[str],
        mode: ResolveMode,
        pattern: Optional[str] = None,
    ) -> GraphRunPromise:
        return self._start(mode, set(node_ids), pattern, operation="remove")

    def _start(
        self,
        mode: ResolveMode,
        selected: set[str],
        pattern: Optional[str],
        *,
        operation: str,
    ) -> GraphRunPromise:
        with self._lock:
            ordered = [node_id for node_id in self.dag._order if node_id in selected]
            run_id = str(uuid.uuid4())
            rows = []
            run_edges = [
                (parent_id, child_id)
                for child_id in ordered
                for parent_id in self.dag.get(child_id).parents
                if parent_id in selected
            ]
            for node_id in ordered:
                entry = self.dag.get(node_id)
                if operation == "remove":
                    dependencies = [
                        child_id for child_id in selected
                        if node_id in self.dag.get(child_id).parents
                    ]
                else:
                    dependencies = [
                        parent for parent in entry.parents if parent in selected
                    ]
                rows.append({
                    "node_id": node_id,
                    "desired_revision": self.store.get_desired_revision(node_id),
                    "remaining_parents": len(dependencies),
                    "status": "ready" if not dependencies else "pending",
                    "phase": "remove" if operation == "remove" else "verify",
                })
            self.store.create_graph_run(
                run_id,
                mode.value,
                rows,
                edges=run_edges,
                pattern=pattern,
                operation=operation,
            )
            self.advance(run_id)
            return self.handle(run_id)

    def handle(self, run_id: str) -> GraphRunPromise:
        return Promise.pending(
            run_id,
            self,
            cancellation=GraphRunCancellation(run_id, self),
            pausing=GraphRunPause(run_id, self),
            progress=GraphRunProgress(run_id, self),
            checkpoint=GraphRunCheckpoint(run_id, self),
        )

    def promise(self, run_id: str) -> Optional[Dict[str, Any]]:
        run = self.store.get_graph_run(run_id)
        if run is None:
            return None
        status = str(run["status"])
        mapped = (
            "resolved" if status == "succeeded"
            else "rejected" if status == "failed"
            else "cancelled" if status == "cancelled"
            else "superseded" if status == "superseded"
            else "pending"
        )
        return {
            "id": run_id,
            "status": mapped,
            "result": self.describe(run_id) if mapped == "resolved" else None,
            "error": {"message": run.get("error_json")}
            if mapped == "rejected" else None,
        }

    @staticmethod
    def promise_result(record: Dict[str, Any]) -> Dict[str, Any]:
        return dict(record["result"])

    @staticmethod
    def _run_id(run) -> str:
        if isinstance(run, Promise):
            if run.id is None:
                raise ValueError("resolved GraphRun Promise has no run ID")
            return run.id
        return str(run)

    def advance(self, run_id: str) -> Dict[str, Any]:
        run_id = self._run_id(run_id)
        with self._lock:
            run = self.store.get_graph_run(run_id)
            if run is None:
                raise KeyError(f"graph run not found: {run_id}")
            if run["status"] in {
                "succeeded", "failed", "cancelled", "superseded", "paused"
            }:
                return run

            mode = ResolveMode(str(run["mode"]))
            operation = str(run.get("operation") or "resolve")
            self._apply_terminal_promises(run_id, mode, operation)
            if run["status"] == "pause_requested":
                active = {
                    str(row["status"])
                    for row in self.store.get_graph_run_nodes(run_id)
                } & {"waiting", "running"}
                if not active:
                    self.store.mark_graph_run_paused(run_id)
                return self.store.get_graph_run(run_id) or {}
            made_progress = True
            while made_progress:
                made_progress = False
                rows = {
                    row["node_id"]: row
                    for row in self.store.get_graph_run_nodes(run_id)
                }
                node_order = (
                    list(reversed(self.dag._order))
                    if operation == "remove"
                    else self.dag._order
                )
                for node_id in node_order:
                    row = rows.get(node_id)
                    if row is None or row["status"] not in {"pending", "ready"}:
                        continue
                    if node_id not in self.dag.nodes:
                        continue
                    readiness = (
                        self._remove_readiness(run_id, node_id, rows)
                        if operation == "remove"
                        else self._readiness(node_id, rows)
                    )
                    if readiness == "wait":
                        continue
                    if readiness in {"blocked", "skipped"}:
                        self.store.set_run_node_status(run_id, node_id, readiness)
                        entry = self.dag.get(node_id)
                        entry.status = (
                            NodeStatus.BLOCKED if readiness == "blocked"
                            else NodeStatus.SKIPPED
                        )
                        made_progress = True
                        continue

                    entry = self.dag.get(node_id)
                    inputs = self._inputs(entry.parents)
                    phase = str(row.get("phase") or (
                        "remove" if operation == "remove" else "resolve"
                    ))
                    context = ResolveContext(
                        graph_run_id=run_id,
                        node_id=node_id,
                        graph_revision=int(run["desired_revision"]),
                        desired_revision=int(row["desired_revision"]),
                        input_fingerprint=_fingerprint(inputs),
                        phase=phase,
                        mode=mode,
                        jobs=self.jobs,
                        pattern=run.get("pattern"),
                    )
                    if phase == "verify":
                        promise = self.dag.verify_node_promise(
                            node_id, inputs, mode, context
                        )
                    elif phase == "remove":
                        promise = self.dag.remove_node_promise(
                            node_id, inputs, mode, context
                        )
                    else:
                        promise = self.dag.resolve_node_promise(
                            node_id, inputs, mode, context
                        )
                    if promise.is_pending:
                        self.store.set_run_node_status(run_id, node_id, "waiting")
                    else:
                        result = promise.result()
                        envelope = ResultEnvelope.from_result(result)
                        if phase == "verify" and result.status != NodeStatus.SUCCESS:
                            self.store.set_run_node_verification(
                                run_id, node_id, envelope.to_dict()
                            )
                            entry.status = NodeStatus.PENDING
                            entry.error = None
                        else:
                            status = self._publish_node_result(
                                run_id,
                                node_id,
                                row,
                                result,
                                mode,
                                phase,
                                delete_node_on_success=(phase == "remove"),
                                run_status=(
                                    "dry_run"
                                    if mode == ResolveMode.DRY_RUN
                                    and phase == "resolve"
                                    and result.status == NodeStatus.PENDING
                                    else None
                                ),
                            )
                            if status == "superseded":
                                entry.status = NodeStatus.PENDING
                                entry.output = None
                                entry.error = None
                            elif (
                                phase == "remove"
                                and result.status in {
                                    NodeStatus.SUCCESS,
                                    NodeStatus.SUCCESS_SKIP,
                                }
                            ):
                                self._forget_removed_node(node_id)
                    made_progress = True

            self._update_run_status(run_id)
            return self.store.get_graph_run(run_id) or {}

    def on_promise_terminal(self, run_id: str, promise_id: str) -> None:
        try:
            self.advance(run_id)
        except Exception:
            logger.exception(
                "failed to resume graph run %s for promise %s",
                run_id,
                promise_id,
            )

    def cancel(self, run_id: str) -> str:
        run_id = self._run_id(run_id)
        status = self.store.request_graph_run_cancellation(run_id)
        self.advance(run_id)
        return status

    def pause(self, run_id: str) -> str:
        run_id = self._run_id(run_id)
        status = self.store.request_graph_run_pause(run_id)
        self.advance(run_id)
        return status

    def resume(self, run_id: str) -> str:
        run_id = self._run_id(run_id)
        status = self.store.resume_graph_run(run_id)
        if status == "running":
            self.advance(run_id)
        return status

    def describe(self, run_id: str) -> Dict[str, Any]:
        run_id = self._run_id(run_id)
        run = self.store.get_graph_run(run_id)
        if run is None:
            raise KeyError(f"graph run not found: {run_id}")
        nodes = self.store.get_graph_run_nodes(run_id)
        summary: Dict[str, int] = {}
        for node in nodes:
            status = str(node["status"])
            summary[status] = summary.get(status, 0) + 1
        return {**run, "summary": summary, "nodes": nodes}

    def resume_incomplete(self) -> None:
        for run_id in self.store.list_incomplete_graph_runs():
            try:
                self.advance(run_id)
            except Exception:
                logger.exception("failed to resume graph run %s", run_id)

    def _apply_terminal_promises(
        self,
        run_id: str,
        mode: ResolveMode,
        operation: str,
    ) -> None:
        for row in self.store.get_graph_run_nodes(run_id):
            if row["status"] != "waiting" or not row["promise_id"]:
                continue
            promise = self.store.get_promise(str(row["promise_id"]))
            if promise is None or promise["status"] == "pending":
                continue
            node_id = str(row["node_id"])
            if promise["status"] == "resolved" and promise["result"]:
                envelope = ResultEnvelope.from_dict(promise["result"])
                result = envelope.to_result()
                phase = str(row.get("phase") or (
                    "remove" if operation == "remove" else "resolve"
                ))
                if phase == "verify" and result.status != NodeStatus.SUCCESS:
                    self.store.set_run_node_verification(
                        run_id, node_id, envelope.to_dict()
                    )
                    entry = self.dag.get(node_id)
                    entry.status = NodeStatus.PENDING
                    entry.error = None
                    continue
                status = self._publish_node_result(
                    run_id,
                    node_id,
                    row,
                    result,
                    mode,
                    phase,
                    delete_node_on_success=(phase == "remove"),
                )
                if status == "superseded":
                    entry = self.dag.get(node_id)
                    entry.status = NodeStatus.PENDING
                    entry.output = None
                    entry.error = None
                elif (
                    phase == "remove"
                    and result.status in {
                        NodeStatus.SUCCESS,
                        NodeStatus.SUCCESS_SKIP,
                    }
                ):
                    self._forget_removed_node(node_id)
                continue
            if promise["status"] == "cancelled":
                self.store.set_run_node_status(run_id, node_id, "cancelled")
                self.store.clear_node_promise(node_id)
                self.dag.get(node_id).status = NodeStatus.PENDING
            elif promise["status"] == "superseded":
                self.store.set_run_node_status(run_id, node_id, "superseded")
                self.store.clear_node_promise(node_id)
                self.dag.get(node_id).status = NodeStatus.PENDING
            else:
                error = promise.get("error") or {}
                message = error.get("message", "Promise rejected")
                result = NodeResult.failed(message)
                self._publish_node_result(
                    run_id,
                    node_id,
                    row,
                    result,
                    mode,
                    str(row.get("phase") or (
                        "remove" if operation == "remove" else "resolve"
                    )),
                )

    def _publish_node_result(
        self,
        run_id: str,
        node_id: str,
        row: Dict[str, Any],
        result: NodeResult[Any],
        mode: ResolveMode,
        phase: str,
        *,
        delete_node_on_success: bool = False,
        run_status: Optional[str] = None,
    ) -> str:
        """Atomically publish a node result and its produced child subgraph."""
        envelope = ResultEnvelope.from_result(result)
        reconciliation = None
        desired_revision = int(row["desired_revision"])
        with graph_transaction(self.dag):
            current = (
                self.store.get_desired_revision(node_id)
                if self.store.has_node(node_id)
                else None
            )
            can_reconcile = (
                current == desired_revision
                and phase == "resolve"
                and mode != ResolveMode.DRY_RUN
                and result.status in {
                    NodeStatus.SUCCESS,
                    NodeStatus.SUCCESS_SKIP,
                    NodeStatus.PENDING_REBOOT,
                }
            )
            if can_reconcile:
                reconciliation = self.dag.reconcile_produced(
                    node_id,
                    result.produced_nodes,
                )
            status = self.store.set_run_node_result(
                run_id,
                node_id,
                desired_revision,
                envelope.to_dict(),
                delete_node_on_success=delete_node_on_success,
                run_status=run_status,
            )
            if (
                status != "superseded"
                and phase in {"verify", "resolve"}
                and node_id in self.dag.nodes
                and self.dag.get(node_id).node.on_result_applied(result, mode)
            ):
                self.store.set_runtime_config(
                    node_id,
                    self.dag._node_config(self.dag.get(node_id).node),
                )
            if status != "superseded" and reconciliation is not None and (
                reconciliation.changed or reconciliation.removed
            ):
                self._sync_run_after_graph_mutation(
                    run_id,
                    reconciliation.changed,
                    reconciliation.removed,
                )

        if status != "superseded" and phase != "remove":
            self.dag.apply_promise_result(node_id, result, mode)
        return status

    def _sync_run_after_graph_mutation(
        self,
        run_id: str,
        changed_node_ids: set[str],
        removed_node_ids: set[str],
    ) -> None:
        affected = set(changed_node_ids)
        for node_id in tuple(changed_node_ids):
            if node_id in self.dag.nodes:
                affected.update(_get_all_descendants(self.dag, node_id))
        affected.difference_update(removed_node_ids)

        current_rows = self.store.get_graph_run_nodes(run_id)
        selected = {
            str(row["node_id"]) for row in current_rows
        } - set(removed_node_ids)
        selected.update(
            node_id for node_id in affected if node_id in self.dag.nodes
        )
        pending_parents = list(affected)
        while pending_parents:
            node_id = pending_parents.pop()
            if node_id not in self.dag.nodes:
                continue
            for parent_id in self.dag.get(node_id).parents:
                if parent_id in selected:
                    continue
                parent = self.dag.get(parent_id)
                if parent.status.value in _SUCCESS_STATUSES:
                    continue
                selected.add(parent_id)
                affected.add(parent_id)
                pending_parents.append(parent_id)
        rows = []
        for node_id in self.dag._order:
            if node_id not in affected or node_id not in selected:
                continue
            entry = self.dag.get(node_id)
            dependencies = [
                parent_id for parent_id in entry.parents
                if parent_id in selected
            ]
            rows.append({
                "node_id": node_id,
                "desired_revision": self.store.get_desired_revision(node_id),
                "remaining_parents": len(dependencies),
                "status": "ready" if not dependencies else "pending",
            })
        edges = [
            (parent_id, child_id)
            for child_id in selected
            if child_id in self.dag.nodes
            for parent_id in self.dag.get(child_id).parents
            if parent_id in selected
        ]
        self.store.sync_graph_run(
            run_id,
            rows,
            edges,
            sorted(removed_node_ids),
        )

    def _forget_removed_node(self, node_id: str) -> None:
        try:
            self.dag.remove(node_id, persist=False)
        except Exception:
            # SQLite already committed the removal. Rebuild memory from the
            # durable graph before propagating the local failure.
            self.dag._reload_from_store()
            raise

    def _readiness(self, node_id: str, rows: Dict[str, Dict[str, Any]]) -> str:
        entry = self.dag.get(node_id)
        for parent_id in entry.parents:
            parent_row = rows.get(parent_id)
            status = (
                str(parent_row["status"])
                if parent_row is not None
                else self.dag.get(parent_id).status.value
            )
            if status in {"pending", "ready", "waiting", "running"}:
                return "wait"
            if status in {NodeStatus.SKIPPED.value, NodeStatus.SUCCESS_SKIP.value}:
                return "skipped"
            if status not in _SUCCESS_STATUSES:
                return "blocked"
        return "ready"

    def _remove_readiness(
        self,
        run_id: str,
        node_id: str,
        rows: Dict[str, Dict[str, Any]],
    ) -> str:
        children = [
            child_id
            for parent_id, child_id in self.store.get_graph_run_edges(run_id)
            if parent_id == node_id
        ]
        for child_id in children:
            status = str(rows[child_id]["status"])
            if status in {"pending", "ready", "waiting", "running"}:
                return "wait"
            if status not in {
                NodeStatus.SUCCESS.value,
                NodeStatus.SUCCESS_SKIP.value,
            }:
                return "blocked"
        return "ready"

    def _inputs(self, parents: list[str]) -> Any:
        if not parents:
            return None
        if len(parents) == 1:
            return self.dag.get(parents[0]).output
        return {parent: self.dag.get(parent).output for parent in parents}

    def _update_run_status(self, run_id: str) -> None:
        run = self.store.get_graph_run(run_id)
        if run is None:
            return
        rows = self.store.get_graph_run_nodes(run_id)
        statuses = {str(row["status"]) for row in rows}
        if run["status"] == "cancel_requested":
            active = statuses & {"pending", "ready", "waiting", "running"}
            if not active:
                self.store.set_graph_run_status(run_id, "cancelled")
            return
        if statuses & _WAITING_STATUSES:
            self.store.set_graph_run_status(run_id, "waiting")
        elif statuses <= _TERMINAL_STATUSES:
            if "superseded" in statuses:
                final = "superseded"
            elif "cancelled" in statuses:
                final = "cancelled"
            elif statuses & {"error", "blocked"}:
                final = "failed"
            else:
                final = "succeeded"
            self.store.set_graph_run_status(run_id, final)
        else:
            self.store.set_graph_run_status(run_id, "running")
