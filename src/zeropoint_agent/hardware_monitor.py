"""Trigger ordinary hardware-root GraphRuns from udev and a periodic timer."""

from __future__ import annotations

import logging
import select
import subprocess
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class HardwareMonitor:
    def __init__(
        self,
        callback: Callable[[], None],
        *,
        interval_seconds: float = 300,
        debounce_seconds: float = 1,
    ) -> None:
        self.callback = callback
        self.interval_seconds = max(5.0, interval_seconds)
        self.debounce_seconds = max(0.1, debounce_seconds)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="zeropoint-hardware-monitor",
            daemon=True,
        )
        self._process: Optional[subprocess.Popen[str]] = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._process is not None:
            self._process.terminate()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        next_periodic = time.monotonic() + self.interval_seconds
        pending_at: Optional[float] = None
        try:
            self._process = subprocess.Popen(
                [
                    "udevadm",
                    "monitor",
                    "--udev",
                    "--property",
                    "--subsystem-match=block",
                    "--subsystem-match=net",
                    "--subsystem-match=usb",
                    "--subsystem-match=pci",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except (FileNotFoundError, OSError) as error:
            logger.warning("udev hardware monitor unavailable: %s", error)
            self._process = None

        while not self._stop.is_set():
            now = time.monotonic()
            timeout = min(1.0, max(0.0, next_periodic - now))
            stream = self._process.stdout if self._process is not None else None
            if stream is not None:
                readable, _, _ = select.select([stream], [], [], timeout)
                if readable:
                    line = stream.readline()
                    if not line and self._process.poll() is not None:
                        self._process = None
                    elif line.startswith(("ACTION=", "UDEV ")):
                        pending_at = time.monotonic() + self.debounce_seconds
            else:
                self._stop.wait(timeout)

            now = time.monotonic()
            should_refresh = now >= next_periodic
            if pending_at is not None and now >= pending_at:
                should_refresh = True
                pending_at = None
            if should_refresh:
                try:
                    self.callback()
                except Exception:
                    logger.exception("hardware refresh callback failed")
                next_periodic = now + self.interval_seconds
