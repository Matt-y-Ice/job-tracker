"""Start the dashboard:  python -m src.dashboard [--port 5000] [--no-browser]"""
import argparse
import logging
import socket
import sys
import threading
import webbrowser

from ..log import setup_logging
from .app import create_app


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Job tracker dashboard")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    setup_logging()
    url = f"http://localhost:{args.port}"
    if not port_free(args.port):
        print(f"Port {args.port} is already in use. The dashboard may already be running: open {url}")
        print("Or start it on another port:  python -m src.dashboard --port 5050")
        if not args.no_browser:
            webbrowser.open(url)
        return 1
    print(f"Dashboard running at {url}  (keep this window open; press Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # no line per request in the console
    create_app().run(host="127.0.0.1", port=args.port, threaded=True, debug=False)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    sys.exit(main())
