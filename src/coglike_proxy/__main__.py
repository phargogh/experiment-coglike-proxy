"""Development server: ``python -m coglike_proxy --root DIR``."""

from __future__ import annotations

import argparse
import logging
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

from .source import RESAMPLING
from .wsgi import create_app


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="directory containing .vrt files")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--tile-size", type=int, default=512)
    parser.add_argument("--overview-resampling", default="nearest", choices=sorted(RESAMPLING))
    parser.add_argument("--bigtiff", choices=["auto", "yes", "no"], default="auto")
    parser.add_argument("--tile-cache-mb", type=int, default=256)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    app = create_app(
        args.root, tile_size=args.tile_size,
        overview_resampling=args.overview_resampling,
        bigtiff=None if args.bigtiff == "auto" else args.bigtiff == "yes",
        tile_cache_bytes=args.tile_cache_mb * 2**20)
    with make_server(args.host, args.port, app, server_class=ThreadingWSGIServer) as httpd:
        host, port = httpd.server_address[:2]
        print(f"Serving VRTs under {app.service.catalog.root} at http://{host}:{port}/")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
