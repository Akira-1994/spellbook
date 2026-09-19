from __future__ import annotations

import socket
import os
import threading
import webbrowser
import sys
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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _acquire_project_lock(path: Path):
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


def main() -> None:
    paths = AppPaths.default()
    lock = _acquire_project_lock(paths.lock_file)
    if lock is None:
        _show_error("法術書已在執行中，請切換到已開啟的瀏覽器分頁。")
        return
    port = _free_port()
    address = f"http://127.0.0.1:{port}/"
    if not os.getenv("SPELLBOOK_NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(address)).start()
    try:
        uvicorn.run(create_app(paths), host="127.0.0.1", port=port, log_level="warning")
    finally:
        lock.close()


if __name__ == "__main__":
    main()
