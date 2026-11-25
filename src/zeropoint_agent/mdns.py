"""mDNS / Zeroconf registry for machine-scoped HTTP exposures."""

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
    except Exception as error:
        logger.debug("zeroconf unavailable: %s", error)
        return None, None


def _host_ip() -> str:
    """Best-effort LAN IP of this host (the one Envoy will be reachable on)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except Exception:
        return "127.0.0.1"


def _canonical_local_hostname(name: str) -> str:
    hostname = name.strip().rstrip(".")
    if not hostname.lower().endswith(".local"):
        raise ValueError(f"mDNS exposure hostname must end in .local: {name!r}")
    return hostname


class MdnsRegistry:
    """Process-wide registry of canonical HTTP exposure hostnames."""

    def __init__(self):
        self._zc = None
        self._info_type = None
        self._registered: Dict[str, object] = {}
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
        except Exception as error:
            logger.warning("zeroconf init failed: %s", error)
            return False

    def register(self, name: str, port: int = 80) -> bool:
        """Register a canonical machine-scoped `.local` hostname."""
        hostname = _canonical_local_hostname(name)
        if not self._ensure_zc():
            return False
        with self._lock:
            if hostname in self._registered:
                return True
            ip = _host_ip()
            try:
                info = self._info_type(
                    type_="_http._tcp.local.",
                    name=f"{hostname}._http._tcp.local.",
                    addresses=[socket.inet_aton(ip)],
                    port=port,
                    server=f"{hostname}.",
                    properties={},
                )
                self._zc.register_service(
                    info,
                    allow_name_change=False,
                    cooperating_responders=True,
                )
                self._registered[hostname] = info
                logger.info("mDNS registered %s -> %s:%d", hostname, ip, port)
                return True
            except Exception as error:
                logger.warning("mDNS register %s failed: %r", hostname, error)
                return False

    def unregister(self, name: str) -> None:
        if self._zc is None:
            return
        hostname = _canonical_local_hostname(name)
        with self._lock:
            info = self._registered.pop(hostname, None)
        if info is not None:
            try:
                self._zc.unregister_service(info)
                logger.info("mDNS unregistered %s", hostname)
            except Exception as error:
                logger.warning("mDNS unregister %s failed: %s", hostname, error)

    def reconcile(self, names: Iterable[Tuple[str, int]]) -> bool:
        """Ensure exactly the given canonical (hostname, port) entries exist."""
        desired = {
            _canonical_local_hostname(name): port
            for name, port in names
        }
        succeeded = True
        with self._lock:
            current = set(self._registered.keys())
        for hostname in current - set(desired):
            self.unregister(hostname)
        for hostname, port in desired.items():
            if hostname not in current:
                succeeded = self.register(hostname, port) and succeeded
        return succeeded

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
