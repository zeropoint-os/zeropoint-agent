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

import betterproto2
from envoy_data_plane.envoy.service.discovery.v3 import (
    AggregatedDiscoveryServiceBase, DiscoveryRequest, DiscoveryResponse,
)
from envoy_data_plane.google.protobuf import Any as PbAny

from zeropoint_agent.xds.snapshot import Snapshot

logger = logging.getLogger(__name__)


# Canonical resource type URLs.
LDS_TYPE = "type.googleapis.com/envoy.config.listener.v3.Listener"
RDS_TYPE = "type.googleapis.com/envoy.config.route.v3.RouteConfiguration"
CDS_TYPE = "type.googleapis.com/envoy.config.cluster.v3.Cluster"


def _pack(msg: betterproto2.Message, type_url: str) -> PbAny:
    return PbAny(type_url=type_url, value=bytes(msg))


def _resources_for(
    snapshot: Snapshot,
    type_url: str,
    resource_names: tuple[str, ...] = (),
) -> List[PbAny]:
    if type_url == LDS_TYPE:
        messages = snapshot.listeners
    elif type_url == RDS_TYPE:
        messages = snapshot.routes
    elif type_url == CDS_TYPE:
        messages = snapshot.clusters
    else:
        return []
    if resource_names:
        requested = set(resource_names)
        messages = [message for message in messages if message.name in requested]
    return [_pack(message, type_url) for message in messages]


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
        # Per-stream state. A SotW subscription is identified by both type and
        # requested resource names; Envoy may add names after acknowledging the
        # current snapshot version (for example a newly installed RDS listener).
        last_pushed: Dict[str, tuple[str, tuple[str, ...]]] = {}
        requested_names: Dict[str, tuple[str, ...]] = {}
        subscribed: set[str] = set()
        nonce_counter = 0

        async def push_for(type_url: str) -> Optional[DiscoveryResponse]:
            nonlocal nonce_counter
            if self._snapshot is None:
                return None
            version = self._snapshot.version
            names = requested_names.get(type_url, ())
            nonce_counter += 1
            resp = DiscoveryResponse(
                version_info=version,
                resources=_resources_for(self._snapshot, type_url, names),
                type_url=type_url,
                nonce=f"n{nonce_counter}",
            )
            last_pushed[type_url] = (version, names)
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
                    if not req.type_url:
                        continue
                    names = tuple(sorted(set(req.resource_names)))
                    async with self._cv:
                        changed = (
                            req.type_url not in subscribed
                            or requested_names.get(req.type_url) != names
                        )
                        subscribed.add(req.type_url)
                        requested_names[req.type_url] = names
                        snapshot_key = (
                            (self._snapshot.version, names)
                            if self._snapshot is not None
                            else None
                        )
                        if changed or last_pushed.get(req.type_url) != snapshot_key:
                            self._cv.notify_all()
            finally:
                async with self._cv:
                    self._cv.notify_all()

        def pending_types() -> list[str]:
            if self._snapshot is None:
                return []
            return [
                type_url
                for type_url in subscribed
                if last_pushed.get(type_url) != (
                    self._snapshot.version,
                    requested_names.get(type_url, ()),
                )
            ]

        reader_task = asyncio.create_task(request_reader())
        try:
            while not reader_task.done():
                async with self._cv:
                    await self._cv.wait_for(
                        lambda: reader_task.done() or bool(pending_types())
                    )
                if reader_task.done():
                    break
                for type_url in pending_types():
                    resp = await push_for(type_url)
                    if resp is not None:
                        yield resp
        finally:
            reader_task.cancel()
            try:
                await reader_task
            except (asyncio.CancelledError, Exception):
                pass
