"""Byte layout of a virtual Cloud Optimized GeoTIFF.

Given a description of a raster, compute the complete structure of an
uncompressed, tiled, pixel-interleaved COG: the header, the GDAL structural
metadata ("ghost") area, one IFD per resolution level, and the byte offset of
every tile. Because tiles are uncompressed, every tile has the same, known
size, so the whole layout is known before any pixel is read. Tile *contents*
are produced elsewhere, on demand.

File order follows the GDAL COG layout:

    header | ghost area | IFD full-res | IFD ovr 1 | ... | IFD ovr N |
    tiles ovr N | ... | tiles ovr 1 | tiles full-res

Within a level, tiles are in row-major order. As in GDAL's COG driver, each
tile's data is preceded by a 4-byte little-endian *leader* holding its byte
count and followed by a *trailer* repeating its last 4 bytes; declaring this in
the ghost area lets GDAL recognise the file as ``LAYOUT=COG``.

Each tile occupies a fixed-size *slot*::

    leader (4) | data (tile_bytes) | trailer (4) | zero padding

Slots are laid out so tile data starts on a ``CHUNK_ALIGNMENT`` boundary and a
tile's trailer never shares an aligned chunk with the next tile's leader.
GDAL's /vsicurl/ fetches whole 16 KiB chunks by default, so with this layout
a chunk-rounded read of one tile touches only constant bytes (padding and
leaders) of its neighbours and never forces them to be rendered. GDAL reads
tile sizes from leaders and tolerates gaps between tiles. The alignment is
purely a performance measure; the file is valid for any client.
"""

from __future__ import annotations

import bisect
import struct
from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import tiff
from .tiff import Tag, TiffFormat

CHUNK_ALIGNMENT = 16384  # GDAL's default CPL_VSIL_CURL_CHUNK_SIZE.
_LEADER_BYTES = 4
_TRAILER_BYTES = 4
_SAMPLE_FORMATS = {"u": 1, "i": 2, "f": 3}  # TIFF SampleFormat by numpy kind.
_GHOST_FIRST_LINE = "GDAL_STRUCTURAL_METADATA_SIZE=%06d bytes\n"
_GHOST_BODY = (
    "LAYOUT=IFDS_BEFORE_DATA\n"
    "BLOCK_ORDER=ROW_MAJOR\n"
    "BLOCK_LEADER=SIZE_AS_UINT4\n"
    "BLOCK_TRAILER=LAST_4_BYTES_REPEATED\n"
    "KNOWN_INCOMPATIBLE_EDITION=NO\n"
)


@dataclass(frozen=True)
class RasterDescription:
    """What the layout needs to know about a raster.

    ``main_ifd_tags`` are copied only into the full-resolution IFD (e.g.
    GeoTIFF keys). ``all_ifd_tags`` are copied into every IFD (e.g. nodata,
    photometric interpretation). Neither may contain tags that encode the
    layout itself; those are computed here.
    """

    width: int
    height: int
    band_count: int
    dtype: np.dtype
    main_ifd_tags: tuple[Tag, ...] = ()
    all_ifd_tags: tuple[Tag, ...] = ()

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1 or self.band_count < 1:
            raise ValueError("raster must have positive dimensions and bands")
        if np.dtype(self.dtype).kind not in _SAMPLE_FORMATS:
            raise ValueError(f"unsupported sample type {self.dtype}")


@dataclass(frozen=True)
class Level:
    """One resolution level: 0 is full resolution, then successive overviews."""

    index: int
    width: int
    height: int
    tiles_across: int
    tiles_down: int
    first_slot_offset: int

    @property
    def tile_count(self) -> int:
        return self.tiles_across * self.tiles_down


@dataclass(frozen=True)
class TileRef:
    """A tile of one level, and the file offset where its slot starts."""

    level: int
    col: int
    row: int
    slot_offset: int


@dataclass(frozen=True)
class CogLayout:
    """Complete byte layout of the virtual file.

    Invariants: ``prefix`` occupies ``[0, len(prefix))``; tile slots follow
    with no gaps, each ``slot_bytes`` long, ending at ``size``. Within a slot,
    tile data starts at ``_LEADER_BYTES``.
    """

    raster: RasterDescription
    tile_size: int
    tile_bytes: int
    slot_bytes: int
    bigtiff: bool
    levels: tuple[Level, ...]
    prefix: bytes
    size: int

    def locate_tile(self, offset: int) -> TileRef:
        """Return the tile whose slot contains byte ``offset``."""
        if not len(self.prefix) <= offset < self.size:
            raise ValueError(f"offset {offset} is outside the tile data region")
        file_order = self.levels[::-1]  # Smallest overview's tiles come first.
        starts = [lvl.first_slot_offset for lvl in file_order]
        level = file_order[bisect.bisect_right(starts, offset) - 1]
        index = (offset - level.first_slot_offset) // self.slot_bytes
        assert index < level.tile_count
        row, col = divmod(index, level.tiles_across)
        return TileRef(level.index, col, row,
                       level.first_slot_offset + index * self.slot_bytes)

    def slot_needs_data(self, lo: int, hi: int) -> bool:
        """Whether bytes ``[lo, hi)`` of a slot include tile data or trailer."""
        return lo < _LEADER_BYTES + self.tile_bytes + _TRAILER_BYTES and hi > _LEADER_BYTES

    def read_slot(self, lo: int, hi: int, data: Callable[[], bytes]) -> bytes:
        """Bytes ``[lo, hi)`` of a tile slot.

        ``data`` returns the tile's ``tile_bytes`` of pixel data. It is called
        only when ``slot_needs_data(lo, hi)``.
        """
        if not 0 <= lo <= hi <= self.slot_bytes:
            raise ValueError(f"[{lo}, {hi}) is outside a slot of {self.slot_bytes} bytes")
        t = self.tile_bytes
        tile = data() if self.slot_needs_data(lo, hi) else b""
        if tile and len(tile) != t:
            raise ValueError(f"tile data is {len(tile)} bytes, expected {t}")
        pieces = (
            (0, _LEADER_BYTES, lambda a, b: struct.pack("<I", t)[a:b]),
            (_LEADER_BYTES, _LEADER_BYTES + t, lambda a, b: tile[a:b]),
            (_LEADER_BYTES + t, _LEADER_BYTES + t + _TRAILER_BYTES,
             lambda a, b: tile[t - _TRAILER_BYTES:][a:b]),
            (_LEADER_BYTES + t + _TRAILER_BYTES, self.slot_bytes,
             lambda a, b: bytes(b - a)),
        )
        out = bytearray()
        for start, stop, read in pieces:
            a, b = max(lo, start), min(hi, stop)
            if a < b:
                out += read(a - start, b - start)
        return bytes(out)


def level_dimensions(width: int, height: int, tile_size: int) -> list[tuple[int, int]]:
    """Full-res dimensions followed by overview dimensions.

    Matches the GDAL COG driver: halve (rounding up) until the level fits in a
    single tile.
    """
    dims = [(width, height)]
    factor = 1
    while dims[-1][0] > tile_size or dims[-1][1] > tile_size:
        factor *= 2
        dims.append((-(-width // factor), -(-height // factor)))
    return dims


def plan_layout(raster: RasterDescription, tile_size: int = 512,
                bigtiff: bool | None = None) -> CogLayout:
    """Plan the layout. ``bigtiff=None`` picks classic TIFF when it fits."""
    if tile_size < 16 or tile_size % 16:
        raise ValueError("TIFF tile dimensions must be positive multiples of 16")
    if bigtiff is None:
        try:
            return _plan(raster, tile_size, tiff.CLASSIC)
        except _TooLargeForClassicTiff:
            return _plan(raster, tile_size, tiff.BIGTIFF)
    return _plan(raster, tile_size, tiff.BIGTIFF if bigtiff else tiff.CLASSIC)


class _TooLargeForClassicTiff(Exception):
    pass


def _round_up(n: int, multiple: int) -> int:
    return n + (-n % multiple)


def _plan(raster: RasterDescription, tile_size: int, fmt: TiffFormat) -> CogLayout:
    dtype = np.dtype(raster.dtype)
    tile_bytes = tile_size * tile_size * raster.band_count * dtype.itemsize
    # Trailer chunk(s) end before the chunk holding the next tile's leader.
    slot_bytes = (_round_up(_LEADER_BYTES + tile_bytes + _TRAILER_BYTES, CHUNK_ALIGNMENT)
                  + CHUNK_ALIGNMENT)

    dims = level_dimensions(raster.width, raster.height, tile_size)
    grid = [(-(-w // tile_size), -(-h // tile_size)) for w, h in dims]

    def ifd_tags(level: int, tile_offsets: list[int]) -> list[Tag]:
        w, h = dims[level]
        n = raster.band_count
        tags = [
            tiff.long_tag(tiff.NEW_SUBFILE_TYPE, [0 if level == 0 else 1]),
            tiff.long_tag(tiff.IMAGE_WIDTH, [w]),
            tiff.long_tag(tiff.IMAGE_LENGTH, [h]),
            tiff.short_tag(tiff.BITS_PER_SAMPLE, [dtype.itemsize * 8] * n),
            tiff.short_tag(tiff.COMPRESSION, [1]),
            tiff.short_tag(tiff.SAMPLES_PER_PIXEL, [n]),
            tiff.short_tag(tiff.PLANAR_CONFIGURATION, [1]),
            tiff.long_tag(tiff.TILE_WIDTH, [tile_size]),
            tiff.long_tag(tiff.TILE_LENGTH, [tile_size]),
            fmt.offsets_tag(tiff.TILE_OFFSETS, tile_offsets),
            fmt.offsets_tag(tiff.TILE_BYTE_COUNTS, [tile_bytes] * len(tile_offsets)),
            tiff.short_tag(tiff.SAMPLE_FORMAT, [_SAMPLE_FORMATS[dtype.kind]] * n),
        ]
        copied = list(raster.all_ifd_tags)
        if level == 0:
            copied += raster.main_ifd_tags
        codes = {t.code for t in copied}
        if tiff.PHOTOMETRIC not in codes:
            tags.append(tiff.short_tag(tiff.PHOTOMETRIC, [1]))  # MinIsBlack
        if tiff.EXTRA_SAMPLES not in codes and n > 1:
            tags.append(tiff.short_tag(tiff.EXTRA_SAMPLES, [0] * (n - 1)))
        layout_codes = {t.code for t in tags}
        if codes & layout_codes:
            raise ValueError(f"copied tags conflict with layout tags: {codes & layout_codes}")
        return tags + copied

    # Pass 1: IFD sizes do not depend on offset values, only on their count.
    ghost = (_GHOST_FIRST_LINE % len(_GHOST_BODY)) + _GHOST_BODY
    first_ifd = fmt.header_size + len(ghost)
    first_ifd += first_ifd % 2
    ifd_offsets = []
    pos = first_ifd
    for level, (across, down) in enumerate(grid):
        pos += pos % 2
        ifd_offsets.append(pos)
        pos += fmt.ifd_size(ifd_tags(level, [0] * (across * down)))

    # Tile slots: smallest overview first, full resolution last. The first
    # tile's data is aligned; its leader sits at the end of the prefix.
    data_start = _round_up(pos + _LEADER_BYTES, CHUNK_ALIGNMENT)
    first_slot = data_start - _LEADER_BYTES
    level_starts = [0] * len(dims)
    pos = first_slot
    for level in reversed(range(len(dims))):
        level_starts[level] = pos
        pos += grid[level][0] * grid[level][1] * slot_bytes
    size = pos
    if size - 1 > fmt.max_offset or tile_bytes > fmt.max_offset:
        raise _TooLargeForClassicTiff()

    # Pass 2: encode with real offsets.
    prefix = bytearray(fmt.header(first_ifd))
    prefix += ghost.encode("ascii")
    for level, (across, down) in enumerate(grid):
        prefix += b"\0" * (ifd_offsets[level] - len(prefix))
        offsets = [level_starts[level] + i * slot_bytes + _LEADER_BYTES
                   for i in range(across * down)]
        nxt = ifd_offsets[level + 1] if level + 1 < len(dims) else 0
        prefix += fmt.encode_ifd(ifd_tags(level, offsets), ifd_offsets[level], nxt)
    prefix += b"\0" * (first_slot - len(prefix))

    levels = tuple(
        Level(i, w, h, grid[i][0], grid[i][1], level_starts[i])
        for i, (w, h) in enumerate(dims))
    return CogLayout(raster=raster, tile_size=tile_size, tile_bytes=tile_bytes,
                     slot_bytes=slot_bytes, bigtiff=fmt.bigtiff, levels=levels,
                     prefix=bytes(prefix), size=size)
