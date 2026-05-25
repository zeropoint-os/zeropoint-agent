"""mDNS / Zeroconf registry for http exposures.

For every http Exposure with a name N, we advertise `N.local` over
mDNS. Other hosts on the LAN can then resolve it to this host's IP
and reach the Envoy HTTP listener on :80.

In mock mode and when zeroconf isn't available we are silently a no-op
so tests and dev environments don't need extra dependencies.
"""

from __future__ import annotations

import logging
import socket
import threading
from typing import Dict, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)


def _zeroconf():
    try:
        from zeroconf import Zeroconf, ServiceInfo  # type: ignore
        return Zeroconf, ServiceInfo
    except Exception as e:
        logger.debug("zeroconf unavailable: %s", e)
        return None, None


def _host_ip() -> str:
    """Best-effort LAN IP of this host (the one Envoy will be reachable on)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Doesn't actually send; uses routing table to pick local iface.
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return "127.0.0.1"


class MdnsRegistry:
    """Process-wide singleton-ish registry of registered http endpoints."""

    def __init__(self):
        self._zc = None
        self._info_type = None
        self._registered: Dict[str, object] = {}  # name -> ServiceInfo
        self._lock = threading.Lock()

    def _ensure_zc(self) -> bool:
        if self._zc is not None:
            return True
        Zeroconf, ServiceInfo = _zeroconf()
        if Zeroconf is None:
            return False
        try:
            self._zc = Zeroconf()
            self._info_type = ServiceInfo
            return True
        except Exception as e:
            logger.warning("zeroconf init failed: %s", e)
            return False

    def register(self, name: str, port: int = 80) -> None:
        """Register `<name>.local` pointing at this host on `port`."""
        if not self._ensure_zc():
            return
        with self._lock:
            if name in self._registered:
                return
            ip = _host_ip()
            try:
                # Advertise an HTTP service so OS-level mDNS resolvers
                # publish a matching A record for the hostname.
                info = self._info_type(
                    type_="_http._tcp.local.",
                    name=f"{name}._http._tcp.local.",
                    addresses=[socket.inet_aton(ip)],
                    port=port,
                    server=f"{name}.local.",
                    properties={},
                )
                self._zc.register_service(info, allow_name_change=False)
                self._registered[name] = info
                logger.info("mDNS registered %s.local -> %s:%d", name, ip, port)
            except Exception as e:
                logger.warning("mDNS register %s failed: %s", name, e)

    def unregister(self, name: str) -> None:
        if self._zc is None:
            return
        with self._lock:
            info = self._registered.pop(name, None)
        if info is not None:
            try:
                self._zc.unregister_service(info)
                logger.info("mDNS unregistered %s.local", name)
            except Exception as e:
                logger.warning("mDNS unregister %s failed: %s", name, e)

    def reconcile(self, names: Iterable[Tuple[str, int]]) -> None:
        """Ensure exactly the given (name, port) entries are registered."""
        desired = dict(names)
        with self._lock:
            current = set(self._registered.keys())
        # Remove stale
        for name in current - set(desired):
            self.unregister(name)
        # Add missing
        for name, port in desired.items():
            if name not in current:
                self.register(name, port)

    def close(self) -> None:
        if self._zc is None:
            return
        with self._lock:
            infos = list(self._registered.values())
            self._registered.clear()
        for info in infos:
            try:
                self._zc.unregister_service(info)
            except Exception:
                pass
        try:
            self._zc.close()
        except Exception:
            pass
        self._zc = None


_REGISTRY: Optional[MdnsRegistry] = None


def get_registry() -> MdnsRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = MdnsRegistry()
    return _REGISTRY


def close_registry() -> None:
    global _REGISTRY
    if _REGISTRY is not None:
        _REGISTRY.close()
        _REGISTRY = None
