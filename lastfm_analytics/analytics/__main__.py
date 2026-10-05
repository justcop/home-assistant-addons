import argparse
import os
import signal
from waitress import create_server
from .web import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="/data")
    parser.add_argument(
        "--development",
        action="store_true",
        help="Loopback-only access for local development",
    )
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        app = create_app(args.data_dir, development=args.development)
    except (ValueError, OSError):
        raise SystemExit(
            "Unable to start Listening Analytics. Check add-on configuration and data directory permissions."
        ) from None
    server = create_server(
        app,
        host="127.0.0.1" if args.development else "0.0.0.0",
        port=args.port,
        threads=4,
        clear_untrusted_proxy_headers=True,
    )

    def shutdown(*_):
        app.extensions["sync_worker"].close()
        server.close()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.run()
    finally:
        app.extensions["sync_worker"].close()


if __name__ == "__main__":
    main()
