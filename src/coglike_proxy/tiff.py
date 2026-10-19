"""Little-endian TIFF / BigTIFF structure encoding.

This module knows nothing about GDAL or HTTP. It encodes image file
directories (IFDs) and file headers, and it can read the first IFD of a small
classic TIFF so that tags written by GDAL (e.g. GeoTIFF keys) can be reused.

Reference: TIFF 6.0 specification and the BigTIFF extension
(https://www.awaresystems.be/imaging/tiff/bigtiff.html).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# TIFF field types used here, and the byte size of one value of each type.
BYTE, ASCII, SHORT, LONG, RATIONAL = 1, 2, 3, 4, 5
SBYTE, UNDEFINED, SSHORT, SLONG, SRATIONAL = 6, 7, 8, 9, 10
FLOAT, DOUBLE, LONG8, SLONG8, IFD8 = 11, 12, 16, 17, 18
TYPE_SIZES = {
    BYTE: 1, ASCII: 1, SHORT: 2, LONG: 4, RATIONAL: 8,
    SBYTE: 1, UNDEFINED: 1, SSHORT: 2, SLONG: 4, SRATIONAL: 8,
    FLOAT: 4, DOUBLE: 8, LONG8: 8, SLONG8: 8, IFD8: 8,
}

# Tag codes.
NEW_SUBFILE_TYPE = 254
IMAGE_WIDTH = 256
IMAGE_LENGTH = 257
BITS_PER_SAMPLE = 258
COMPRESSION = 259
PHOTOMETRIC = 262
SAMPLES_PER_PIXEL = 277
PLANAR_CONFIGURATION = 284
COLOR_MAP = 320
TILE_WIDTH = 322
TILE_LENGTH = 323
TILE_OFFSETS = 324
TILE_BYTE_COUNTS = 325
EXTRA_SAMPLES = 338
SAMPLE_FORMAT = 339
MODEL_PIXEL_SCALE = 33550
MODEL_TIEPOINT = 33922
MODEL_TRANSFORMATION = 34264
GEO_KEY_DIRECTORY = 34735
GEO_DOUBLE_PARAMS = 34736
GEO_ASCII_PARAMS = 34737
GDAL_METADATA = 42112
GDAL_NODATA = 42113


@dataclass(frozen=True)
class Tag:
    """One IFD entry. ``value`` holds ``count`` little-endian encoded values."""

    code: int
    type: int
    count: int
    value: bytes

    def __post_init__(self) -> None:
        if self.type not in TYPE_SIZES:
            raise ValueError(f"unsupported TIFF field type {self.type}")
        if self.count < 1:
            raise ValueError(f"tag {self.code} must have at least one value")
        if len(self.value) != self.count * TYPE_SIZES[self.type]:
            raise ValueError(
                f"tag {self.code}: {len(self.value)} bytes does not match "
                f"{self.count} values of type {self.type}")


def short_tag(code: int, values: list[int]) -> Tag:
    return Tag(code, SHORT, len(values), struct.pack(f"<{len(values)}H", *values))


def long_tag(code: int, values: list[int]) -> Tag:
    return Tag(code, LONG, len(values), struct.pack(f"<{len(values)}I", *values))


def long8_tag(code: int, values: list[int]) -> Tag:
    return Tag(code, LONG8, len(values), struct.pack(f"<{len(values)}Q", *values))


@dataclass(frozen=True)
class TiffFormat:
    """Structural constants that differ between classic TIFF and BigTIFF."""

    bigtiff: bool
    header_size: int
    entry_count_size: int
    entry_size: int
    next_offset_size: int
    inline_value_size: int
    max_offset: int

    def offsets_tag(self, code: int, values: list[int]) -> Tag:
        """Encode file offsets / byte counts with the widest type allowed."""
        return long8_tag(code, values) if self.bigtiff else long_tag(code, values)

    def header(self, first_ifd_offset: int) -> bytes:
        if self.bigtiff:
            return b"II" + struct.pack("<HHHQ", 43, 8, 0, first_ifd_offset)
        return b"II" + struct.pack("<HI", 42, first_ifd_offset)

    def ifd_size(self, tags: list[Tag]) -> int:
        """Bytes occupied by an IFD and its out-of-line values."""
        size = self.entry_count_size + len(tags) * self.entry_size + self.next_offset_size
        for tag in tags:
            if len(tag.value) > self.inline_value_size:
                size += _even(len(tag.value))
        return size

    def encode_ifd(self, tags: list[Tag], ifd_offset: int, next_ifd_offset: int) -> bytes:
        """Encode an IFD located at ``ifd_offset``.

        Out-of-line values are written immediately after the IFD, each padded
        to an even length so every offset stays word aligned. The result is
        exactly ``ifd_size(tags)`` bytes long.
        """
        if ifd_offset % 2:
            raise ValueError("IFD offsets must be word aligned")
        tags = sorted(tags, key=lambda t: t.code)
        codes = [t.code for t in tags]
        if len(set(codes)) != len(codes):
            raise ValueError(f"duplicate tags in IFD: {codes}")

        entries = bytearray()
        extra = bytearray()
        extra_offset = (ifd_offset + self.entry_count_size
                        + len(tags) * self.entry_size + self.next_offset_size)
        count_fmt, ptr_fmt = ("<Q", "<Q") if self.bigtiff else ("<I", "<I")
        for tag in tags:
            entries += struct.pack("<HH", tag.code, tag.type)
            entries += struct.pack(count_fmt, tag.count)
            if len(tag.value) <= self.inline_value_size:
                entries += tag.value.ljust(self.inline_value_size, b"\0")
            else:
                entries += struct.pack(ptr_fmt, extra_offset + len(extra))
                extra += tag.value
                if len(extra) % 2:
                    extra += b"\0"

        count = struct.pack("<Q" if self.bigtiff else "<H", len(tags))
        nxt = struct.pack("<Q" if self.bigtiff else "<I", next_ifd_offset)
        encoded = bytes(count + entries + nxt + extra)
        assert len(encoded) == self.ifd_size(tags)
        assert ifd_offset + len(encoded) <= self.max_offset
        return encoded


CLASSIC = TiffFormat(bigtiff=False, header_size=8, entry_count_size=2,
                     entry_size=12, next_offset_size=4, inline_value_size=4,
                     max_offset=2**32 - 1)
BIGTIFF = TiffFormat(bigtiff=True, header_size=16, entry_count_size=8,
                     entry_size=20, next_offset_size=8, inline_value_size=8,
                     max_offset=2**64 - 1)


def _even(n: int) -> int:
    return n + (n % 2)


def read_first_ifd_tags(data: bytes) -> dict[int, Tag]:
    """Read the tags of the first IFD of a little-endian classic TIFF.

    Intended for small, trusted, GDAL-generated files held in memory.
    """
    if data[:4] != b"II*\0":
        raise ValueError("expected a little-endian classic TIFF")
    (ifd_offset,) = struct.unpack_from("<I", data, 4)
    (n_entries,) = struct.unpack_from("<H", data, ifd_offset)
    tags: dict[int, Tag] = {}
    for i in range(n_entries):
        pos = ifd_offset + 2 + i * 12
        code, ftype, count = struct.unpack_from("<HHI", data, pos)
        if ftype not in TYPE_SIZES:
            continue  # Unknown types cannot be re-encoded; nothing we copy uses them.
        nbytes = count * TYPE_SIZES[ftype]
        if nbytes <= 4:
            value = data[pos + 8:pos + 8 + nbytes]
        else:
            (value_offset,) = struct.unpack_from("<I", data, pos + 8)
            value = data[value_offset:value_offset + nbytes]
        if len(value) != nbytes:
            raise ValueError(f"tag {code} value extends beyond end of file")
        tags[code] = Tag(code, ftype, count, bytes(value))
    return tags
