from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# Windowless PyInstaller builds expose stdout/stderr as None; Uvicorn's
# logging setup expects stream-like objects even when the log level is quiet.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import uvicorn

from spellbook.app import create_app
from spellbook.config import AppPaths
from spellbook.lifecycle import Lifecycle


# Pages ping every 30 s, but background tabs may be throttled to about one
# timer per minute, so the fallback allows several missed pings.
IDLE_TIMEOUT_SECONDS = int(os.getenv("SPELLBOOK_IDLE_TIMEOUT", 5 * 60))
# How long to wait after the last page closed, so a reload can reconnect.
CLOSE_GRACE_SECONDS = int(os.getenv("SPELLBOOK_CLOSE_GRACE", 15))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _acquire_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0)
    if path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _show_error(message: str) -> None:
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "法術書", 0x10)
    else:
        print(message, file=sys.stderr)


def _open_browser(address: str) -> None:
    if not os.getenv("SPELLBOOK_NO_BROWSER"):
        webbrowser.open(address)


def _reopen_running_instance(paths: AppPaths) -> bool:
    """Another copy holds the lock: point the browser at it instead of failing."""
    try:
        address = json.loads(paths.server_file.read_text(encoding="utf-8"))["address"]
        with urllib.request.urlopen(address + "health", timeout=3) as response:
            if response.status == 200:
                _open_browser(address)
                return True
    except (OSError, ValueError, KeyError):
        pass
    return False


def _supervise(server: uvicorn.Server, lifecycle: Lifecycle, address: str) -> None:
    while not server.started:
        if server.should_exit:
            return
        time.sleep(0.1)
    _open_browser(address)
    while not server.should_exit:
        time.sleep(0.5)
        if lifecycle.should_exit():
            server.should_exit = True


def main() -> None:
    paths = AppPaths.default()
    lock = _acquire_lock(paths.lock_file)
    if lock is None:
        if not _reopen_running_instance(paths):
            _show_error("法術書已在執行中，但無法連線。請稍候幾秒後再開啟一次。")
        return
    try:
        try:
            lifecycle = Lifecycle(idle_timeout=IDLE_TIMEOUT_SECONDS, close_grace=CLOSE_GRACE_SECONDS)
            app = create_app(paths, lifecycle)
        except Exception as exc:  # noqa: BLE001 - shown to the user instead of a silent exit
            _show_error(f"法術書無法啟動：\n{exc}")
            return
        port = _free_port()
        address = f"http://127.0.0.1:{port}/"
        paths.server_file.write_text(json.dumps({"address": address, "pid": os.getpid()}), encoding="utf-8")
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=_supervise, args=(server, lifecycle, address), daemon=True).start()
        server.run()
    finally:
        paths.server_file.unlink(missing_ok=True)
        lock.close()


if __name__ == "__main__":
    main()
