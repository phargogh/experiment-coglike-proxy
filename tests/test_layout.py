import struct

import numpy as np
import pytest

from coglike_proxy import tiff
from coglike_proxy.layout import (CHUNK_ALIGNMENT, RasterDescription, level_dimensions,
                                  plan_layout)


def test_overview_levels_halve_until_one_tile():
    assert level_dimensions(1000, 700, 256) == [(1000, 700), (500, 350), (250, 175)]
    assert level_dimensions(256, 256, 256) == [(256, 256)]
    assert level_dimensions(257, 10, 256) == [(257, 10), (129, 5)]


def test_tile_slots_are_contiguous_smallest_overview_first():
    raster = RasterDescription(1000, 700, 3, np.dtype("uint16"))
    layout = plan_layout(raster, tile_size=256)
    assert not layout.bigtiff
    assert layout.tile_bytes == 256 * 256 * 3 * 2
    starts = [lvl.first_slot_offset for lvl in layout.levels]
    assert starts[-1] == len(layout.prefix)
    assert starts == sorted(starts, reverse=True)
    total = sum(lvl.tile_count for lvl in layout.levels) * layout.slot_bytes
    assert layout.size == len(layout.prefix) + total


@pytest.mark.parametrize("tile_size, bands, dtype", [
    (16, 1, "uint8"), (128, 1, "uint8"), (256, 3, "float32"), (512, 1, "int16"),
])
def test_tile_offsets_are_chunk_aligned_and_isolated(tile_size, bands, dtype):
    """Chunk-rounded reads of a tile never reach a neighbour's data or trailer."""
    layout = plan_layout(RasterDescription(3000, 2000, bands, np.dtype(dtype)), tile_size)
    chunk = CHUNK_ALIGNMENT
    assert layout.slot_bytes % chunk == 0
    for level in layout.levels:
        data = level.first_slot_offset + 4
        assert data % chunk == 0
    # Chunks holding tile data or trailer are disjoint from chunks holding the
    # next tile's leader.
    trailer_end = 4 + layout.tile_bytes + 4
    assert -(-trailer_end // chunk) * chunk <= layout.slot_bytes - chunk


def test_locate_tile_covers_every_slot_boundary():
    layout = plan_layout(RasterDescription(600, 300, 1, np.dtype("uint8")), tile_size=256)
    for level in layout.levels:
        for row in range(level.tiles_down):
            for col in range(level.tiles_across):
                offset = (level.first_slot_offset
                          + (row * level.tiles_across + col) * layout.slot_bytes)
                for probe in (offset, offset + layout.slot_bytes - 1):
                    ref = layout.locate_tile(probe)
                    assert (ref.level, ref.col, ref.row, ref.slot_offset) == (
                        level.index, col, row, offset)
    with pytest.raises(ValueError):
        layout.locate_tile(layout.size)
    with pytest.raises(ValueError):
        layout.locate_tile(0)


def test_slot_holds_leader_data_trailer_then_padding():
    layout = plan_layout(RasterDescription(16, 16, 1, np.dtype("uint8")), tile_size=16)
    data = bytes(range(256))
    whole = layout.read_slot(0, layout.slot_bytes, lambda: data)
    assert whole == (struct.pack("<I", 256) + data + data[-4:]
                     + bytes(layout.slot_bytes - 264))
    for lo, hi in [(0, 3), (2, 10), (100, 262), (259, 300), (263, 264)]:
        assert layout.read_slot(lo, hi, lambda: data) == whole[lo:hi]


def test_slot_reads_outside_data_do_not_render():
    layout = plan_layout(RasterDescription(16, 16, 1, np.dtype("uint8")), tile_size=16)

    def fail():
        raise AssertionError("tile rendered")

    assert layout.read_slot(0, 4, fail) == struct.pack("<I", 256)
    assert layout.read_slot(264, layout.slot_bytes, fail) == bytes(layout.slot_bytes - 264)
    with pytest.raises(ValueError):
        layout.read_slot(0, 10, lambda: b"short")


def test_ghost_area_and_main_ifd_position():
    layout = plan_layout(RasterDescription(100, 100, 1, np.dtype("float32")), tile_size=64)
    prefix = layout.prefix
    assert prefix[:4] == b"II*\0"
    first_line = "GDAL_STRUCTURAL_METADATA_SIZE=%06d bytes\n" % 0
    size = int(prefix[8 + 30:8 + 36])
    body = prefix[8 + len(first_line):8 + len(first_line) + size].decode()
    assert "LAYOUT=IFDS_BEFORE_DATA" in body and "BLOCK_ORDER=ROW_MAJOR" in body
    expected_ifd = 8 + len(first_line) + size
    expected_ifd += expected_ifd % 2
    assert struct.unpack_from("<I", prefix, 4)[0] == expected_ifd


def test_bigtiff_is_chosen_only_when_needed_or_forced():
    small = RasterDescription(100, 100, 1, np.dtype("uint8"))
    assert plan_layout(small, 64, bigtiff=True).prefix[:4] == b"II+\0"
    huge = RasterDescription(100_000, 100_000, 1, np.dtype("uint8"))  # > 4 GiB uncompressed
    layout = plan_layout(huge, 512)
    assert layout.bigtiff and layout.size > 2**32
    with pytest.raises(Exception):
        plan_layout(huge, 512, bigtiff=False)


def test_copied_tags_cannot_override_layout_tags():
    bad = RasterDescription(10, 10, 1, np.dtype("uint8"),
                            all_ifd_tags=(tiff.long_tag(tiff.IMAGE_WIDTH, [5]),))
    with pytest.raises(ValueError, match="conflict"):
        plan_layout(bad, 16)


@pytest.mark.parametrize("dtype", ["complex64", "bool"])
def test_unsupported_sample_types_rejected(dtype):
    with pytest.raises(ValueError):
        RasterDescription(10, 10, 1, np.dtype(dtype))
