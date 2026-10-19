"""Assemble the whole virtual file, open it with GDAL, compare with the VRT."""

import uuid

import numpy as np
import pytest
from osgeo import gdal
from osgeo_utils.samples.validate_cloud_optimized_geotiff import validate

from coglike_proxy import source
from coglike_proxy.service import CogLikeService
from conftest import mosaic_vrt


def open_virtual(service: CogLikeService, url_path: str) -> gdal.Dataset:
    cog = service.lookup(url_path)
    name = f"/vsimem/roundtrip/{uuid.uuid4().hex}.tif"
    gdal.FileFromMemBuffer(name, b"".join(service.iter_bytes(cog, 0, cog.size)))
    return gdal.Open(name)


def overview_oracle(vrt: gdal.Dataset, width: int, height: int, alg: str) -> np.ndarray:
    return vrt.ReadAsArray(buf_xsize=width, buf_ysize=height,
                           resample_alg=source.RESAMPLING[alg])


def assert_same_raster(out: gdal.Dataset, vrt: gdal.Dataset) -> None:
    assert (out.RasterXSize, out.RasterYSize, out.RasterCount) == (
        vrt.RasterXSize, vrt.RasterYSize, vrt.RasterCount)
    assert out.GetGeoTransform() == pytest.approx(vrt.GetGeoTransform())
    assert out.GetSpatialRef().IsSame(vrt.GetSpatialRef())
    for i in range(1, vrt.RasterCount + 1):
        assert out.GetRasterBand(i).DataType == vrt.GetRasterBand(i).DataType
        assert out.GetRasterBand(i).GetNoDataValue() == vrt.GetRasterBand(i).GetNoDataValue()
    np.testing.assert_array_equal(out.ReadAsArray(), vrt.ReadAsArray())
    warnings, errors, _ = validate(out, full_check=True)
    assert (warnings, errors) == ([], [])
    assert out.GetMetadataItem("LAYOUT", "IMAGE_STRUCTURE") == "COG"


@pytest.mark.parametrize("dtype, bands", [
    ("uint8", 1), ("int8", 1), ("int16", 2), ("uint32", 1), ("int64", 1),
    ("float32", 3), ("float64", 1),
])
@pytest.mark.parametrize("resampling", ["nearest", "average"])
def test_full_resolution_and_overviews_match_vrt(tmp_path, dtype, bands, resampling):
    rng = np.random.default_rng(42)
    pixels = (rng.random((bands, 512, 768)) * 100).astype(dtype)
    vrt_path = mosaic_vrt(tmp_path, "m", pixels, nodata=0)
    service = CogLikeService(tmp_path, tile_size=128, overview_resampling=resampling)

    out = open_virtual(service, "/m.tif")
    vrt = gdal.Open(str(vrt_path))
    assert_same_raster(out, vrt)

    # Dimensions divisible by every overview factor: per-tile resampling must
    # agree exactly with GDAL resampling the whole VRT in one read.
    band = out.GetRasterBand(1)
    assert band.GetOverviewCount() == 3  # 384x256, 192x128, 96x64
    for i in range(band.GetOverviewCount()):
        ovr = band.GetOverview(i)
        expected = overview_oracle(vrt, ovr.XSize, ovr.YSize, resampling)
        got = np.stack([out.GetRasterBand(b).GetOverview(i).ReadAsArray()
                        for b in range(1, bands + 1)])
        np.testing.assert_array_equal(got, expected.reshape(got.shape))


def test_ragged_dimensions_produce_valid_cog(tmp_path):
    rng = np.random.default_rng(1)
    pixels = rng.integers(1, 6, (1, 333, 1001)).astype("uint8")
    vrt_path = mosaic_vrt(tmp_path, "ragged", pixels, parts=3)
    out = open_virtual(CogLikeService(tmp_path, tile_size=128), "/ragged.tif")
    assert_same_raster(out, gdal.Open(str(vrt_path)))
    band = out.GetRasterBand(1)
    shapes = [(band.GetOverview(i).XSize, band.GetOverview(i).YSize)
              for i in range(band.GetOverviewCount())]
    assert shapes == [(501, 167), (251, 84), (126, 42)]
    # Nearest-neighbour overviews only contain categories present in the source.
    for i in range(band.GetOverviewCount()):
        assert set(np.unique(band.GetOverview(i).ReadAsArray())) <= {1, 2, 3, 4, 5}


def test_forced_bigtiff_reads_identically(tmp_path):
    pixels = np.arange(300 * 200, dtype="uint16").reshape(1, 200, 300)
    vrt_path = mosaic_vrt(tmp_path, "big", pixels)
    service = CogLikeService(tmp_path, tile_size=64, bigtiff=True)
    assert service.lookup("/big.tif").layout.prefix[:4] == b"II+\0"
    assert_same_raster(open_virtual(service, "/big.tif"), gdal.Open(str(vrt_path)))


def test_palette_and_band_metadata_are_preserved(tmp_path):
    table = gdal.ColorTable()
    table.SetColorEntry(1, (255, 0, 0, 255))
    table.SetColorEntry(2, (0, 0, 255, 255))
    pixels = np.ones((1, 100, 100), dtype="uint8")
    pixels[:, 50:] = 2
    vrt_path = mosaic_vrt(tmp_path, "pal", pixels, color_table=table)
    vrt = gdal.Open(str(vrt_path), gdal.GA_Update)
    vrt.GetRasterBand(1).SetDescription("land cover")
    vrt.SetMetadataItem("SOURCE", "unit test")
    vrt.Close()

    out = open_virtual(CogLikeService(tmp_path, tile_size=32), "/pal.tif")
    band = out.GetRasterBand(1)
    assert band.GetColorInterpretation() == gdal.GCI_PaletteIndex
    assert band.GetColorTable().GetColorEntry(2) == (0, 0, 255, 255)
    assert band.GetDescription() == "land cover"
    assert out.GetMetadataItem("SOURCE") == "unit test"
    assert band.GetOverview(0).GetColorInterpretation() == gdal.GCI_PaletteIndex


def test_rgba_photometric_is_preserved(tmp_path):
    pixels = np.full((4, 64, 64), 200, dtype="uint8")
    interp = [gdal.GCI_RedBand, gdal.GCI_GreenBand, gdal.GCI_BlueBand, gdal.GCI_AlphaBand]
    vrt_path = mosaic_vrt(tmp_path, "rgba", pixels, color_interp=interp)
    out = open_virtual(CogLikeService(tmp_path, tile_size=32), "/rgba.tif")
    assert [out.GetRasterBand(i).GetColorInterpretation() for i in range(1, 5)] == interp
    np.testing.assert_array_equal(out.ReadAsArray(), gdal.Open(str(vrt_path)).ReadAsArray())


def test_mixed_band_types_are_unsupported(tmp_path):
    vrt = gdal.GetDriverByName("VRT").Create(str(tmp_path / "mixed.vrt"), 10, 10, 1, gdal.GDT_Byte)
    vrt.AddBand(gdal.GDT_Float32)
    vrt.Close()
    with pytest.raises(source.UnsupportedRaster, match="differing"):
        CogLikeService(tmp_path).lookup("/mixed.tif")


def test_header_reads_do_not_open_gdal(tmp_path, monkeypatch):
    mosaic_vrt(tmp_path, "m", np.zeros((1, 64, 64), dtype="uint8"))
    service = CogLikeService(tmp_path, tile_size=32)
    cog = service.lookup("/m.tif")
    opened = []
    real_open = source.open_raster
    monkeypatch.setattr(source, "open_raster", lambda p: opened.append(p) or real_open(p))
    b"".join(service.iter_bytes(cog, 0, len(cog.layout.prefix)))
    assert opened == []
    b"".join(service.iter_bytes(cog, 0, cog.size))
    b"".join(service.iter_bytes(cog, 0, cog.size))  # Second pass is served from cache.
    assert len(opened) == 1
