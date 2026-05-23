"""Graph transaction — atomic, rollback-on-failure for graph mutations.

Any handler that performs a multi-step graph mutation (a PUT that
fires a node hook AND writes config + perms; a module install that
creates a namespace + many Vars + a Terraform; a delete
cascade) wraps its work in `graph_transaction`. The transaction
snapshots `graph.db` before the work runs and restores it if anything
raises.

This means **the graph either fully reflects a mutation or doesn't
reflect it at all** — a partially-applied state is never persisted.

The transaction's scope is the graph store. Filesystem side effects
(e.g. DirectoryVar directory moves) are the responsibility of the
node's `on_config_changed` hook to make atomic-or-revertible; the
transaction's job is the graph store and the in-memory DAG.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from zeropoint_agent.dag import DAG

logger = logging.getLogger(__name__)


@contextmanager
def graph_transaction(dag: DAG) -> Iterator[None]:
    """Atomic transaction over the graph store.

    Usage:
        with graph_transaction(request.app.state.dag):
            # ... do mutation work; may raise

    On entry: snapshots `graph.db` (and any sidecar files) to a sibling
    directory.

    On exception inside the block:
      1. Restore `graph.db` from the snapshot.
      2. Reload the in-memory DAG so it matches disk.
      3. Re-raise the original exception unchanged.

    The DAG instance the caller holds is mutated in place by the
    reload, so any reference outside this transaction sees the
    rolled-back state.

    On success: discard the snapshot.
    """
    store = dag._store
    if store is None:
        # No store — nothing to snapshot. Run the block directly;
        # callers shouldn't rely on rollback when the agent is
        # running in store-less mode.
        yield
        return

    snapshot_path = f"{store.db_path}.snapshot"
    store.snapshot_to(snapshot_path)
    try:
        yield
    except Exception:
        logger.warning("graph transaction failed; rolling back from snapshot")
        try:
            store.restore_from(snapshot_path)
            # Replace in-memory DAG state. We mutate `dag` in place so
            # any caller still holding a reference sees the rollback.
            dag._reload_from_store()
        except Exception:
            # If restore itself fails the graph is in an undefined
            # state; this is a hard failure we can only log loudly.
            logger.exception(
                "FATAL: failed to restore graph from snapshot at %s",
                snapshot_path)
        raise
    finally:
        store.discard_snapshot(snapshot_path)
