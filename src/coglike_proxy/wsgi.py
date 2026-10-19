"""WSGI application exposing virtual COGs over HTTP with byte-range support.

Any WSGI server can host ``application`` (configured from the environment) or
an app built by ``create_app``. GDAL reaches it through ``/vsicurl/``.
"""

from __future__ import annotations

import logging
import uuid
from email.utils import formatdate
from pathlib import Path
from typing import Callable, Iterable, Iterator

from . import source
from .ranges import parse_range_header
from .service import CogLikeService, VirtualCog, service_from_env

log = logging.getLogger(__name__)

CONTENT_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"
StartResponse = Callable[..., object]


class CogLikeApp:
    def __init__(self, service: CogLikeService) -> None:
        self.service = service

    def __call__(self, environ: dict, start_response: StartResponse) -> Iterable[bytes]:
        method = environ["REQUEST_METHOD"]
        if method not in ("GET", "HEAD"):
            return _plain(start_response, "405 Method Not Allowed",
                          "Method not allowed\n", [("Allow", "GET, HEAD")])
        try:
            # PEP 3333: PATH_INFO is decoded bytes carried as latin-1.
            path = environ.get("PATH_INFO", "").encode("latin-1").decode("utf-8")
        except UnicodeError:
            return _plain(start_response, "404 Not Found", "Not found\n")

        try:
            cog = self.service.lookup(path)
        except source.UnsupportedRaster as e:
            log.error("cannot serve %s: %s", path, e)
            return _plain(start_response, "500 Internal Server Error",
                          f"Raster cannot be served as a COG: {e}\n")
        except RuntimeError:
            log.exception("GDAL failed to read the VRT for %s", path)
            return _plain(start_response, "500 Internal Server Error",
                          "Failed to read raster\n")
        if cog is None:
            return _plain(start_response, "404 Not Found", "Not found\n")

        headers = [
            ("Accept-Ranges", "bytes"),
            ("ETag", cog.version.etag),
            ("Last-Modified", formatdate(cog.version.mtime_ns / 1e9, usegmt=True)),
        ]
        size = cog.size
        ranges = parse_range_header(environ.get("HTTP_RANGE"), size)
        head = method == "HEAD"

        if ranges is None:
            start_response("200 OK", headers + [
                ("Content-Type", CONTENT_TYPE), ("Content-Length", str(size))])
            return [] if head else self.service.iter_bytes(cog, 0, size)

        if not ranges:
            start_response("416 Range Not Satisfiable", headers + [
                ("Content-Range", f"bytes */{size}"), ("Content-Length", "0")])
            return []

        if len(ranges) == 1:
            start, stop = ranges[0]
            start_response("206 Partial Content", headers + [
                ("Content-Type", CONTENT_TYPE),
                ("Content-Range", f"bytes {start}-{stop - 1}/{size}"),
                ("Content-Length", str(stop - start))])
            return [] if head else self.service.iter_bytes(cog, start, stop)

        boundary = uuid.uuid4().hex
        parts = [(_part_header(boundary, start, stop, size), start, stop)
                 for start, stop in ranges]
        closing = f"\r\n--{boundary}--\r\n".encode("ascii")
        length = sum(len(h) + stop - start for h, start, stop in parts) + len(closing)
        start_response("206 Partial Content", headers + [
            ("Content-Type", f"multipart/byteranges; boundary={boundary}"),
            ("Content-Length", str(length))])
        return [] if head else self._multipart(cog, parts, closing)

    def _multipart(self, cog: VirtualCog, parts: list[tuple[bytes, int, int]],
                   closing: bytes) -> Iterator[bytes]:
        for header, start, stop in parts:
            yield header
            yield from self.service.iter_bytes(cog, start, stop)
        yield closing


def _part_header(boundary: str, start: int, stop: int, size: int) -> bytes:
    return (f"\r\n--{boundary}\r\n"
            f"Content-Type: {CONTENT_TYPE}\r\n"
            f"Content-Range: bytes {start}-{stop - 1}/{size}\r\n\r\n").encode("ascii")


def _plain(start_response: StartResponse, status: str, body: str,
           extra_headers: list[tuple[str, str]] = ()) -> list[bytes]:
    data = body.encode("utf-8")
    start_response(status, [("Content-Type", "text/plain; charset=utf-8"),
                            ("Content-Length", str(len(data))), *extra_headers])
    return [data]


def create_app(root: str | Path, **service_options) -> CogLikeApp:
    """Factory, e.g. ``gunicorn 'coglike_proxy.wsgi:create_app("/data/vrt")'``."""
    return CogLikeApp(CogLikeService(Path(root), **service_options))


def application(environ: dict, start_response: StartResponse) -> Iterable[bytes]:
    """WSGI entry point configured from ``COGLIKE_*`` environment variables."""
    global _env_app
    if _env_app is None:
        _env_app = CogLikeApp(service_from_env())
    return _env_app(environ, start_response)


_env_app: CogLikeApp | None = None
