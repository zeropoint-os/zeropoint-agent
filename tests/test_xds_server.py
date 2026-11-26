"""ADS SotW subscription and resource-name behavior."""

from __future__ import annotations

import asyncio
import unittest

from envoy_data_plane.envoy.config.cluster.v3 import Cluster
from envoy_data_plane.envoy.config.listener.v3 import Listener
from envoy_data_plane.envoy.config.route.v3 import RouteConfiguration
from envoy_data_plane.envoy.service.discovery.v3 import DiscoveryRequest

from zeropoint_agent.xds.server import (
    CDS_TYPE,
    LDS_TYPE,
    RDS_TYPE,
    AdsServer,
    _resources_for,
)
from zeropoint_agent.xds.snapshot import Snapshot


class RequestStream:
    def __init__(self):
        self.queue = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.queue.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def send(self, request: DiscoveryRequest) -> None:
        await self.queue.put(request)

    async def close(self) -> None:
        await self.queue.put(None)


def _names(resources, message_type):
    return [message_type().parse(resource.value).name for resource in resources]


def snapshot(version: str, *, extended: bool = False) -> Snapshot:
    listeners = [Listener(name="http_listener")]
    routes = [RouteConfiguration(name="http_routes")]
    clusters = [Cluster(name="cluster_qbittorrent")]
    if extended:
        listeners.append(Listener(name="private_listener"))
        routes.append(RouteConfiguration(name="private_routes"))
        clusters.append(Cluster(name="cluster_youtube_dl"))
    return Snapshot(
        version=version,
        listeners=listeners,
        routes=routes,
        clusters=clusters,
    )


class AdsSubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_same_version_rds_name_subscription_repushes_once(self):
        server = AdsServer()
        await server.update_snapshot(snapshot("v1"))
        requests = RequestStream()
        responses = server.stream_aggregated_resources(requests)

        await requests.send(DiscoveryRequest(
            type_url=RDS_TYPE,
            resource_names=["http_routes"],
        ))
        first = await asyncio.wait_for(anext(responses), 1)
        self.assertEqual(first.version_info, "v1")
        self.assertEqual(_names(first.resources, RouteConfiguration), ["http_routes"])

        await server.update_snapshot(snapshot("v2", extended=True))
        second = await asyncio.wait_for(anext(responses), 1)
        self.assertEqual(second.version_info, "v2")
        self.assertEqual(_names(second.resources, RouteConfiguration), ["http_routes"])

        await requests.send(DiscoveryRequest(
            type_url=RDS_TYPE,
            version_info="v2",
            response_nonce=second.nonce,
            resource_names=["http_routes", "private_routes"],
        ))
        late = await asyncio.wait_for(anext(responses), 1)
        self.assertEqual(late.version_info, "v2")
        self.assertEqual(
            _names(late.resources, RouteConfiguration),
            ["http_routes", "private_routes"],
        )

        await requests.send(DiscoveryRequest(
            type_url=RDS_TYPE,
            version_info="v2",
            response_nonce=late.nonce,
            resource_names=["private_routes", "http_routes"],
        ))
        pending = asyncio.create_task(anext(responses))
        await asyncio.sleep(0.05)
        self.assertFalse(pending.done(), "identical ACK triggered a response loop")
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        await requests.close()
        await responses.aclose()

    def test_resource_name_filtering_for_lds_cds_and_wildcard(self):
        value = snapshot("v2", extended=True)
        self.assertEqual(
            _names(_resources_for(value, LDS_TYPE, ("private_listener",)), Listener),
            ["private_listener"],
        )
        self.assertEqual(
            _names(
                _resources_for(value, CDS_TYPE, ("cluster_youtube_dl",)),
                Cluster,
            ),
            ["cluster_youtube_dl"],
        )
        self.assertEqual(
            set(_names(_resources_for(value, LDS_TYPE), Listener)),
            {"http_listener", "private_listener"},
        )
        self.assertEqual(len(_resources_for(value, CDS_TYPE)), 2)


if __name__ == "__main__":
    unittest.main()
