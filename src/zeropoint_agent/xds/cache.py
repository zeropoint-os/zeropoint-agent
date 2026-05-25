"""xDS cache — stateful sink for per-Service slices.

Each `Service.resolve()` writes its slice into this cache via
`set(id, ResolvedEndpoint)` or `clear(id)`. The cache supports
begin/commit semantics so a resolve cycle can atomically rebuild
the cache without flashing an empty snapshot mid-cycle:

  cache.begin()           # start staging; live state unchanged
  cache.set(id, ep)       # write into staging
  cache.commit()          # atomic swap staging -> live; flush

After commit the flush task ships a fresh Snapshot to connected
Envoys and reconciles mDNS in lockstep. Services that didn't write
during the cycle (deleted, errored, no child Endpoint) are simply
absent from staging and therefore dropped on commit. The graph IS
the source of truth; the cache is just the last resolve's output.

Outside a begin/commit pair, set/clear write directly to live state
(useful for tests and ad-hoc updates).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional, TYPE_CHECKING

from zeropoint_agent.xds.snapshot import ResolvedEndpoint, build_snapshot

if TYPE_CHECKING:
    from zeropoint_agent.xds.server import AdsServer

logger = logging.getLogger(__name__)


class XdsCache:
    """In-memory cache of {id: ResolvedEndpoint} feeding the ADS server.

    `id` is a stable key chosen by the writer (typically the Service
    node's graph id). The cache has two states:

      - **live**:    what's currently pushed to Envoy
      - **staging**: a new set being built during a resolve cycle

    begin() creates an empty staging set; commit() atomically swaps
    staging -> live and triggers a push.
    """

    def __init__(self, ads: "AdsServer", flush_interval_sec: float = 0.5):
        self._ads = ads
        self._live: Dict[str, ResolvedEndpoint] = {}
        self._staging: Optional[Dict[str, ResolvedEndpoint]] = None
        self._dirty = False
        self._cond = asyncio.Condition()
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._flush_interval = flush_interval_sec
        self._last_http_names: set[str] = set()

    # --- resolve-cycle scope ---------------------------------------------

    def begin(self) -> None:
        """Start a resolve cycle. set/clear now write to staging."""
        self._staging = {}

    def commit(self) -> None:
        """Atomic swap: staging becomes live, schedule a flush."""
        if self._staging is None:
            return
        if self._staging != self._live:
            self._live = self._staging
            self._mark_dirty()
        self._staging = None

    def abort(self) -> None:
        """Discard staging without affecting live state."""
        self._staging = None

    # --- writes ----------------------------------------------------------

    def _target(self) -> Dict[str, ResolvedEndpoint]:
        return self._staging if self._staging is not None else self._live

    def set(self, id: str, ep: ResolvedEndpoint) -> None:
        """Upsert a slice. Writes to staging if a cycle is active."""
        t = self._target()
        prev = t.get(id)
        if prev == ep:
            return
        t[id] = ep
        if self._staging is None:
            self._mark_dirty()

    def clear(self, id: str) -> None:
        """Drop a slice. No-op if not present."""
        t = self._target()
        if id in t:
            del t[id]
            if self._staging is None:
                self._mark_dirty()

    def snapshot_now(self) -> list[ResolvedEndpoint]:
        """Current live entries (read-only view, for debugging/tests)."""
        return list(self._live.values())

    # --- flush loop ------------------------------------------------------

    def _mark_dirty(self) -> None:
        self._dirty = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._notify())

    async def _notify(self) -> None:
        async with self._cond:
            self._cond.notify_all()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._flush_loop())
        logger.info("xDS cache flush loop started (interval=%.1fs)",
                    self._flush_interval)

    async def stop(self) -> None:
        self._stopping = True
        async with self._cond:
            self._cond.notify_all()
        if self._task is not None:
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _flush_loop(self) -> None:
        while not self._stopping:
            async with self._cond:
                try:
                    await asyncio.wait_for(
                        self._cond.wait(), timeout=self._flush_interval)
                except asyncio.TimeoutError:
                    pass
            if self._stopping:
                break
            if not self._dirty:
                continue
            self._dirty = False
            await self._flush_once()

    async def _flush_once(self) -> None:
        entries = list(self._live.values())
        version = self._ads.next_version()
        snapshot = build_snapshot(version, entries)
        try:
            await self._ads.update_snapshot(snapshot)
        except Exception as e:
            logger.warning("xDS push failed: %s", e)
            return

        http_names = {ep.name for ep in entries if ep.protocol == "http"}
        if http_names != self._last_http_names:
            try:
                from zeropoint_agent.mdns import get_registry
                registry = get_registry()
                registry.reconcile([(n, 80) for n in http_names])
                self._last_http_names = http_names
            except Exception as e:
                logger.warning("mDNS reconcile failed (continuing): %s", e)

