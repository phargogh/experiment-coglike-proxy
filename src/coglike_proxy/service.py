"""Serve byte ranges of virtual COGs backed by VRT files."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from . import source
from .cache import LruCache
from .catalog import VrtCatalog
from .layout import CogLayout, plan_layout


@dataclass(frozen=True)
class VrtVersion:
    """Identifies one version of a VRT file. A new version gets a new layout."""

    path: Path
    mtime_ns: int
    size: int

    @property
    def etag(self) -> str:
        return f'"{self.mtime_ns:x}-{self.size:x}"'


@dataclass(frozen=True)
class VirtualCog:
    version: VrtVersion
    layout: CogLayout

    @property
    def size(self) -> int:
        return self.layout.size


class CogLikeService:
    """Resolve request paths to virtual COGs and produce their bytes.

    Thread safe. Each byte stream opens its own GDAL dataset, and only when a
    tile that is not cached has to be rendered.
    """

    def __init__(self, root: Path, *, tile_size: int = 512,
                 overview_resampling: str = "nearest",
                 bigtiff: bool | None = None,
                 tile_cache_bytes: int = 256 * 2**20,
                 layout_cache_bytes: int = 64 * 2**20) -> None:
        if overview_resampling not in source.RESAMPLING:
            raise ValueError(f"unknown resampling {overview_resampling!r}; "
                             f"choose from {sorted(source.RESAMPLING)}")
        self.catalog = VrtCatalog(root)
        self.tile_size = tile_size
        self.overview_resampling = overview_resampling
        self.bigtiff = bigtiff
        self._layouts: LruCache[CogLayout] = LruCache(
            layout_cache_bytes, lambda layout: len(layout.prefix))
        self._tiles: LruCache[bytes] = LruCache(tile_cache_bytes, len)

    def lookup(self, url_path: str) -> VirtualCog | None:
        """``None`` when no VRT maps to ``url_path``.

        Raises ``source.UnsupportedRaster`` or GDAL's ``RuntimeError`` when a
        VRT exists but cannot be served.
        """
        path = self.catalog.resolve(url_path)
        if path is None:
            return None
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        version = VrtVersion(path, stat.st_mtime_ns, stat.st_size)
        layout = self._layouts.get(version)
        if layout is None:
            with source.open_raster(path) as ds:
                raster = source.describe(ds)
            layout = plan_layout(raster, self.tile_size, self.bigtiff)
            self._layouts.put(version, layout)
        return VirtualCog(version, layout)

    def iter_bytes(self, cog: VirtualCog, start: int, stop: int) -> Iterator[bytes]:
        """Yield bytes ``[start, stop)`` of the virtual file.

        A generator: the GDAL dataset it may open is closed when iteration
        finishes or the generator is closed (WSGI servers call ``close()``).
        """
        layout = cog.layout
        if not 0 <= start <= stop <= layout.size:
            raise ValueError(f"range [{start}, {stop}) outside file of {layout.size}")
        prefix_len = len(layout.prefix)
        pos = start
        if pos < prefix_len:
            end = min(stop, prefix_len)
            yield layout.prefix[pos:end]
            pos = end

        ds = None

        def tile_data(ref) -> bytes:
            nonlocal ds
            key = (cog.version, self.tile_size, self.overview_resampling,
                   ref.level, ref.col, ref.row)
            data = self._tiles.get(key)
            if data is None:
                if ds is None:
                    ds = source.open_raster(cog.version.path)
                data = source.render_tile(ds, layout, ref.level, ref.col, ref.row,
                                          self.overview_resampling)
                self._tiles.put(key, data)
            return data

        try:
            while pos < stop:
                ref = layout.locate_tile(pos)
                end = min(stop, ref.slot_offset + layout.slot_bytes)
                yield layout.read_slot(pos - ref.slot_offset, end - ref.slot_offset,
                                       lambda: tile_data(ref))
                pos = end
        finally:
            if ds is not None:
                ds.Close()


def service_from_env() -> CogLikeService:
    """Build a service from ``COGLIKE_*`` environment variables (for WSGI servers)."""
    bigtiff = os.environ.get("COGLIKE_BIGTIFF", "auto").lower()
    if bigtiff not in ("auto", "yes", "no"):
        raise ValueError("COGLIKE_BIGTIFF must be auto, yes or no")
    return CogLikeService(
        Path(os.environ["COGLIKE_ROOT"]),
        tile_size=int(os.environ.get("COGLIKE_TILE_SIZE", 512)),
        overview_resampling=os.environ.get("COGLIKE_OVERVIEW_RESAMPLING", "nearest"),
        bigtiff=None if bigtiff == "auto" else bigtiff == "yes",
        tile_cache_bytes=int(os.environ.get("COGLIKE_TILE_CACHE_BYTES", 256 * 2**20)),
    )
