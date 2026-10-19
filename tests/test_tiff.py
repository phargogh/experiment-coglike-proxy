import struct

import pytest

from coglike_proxy import tiff


def test_classic_ifd_roundtrips_through_reader():
    tags = [
        tiff.long_tag(tiff.IMAGE_WIDTH, [70000]),
        tiff.short_tag(tiff.BITS_PER_SAMPLE, [8, 8, 8]),  # 6 bytes: out of line
        tiff.Tag(tiff.GDAL_NODATA, tiff.ASCII, 4, b"-99\0"),  # exactly inline
        tiff.Tag(tiff.GEO_ASCII_PARAMS, tiff.ASCII, 5, b"abcd\0"),  # odd length
        tiff.long_tag(tiff.TILE_OFFSETS, [10, 20, 30]),
    ]
    ifd = tiff.CLASSIC.encode_ifd(tags, ifd_offset=8, next_ifd_offset=0)
    data = tiff.CLASSIC.header(8) + ifd
    assert len(ifd) == tiff.CLASSIC.ifd_size(tags)
    assert tiff.read_first_ifd_tags(data) == {t.code: t for t in tags}


def test_entries_are_sorted_and_duplicates_rejected():
    a, b = tiff.long_tag(300, [1]), tiff.long_tag(256, [2])
    ifd = tiff.CLASSIC.encode_ifd([a, b], 8, 0)
    first, second = struct.unpack_from("<H", ifd, 2)[0], struct.unpack_from("<H", ifd, 14)[0]
    assert (first, second) == (256, 300)
    with pytest.raises(ValueError, match="duplicate"):
        tiff.CLASSIC.encode_ifd([a, a], 8, 0)


def test_bigtiff_inlines_up_to_eight_bytes():
    tags = [tiff.long8_tag(tiff.TILE_OFFSETS, [2**40])]
    ifd = tiff.BIGTIFF.encode_ifd(tags, ifd_offset=16, next_ifd_offset=0)
    assert len(ifd) == 8 + 20 + 8  # no out-of-line data
    assert struct.unpack_from("<Q", ifd, 8 + 12)[0] == 2**40
    assert tiff.BIGTIFF.header(16) == b"II+\0\x08\0\0\0" + struct.pack("<Q", 16)


def test_tag_value_length_is_checked():
    with pytest.raises(ValueError):
        tiff.Tag(tiff.IMAGE_WIDTH, tiff.LONG, 2, b"\0" * 4)
