"""Run the xDS gRPC server as a background asyncio task.

Wraps `AdsServer` in a grpclib `Server`. Designed to start on app
boot (via FastAPI `startup` hook) and stop on app shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from grpclib.server import Server

from zeropoint_agent.xds.server import AdsServer

logger = logging.getLogger(__name__)

DEFAULT_PORT = 18000


class XdsRunner:
    """Owns the lifecycle of an `AdsServer` + grpclib `Server`."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.ads = AdsServer()
        self._server: Optional[Server] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = Server([self.ads])
        await self._server.start("0.0.0.0", self.port)
        logger.info("xDS server listening on :%d", self.port)
        self._task = asyncio.create_task(self._server.wait_closed())

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        try:
            await self._server.wait_closed()
        except Exception as e:
            logger.warning("xDS server stop error: %s", e)
        self._server = None
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        logger.info("xDS server stopped")
