"""Trusted-LAN mDNS advertisement for ZeroPoint agent discovery."""

from __future__ import annotations

import logging
import platform
import socket
from typing import Optional

from zeropoint_agent.identity import API_VERSION, AgentIdentity

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_zeropoint-agent._tcp.local."


def _host_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class AgentAdvertiser:
    def __init__(self, identity: AgentIdentity, port: int):
        self.identity = identity
        self.port = port
        self._zeroconf = None
        self._info = None

    def start(self) -> bool:
        try:
            from zeroconf import ServiceInfo, Zeroconf

            address = _host_ip()
            suffix = self.identity.agent_id[-8:]
            instance = f"{self.identity.name}-{suffix}.{SERVICE_TYPE}"
            info = ServiceInfo(
                SERVICE_TYPE,
                instance,
                addresses=[socket.inet_aton(address)],
                port=self.port,
                properties={
                    "id": self.identity.agent_id,
                    "name": self.identity.name,
                    "api": API_VERSION,
                    "arch": platform.machine(),
                    "security": "trusted_lan",
                },
                server=f"{socket.gethostname()}.local.",
            )
            zeroconf = Zeroconf()
            zeroconf.register_service(
                info,
                allow_name_change=True,
                cooperating_responders=True,
            )
            self._zeroconf = zeroconf
            self._info = info
            logger.info(
                "advertising ZeroPoint agent %s at %s:%d",
                self.identity.name,
                address,
                self.port,
            )
            return True
        except Exception as error:
            logger.warning("agent mDNS advertisement failed: %r", error)
            self.stop()
            return False

    def stop(self) -> None:
        if self._zeroconf is None:
            return
        try:
            if self._info is not None:
                self._zeroconf.unregister_service(self._info)
        except Exception:
            pass
        try:
            self._zeroconf.close()
        except Exception:
            pass
        self._zeroconf = None
        self._info = None
