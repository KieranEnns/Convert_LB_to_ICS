#!/usr/bin/env python3
"""Desktop entry point for packaged builds.

This module is intended for PyInstaller/py2app-style packaging. It starts the
local web app and opens the user's browser without requiring a Terminal window.
"""

from __future__ import annotations

import socket
import threading
import webbrowser

from web_app import HOST, PORT, run


def is_server_running(host: str = HOST, port: int = PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, port)) == 0


def open_browser(host: str = HOST, port: int = PORT) -> None:
    webbrowser.open(f"http://{host}:{port}")


def main() -> int:
    if is_server_running():
        open_browser()
        return 0

    threading.Timer(1.0, open_browser).start()
    run(HOST, PORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
