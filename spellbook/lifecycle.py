from __future__ import annotations

import threading
import time
from typing import Callable


class Lifecycle:
    """Decides when the windowless desktop app should exit.

    Every open page registers itself by pinging with its own id and says
    goodbye when it is closed. The app exits:

    - right away when the user asks it to quit;
    - `close_grace` seconds after the last page closed, unless a page (for
      example the same page after a reload) connects again meanwhile;
    - as a fallback, after `idle_timeout` seconds without any request, for
      pages that vanished without saying goodbye (browser crash, sleep).
    """

    def __init__(
        self,
        *,
        idle_timeout: float = 5 * 60,
        close_grace: float = 15,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.idle_timeout = idle_timeout
        self.close_grace = close_grace
        self.clock = clock
        self._lock = threading.Lock()
        self._pages: dict[str, float] = {}
        self._last_activity = clock()
        self._closed_at: float | None = None
        self._quit_requested = False

    def touch(self) -> None:
        with self._lock:
            self._last_activity = self.clock()

    def ping(self, page: str) -> None:
        with self._lock:
            now = self.clock()
            self._last_activity = now
            self._pages[page] = now

    def close(self, page: str) -> None:
        with self._lock:
            now = self.clock()
            self._last_activity = now
            self._pages.pop(page, None)
            self._closed_at = now

    def request_quit(self) -> None:
        with self._lock:
            self._quit_requested = True

    def should_exit(self) -> bool:
        with self._lock:
            if self._quit_requested:
                return True
            now = self.clock()
            idle = now - self._last_activity
            # Pages that stopped pinging without closing are presumed gone.
            self._pages = {page: seen for page, seen in self._pages.items() if now - seen < self.idle_timeout}
            if not self._pages and self._closed_at is not None and idle >= self.close_grace:
                return True
            return idle >= self.idle_timeout
