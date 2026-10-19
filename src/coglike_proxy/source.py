"""GDAL boundary: describe a raster and render tiles of the virtual COG.

This is the only module that reads pixels. GDAL datasets are not thread safe,
so callers own dataset handles and must not share one across threads.

Georeferencing and metadata are not encoded by hand: GDAL writes a 1x1-pixel
GeoTIFF of the raster's top-left corner to memory, and the relevant tags of
that file are copied into the virtual COG. Since the corner pixel shares the
raster's origin and pixel size, its georeferencing tags are valid for the full
raster, and the encoding stays exactly what GDAL itself would write.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np
from osgeo import gdal, gdal_array

from . import tiff
from .layout import CogLayout, RasterDescription

gdal.UseExceptions()

RESAMPLING = {
    "nearest": gdal.GRIORA_NearestNeighbour,
    "bilinear": gdal.GRIORA_Bilinear,
    "cubic": gdal.GRIORA_Cubic,
    "cubicspline": gdal.GRIORA_CubicSpline,
    "lanczos": gdal.GRIORA_Lanczos,
    "average": gdal.GRIORA_Average,
    "rms": gdal.GRIORA_RMS,
    "mode": gdal.GRIORA_Mode,
    "gauss": gdal.GRIORA_Gauss,
}

# Tags borrowed from the GDAL-written GeoTIFF.
_MAIN_IFD_TAGS = (
    tiff.MODEL_PIXEL_SCALE, tiff.MODEL_TIEPOINT, tiff.MODEL_TRANSFORMATION,
    tiff.GEO_KEY_DIRECTORY, tiff.GEO_DOUBLE_PARAMS, tiff.GEO_ASCII_PARAMS,
    tiff.GDAL_METADATA,
)
_ALL_IFD_TAGS = (tiff.PHOTOMETRIC, tiff.EXTRA_SAMPLES, tiff.COLOR_MAP, tiff.GDAL_NODATA)


class UnsupportedRaster(Exception):
    """The raster cannot be represented as an uncompressed COG by this service."""


class RasterChanged(Exception):
    """The raster no longer matches the layout that was planned for it."""


def open_raster(path: Path) -> gdal.Dataset:
    """Open read-only. The caller owns the handle and must ``Close()`` it."""
    return gdal.OpenEx(str(path), gdal.OF_RASTER | gdal.OF_READONLY)


def describe(ds: gdal.Dataset) -> RasterDescription:
    if ds.RasterCount < 1:
        raise UnsupportedRaster("raster has no bands")
    types = {ds.GetRasterBand(i + 1).DataType for i in range(ds.RasterCount)}
    if len(types) != 1:
        names = sorted(gdal.GetDataTypeName(t) for t in types)
        raise UnsupportedRaster(f"bands have differing data types: {names}")
    (gdal_type,) = types
    numeric = gdal_array.GDALTypeCodeToNumericTypeCode(gdal_type)
    dtype = np.dtype(numeric) if numeric is not None else None
    if dtype is None or dtype.kind not in "uif":
        raise UnsupportedRaster(
            f"data type {gdal.GetDataTypeName(gdal_type)} is not supported")

    tags = _gdal_geotiff_tags(ds)
    return RasterDescription(
        width=ds.RasterXSize,
        height=ds.RasterYSize,
        band_count=ds.RasterCount,
        dtype=dtype,
        main_ifd_tags=tuple(tags[c] for c in _MAIN_IFD_TAGS if c in tags),
        all_ifd_tags=tuple(tags[c] for c in _ALL_IFD_TAGS if c in tags),
    )


def _gdal_geotiff_tags(ds: gdal.Dataset) -> dict[int, tiff.Tag]:
    name = f"/vsimem/coglike_proxy/{uuid.uuid4().hex}.tif"
    try:
        # PAM disabled so nothing is diverted into an .aux.xml we would not see.
        with gdal.config_options({"GDAL_PAM_ENABLED": "NO"}):
            out = gdal.Translate(
                name, ds, format="GTiff", srcWin=[0, 0, 1, 1], maskBand="none",
                creationOptions=["TILED=NO", "COMPRESS=NONE", "BIGTIFF=NO",
                                 "ENDIANNESS=LITTLE", "INTERLEAVE=PIXEL"])
            out.Close()
        with gdal.VSIFile(name, "rb") as f:
            data = f.read()
    finally:
        gdal.Unlink(name)
    return tiff.read_first_ifd_tags(data)


def render_tile(ds: gdal.Dataset, layout: CogLayout, level: int, col: int,
                row: int, resampling: str = "nearest") -> bytes:
    """Return the exact bytes of one tile: ``layout.tile_bytes`` long.

    Overview pixels are resampled from the full-resolution raster over the
    window the overview tile covers; GDAL uses the sources' own overviews
    where they exist. Edge tiles are zero-padded.
    """
    raster = layout.raster
    if (ds.RasterXSize, ds.RasterYSize, ds.RasterCount) != (
            raster.width, raster.height, raster.band_count):
        raise RasterChanged("raster dimensions differ from the planned layout")

    lvl = layout.levels[level]
    ts = layout.tile_size
    x0, y0 = col * ts, row * ts
    if not (0 <= x0 < lvl.width and 0 <= y0 < lvl.height):
        raise ValueError(f"tile ({col}, {row}) is outside level {level}")
    w, h = min(ts, lvl.width - x0), min(ts, lvl.height - y0)

    # Full-resolution window covered by this tile; exact integers at level 0.
    sx, sy = raster.width / lvl.width, raster.height / lvl.height
    xoff, yoff = x0 * sx, y0 * sy
    xsize = min(raster.width, (x0 + w) * sx) - xoff
    ysize = min(raster.height, (y0 + h) * sy) - yoff

    pixels = ds.ReadAsArray(xoff, yoff, xsize, ysize, buf_xsize=w, buf_ysize=h,
                            resample_alg=RESAMPLING[resampling],
                            interleave="pixel")
    pixels = pixels.reshape(h, w, raster.band_count)
    dtype = np.dtype(raster.dtype).newbyteorder("<")
    if pixels.dtype.kind != dtype.kind or pixels.dtype.itemsize != dtype.itemsize:
        raise RasterChanged(f"raster data type {pixels.dtype} differs from {dtype}")

    tile = np.zeros((ts, ts, raster.band_count), dtype=dtype)
    tile[:h, :w, :] = pixels
    data = tile.tobytes()
    assert len(data) == layout.tile_bytes
    return data
