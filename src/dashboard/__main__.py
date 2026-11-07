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


def choose_port(requested: int, user_set_port: bool, platform: str, free) -> int:
    """Keep the requested port unless macOS has taken the default and the user did not pick one."""
    if free(requested) or user_set_port or platform != "darwin":
        return requested
    for candidate in range(5050, 5060):
        if free(candidate):
            return candidate
    return requested


def main() -> int:
    parser = argparse.ArgumentParser(description="Job tracker dashboard")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    setup_logging()
    user_set_port = any(a == "--port" or a.startswith("--port=") for a in sys.argv[1:])
    port = choose_port(args.port, user_set_port, sys.platform, port_free)
    url = f"http://localhost:{port}"
    if not port_free(port):
        print(f"Port {port} is already in use. The dashboard may already be running: open {url}")
        print("Or start it on another port:  python -m src.dashboard --port 5050")
        if not args.no_browser:
            webbrowser.open(url)
        return 1
    print(f"Dashboard running at {url}  (keep this window open; press Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # no line per request in the console
    create_app().run(host="127.0.0.1", port=port, threaded=True, debug=False)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    sys.exit(main())
