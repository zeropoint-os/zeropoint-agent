"""Atomic graph mutation helper.

Graph mutations update the durable SQLite ledger and the in-memory DAG together.
The store transaction provides the crash-safe boundary; if any operation raises,
SQLite rolls back and the DAG is rehydrated from the committed state.
Filesystem or external-system side effects remain the responsibility of the
node performing them and must be atomic, reversible or delegated to a durable
job.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from zeropoint_agent.dag import DAG

logger = logging.getLogger(__name__)


@contextmanager
def graph_transaction(dag: DAG) -> Iterator[None]:
    """Apply a multi-step graph mutation atomically.

    Store-less DAGs remain useful in focused tests, but do not gain rollback.
    Stored DAGs use the GraphStore transaction and reload their in-memory state
    after a rollback so callers never retain partially-mutated entries.
    """
    store = dag._store
    if store is None:
        yield
        return

    try:
        with store.transaction():
            yield
    except BaseException:
        logger.warning("graph transaction failed; reloading committed state")
        try:
            dag._reload_from_store()
        except Exception:
            logger.exception("FATAL: failed to reload graph after rollback")
        raise
