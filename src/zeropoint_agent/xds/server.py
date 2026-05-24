"""xDS ADS server — pushes Snapshots to connected Envoy instances.

Hand-rolled because there is no Python equivalent of go-control-plane.
Built on top of `envoy-data-plane` (betterproto + grpclib).

Model: one snapshot at a time, monotonic version. When a new snapshot
is installed via `update_snapshot()`, every connected stream is woken
and sends a fresh DiscoveryResponse per resource type whose contents
changed (or whose version the Envoy hasn't yet acked).

The agent runs as a single ADS node (`nodeID = "zeropoint-node"`), so
this is intentionally simple: we don't fan out by Envoy identity.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Dict, List, Optional

import betterproto
from envoy_data_plane.envoy.service.discovery.v3 import (
    AggregatedDiscoveryServiceBase, DiscoveryRequest, DiscoveryResponse,
)
from betterproto.lib.google.protobuf import Any as PbAny

from zeropoint_agent.xds.snapshot import Snapshot

logger = logging.getLogger(__name__)


# Canonical resource type URLs.
LDS_TYPE = "type.googleapis.com/envoy.config.listener.v3.Listener"
RDS_TYPE = "type.googleapis.com/envoy.config.route.v3.RouteConfiguration"
CDS_TYPE = "type.googleapis.com/envoy.config.cluster.v3.Cluster"


def _pack(msg: betterproto.Message, type_url: str) -> PbAny:
    return PbAny(type_url=type_url, value=bytes(msg))


def _resources_for(snapshot: Snapshot, type_url: str) -> List[PbAny]:
    if type_url == LDS_TYPE:
        return [_pack(l, LDS_TYPE) for l in snapshot.listeners]
    if type_url == RDS_TYPE:
        return [_pack(r, RDS_TYPE) for r in snapshot.routes]
    if type_url == CDS_TYPE:
        return [_pack(c, CDS_TYPE) for c in snapshot.clusters]
    return []


class AdsServer(AggregatedDiscoveryServiceBase):
    """Aggregated Discovery Service implementation.

    Holds the current snapshot in memory; uses an asyncio.Event to wake
    streams when the snapshot is replaced.
    """

    def __init__(self):
        self._snapshot: Optional[Snapshot] = None
        self._cv = asyncio.Condition()
        self._counter = 0

    def next_version(self) -> str:
        self._counter += 1
        return f"v{self._counter}"

    async def update_snapshot(self, snapshot: Snapshot) -> None:
        async with self._cv:
            self._snapshot = snapshot
            self._cv.notify_all()
        logger.info("xDS snapshot updated to %s "
                    "(listeners=%d, routes=%d, clusters=%d)",
                    snapshot.version, len(snapshot.listeners),
                    len(snapshot.routes), len(snapshot.clusters))

    async def stream_aggregated_resources(
        self,
        discovery_request_iterator: AsyncIterator[DiscoveryRequest],
    ) -> AsyncIterator[DiscoveryResponse]:
        """One bidirectional stream per Envoy instance.

        Each `DiscoveryRequest` either subscribes Envoy to a type
        (different version_info or new type_url) or acks/nacks our
        last push (matching version_info, no error). We respond by
        sending the current snapshot for that type. When the snapshot
        changes, we re-push every type Envoy has previously asked for.
        """
        # Per-stream state.
        last_pushed_version: Dict[str, str] = {}   # type_url -> our version
        subscribed: set[str] = set()              # type_urls Envoy cares about
        nonce_counter = 0

        async def push_for(type_url: str) -> Optional[DiscoveryResponse]:
            nonlocal nonce_counter
            if self._snapshot is None:
                return None
            version = self._snapshot.version
            nonce_counter += 1
            resp = DiscoveryResponse(
                version_info=version,
                resources=_resources_for(self._snapshot, type_url),
                type_url=type_url,
                nonce=f"n{nonce_counter}",
            )
            last_pushed_version[type_url] = version
            return resp

        async def request_reader():
            """Concurrent reader that updates subscribed/last_acked state."""
            try:
                async for req in discovery_request_iterator:
                    if req.error_detail and req.error_detail.message:
                        logger.warning(
                            "xDS NACK from envoy: type=%s ver=%s err=%s",
                            req.type_url, req.version_info,
                            req.error_detail.message)
                    subscribed.add(req.type_url)
                    async with self._cv:
                        self._cv.notify_all()
            finally:
                async with self._cv:
                    self._cv.notify_all()

        reader_task = asyncio.create_task(request_reader())
        try:
            while not reader_task.done():
                async with self._cv:
                    await self._cv.wait()
                if not subscribed:
                    continue
                for type_url in list(subscribed):
                    if (self._snapshot is not None and
                            last_pushed_version.get(type_url) != self._snapshot.version):
                        resp = await push_for(type_url)
                        if resp is not None:
                            yield resp
        finally:
            reader_task.cancel()
            try:
                await reader_task
            except (asyncio.CancelledError, Exception):
                pass
