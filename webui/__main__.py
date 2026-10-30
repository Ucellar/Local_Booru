"""Run the web gallery without the desktop window: ``python -m webui [--port N] [--no-browser]``."""
from __future__ import annotations

import argparse
import logging
import threading
import time
import webbrowser


def _settings_provider():
    """Settings as the desktop program saved them; re-read when the settings file changes."""
    from core.settings import SETTINGS_FILE, load_settings
    state = {"stamp": None, "settings": {}}
    lock = threading.Lock()

    def provider() -> dict:
        try:
            stamp = SETTINGS_FILE.stat().st_mtime_ns
        except OSError:
            stamp = 0
        with lock:
            if stamp != state["stamp"]:
                state["settings"] = load_settings()
                state["stamp"] = stamp
            return state["settings"]

    return provider


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m webui", description="Галерея Local Booru в браузере")
    parser.add_argument("--port", type=int, default=0, help="порт (по умолчанию — любой свободный)")
    parser.add_argument("--no-browser", action="store_true", help="не открывать браузер, только напечатать адрес")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from webui.server import WebUI
    ui = WebUI(_settings_provider(), port=args.port)
    ui.start()
    link = ui.login_url("/posts")
    print("Галерея открыта по адресу (ссылка действует, пока работает это окно):", flush=True)
    print(link, flush=True)
    if not args.no_browser:
        webbrowser.open(link)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("Остановлено.", flush=True)
    finally:
        ui.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
