"""End to end: GDAL reads the service over HTTP via /vsicurl/."""

import http.client
import os
import re
import time
import urllib.parse

import numpy as np
import pytest
from osgeo import gdal
from osgeo_utils.samples.validate_cloud_optimized_geotiff import validate

from conftest import mosaic_vrt


@pytest.fixture
def mosaic(tmp_path):
    rng = np.random.default_rng(7)
    pixels = rng.integers(0, 1000, (2, 600, 900)).astype("int16")
    vrt = mosaic_vrt(tmp_path / "data" / "region", "elevation", pixels, nodata=-1)
    return tmp_path / "data", vrt


def request(base, method, path, headers=None):
    url = urllib.parse.urlsplit(base)
    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=30)
    try:
        conn.request(method, path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


@pytest.mark.parametrize("config", [
    {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR"},
    {"GDAL_DISABLE_READDIR_ON_OPEN": "TRUE"},
    {"GDAL_HTTP_MULTIRANGE": "SINGLE_GET"},  # GDAL asks for multipart/byteranges
    {"GDAL_HTTP_MULTIRANGE": "SERIAL"},
    {"CPL_VSIL_CURL_CHUNK_SIZE": "100000"},  # not the chunk size slots assume
], ids=lambda c: "-".join(f"{k}={v}" for k, v in c.items()))
def test_gdal_reads_virtual_cog_over_http(serve, mosaic, config):
    root, vrt_path = mosaic
    base = serve(root, tile_size=128)
    with gdal.config_options(config):
        remote = gdal.Open(f"/vsicurl/{base}/region/elevation.tif")
        vrt = gdal.Open(str(vrt_path))
        assert remote.GetDriver().ShortName == "GTiff"
        assert remote.GetGeoTransform() == vrt.GetGeoTransform()
        assert remote.GetSpatialRef().IsSame(vrt.GetSpatialRef())
        assert remote.GetRasterBand(2).GetNoDataValue() == -1
        np.testing.assert_array_equal(remote.ReadAsArray(), vrt.ReadAsArray())
        # A window read, as a tiling client would do.
        np.testing.assert_array_equal(remote.ReadAsArray(130, 250, 300, 100),
                                      vrt.ReadAsArray(130, 250, 300, 100))
        ovr = remote.GetRasterBand(1).GetOverview(1)
        np.testing.assert_array_equal(
            ovr.ReadAsArray(),
            vrt.GetRasterBand(1).ReadAsArray(buf_xsize=ovr.XSize, buf_ysize=ovr.YSize))
        assert validate(remote, full_check=True)[:2] == ([], [])
        assert remote.GetMetadataItem("LAYOUT", "IMAGE_STRUCTURE") == "COG"


def test_opening_renders_no_tiles(serve, mosaic, monkeypatch):
    from coglike_proxy import source
    rendered = []
    real_render = source.render_tile
    monkeypatch.setattr(source, "render_tile",
                        lambda ds, layout, *tile, **kw: rendered.append(tile)
                        or real_render(ds, layout, *tile, **kw))
    base = serve(mosaic[0], tile_size=128)
    remote = gdal.Open(f"/vsicurl/{base}/region/elevation.tif")
    assert remote.RasterXSize == 900 and remote.GetRasterBand(1).GetOverviewCount() == 3
    assert rendered == []
    remote.ReadAsArray(0, 0, 10, 10)
    assert rendered == [(0, 0, 0, "nearest")]  # only full-resolution tile (0, 0)
    remote.GetRasterBand(1).GetOverview(0).ReadAsArray(200, 150, 10, 10)
    assert rendered[1:] == [(1, 1, 1, "nearest")]


def test_head_and_byte_ranges(serve, mosaic):
    base = serve(mosaic[0], tile_size=128)
    path = "/region/elevation.tif"
    status, headers, body = request(base, "HEAD", path)
    assert status == 200 and body == b""
    assert headers["Accept-Ranges"] == "bytes"
    size = int(headers["Content-Length"])

    status, _, full = request(base, "GET", path)
    assert status == 200 and len(full) == size and full[:4] == b"II*\0"

    status, headers, body = request(base, "GET", path, {"Range": "bytes=100-199"})
    assert status == 206 and body == full[100:200]
    assert headers["Content-Range"] == f"bytes 100-199/{size}"

    status, headers, body = request(base, "GET", path, {"Range": "bytes=-10"})
    assert status == 206 and body == full[-10:]

    status, headers, body = request(base, "HEAD", path, {"Range": "bytes=0-9"})
    assert status == 206 and headers["Content-Length"] == "10" and body == b""

    status, headers, _ = request(base, "GET", path, {"Range": f"bytes={size}-"})
    assert status == 416 and headers["Content-Range"] == f"bytes */{size}"

    status, headers, body = request(base, "GET", path, {"Range": "bytes=0-3, 5000-5099"})
    assert status == 206
    boundary = re.fullmatch(r"multipart/byteranges; boundary=(\w+)",
                            headers["Content-Type"]).group(1)
    assert int(headers["Content-Length"]) == len(body)
    parts = body.split(f"--{boundary}".encode())
    assert parts[-1] == b"--\r\n"
    contents = [p.split(b"\r\n\r\n", 1)[1][:-2] for p in parts[1:-1]]
    assert contents == [full[0:4], full[5000:5100]]
    assert f"Content-Range: bytes 5000-5099/{size}".encode() in parts[2]


@pytest.mark.parametrize("method, path, status", [
    ("GET", "/region/missing.tif", 404),
    ("GET", "/region/elevation.vrt", 404),
    ("GET", "/region/elevation_part0.tif", 404),  # real GeoTIFFs are not exposed
    ("GET", "/region/../region/elevation.tif", 404),
    ("GET", "/%2e%2e/etc/passwd.tif", 404),
    ("GET", "/region/", 404),
    ("POST", "/region/elevation.tif", 405),
])
def test_error_statuses(serve, mosaic, method, path, status):
    got, headers, _ = request(serve(mosaic[0]), method, path)
    assert got == status
    if status == 405:
        assert headers["Allow"] == "GET, HEAD"


def test_unservable_vrt_is_a_server_error(serve, tmp_path):
    vrt = gdal.GetDriverByName("VRT").Create(str(tmp_path / "c.vrt"), 4, 4, 1, gdal.GDT_CFloat32)
    vrt.Close()
    (tmp_path / "broken.vrt").write_text("not xml")
    base = serve(tmp_path)
    status, _, body = request(base, "GET", "/c.tif")
    assert status == 500 and b"not supported" in body
    assert request(base, "GET", "/broken.tif")[0] == 500


def test_editing_the_vrt_changes_etag_and_content(serve, tmp_path):
    vrt = mosaic_vrt(tmp_path, "m", np.zeros((1, 64, 64), dtype="uint8"))
    base = serve(tmp_path, tile_size=32)
    _, before, _ = request(base, "HEAD", "/m.tif")
    gdal.BuildVRT(str(vrt), [str(tmp_path / "m_part0.tif")]).Close()  # half the width
    later = time.time() + 5
    os.utime(vrt, (later, later))
    _, after, _ = request(base, "HEAD", "/m.tif")
    assert before["ETag"] != after["ETag"]
    assert int(after["Content-Length"]) < int(before["Content-Length"])


def test_concurrent_clients(serve, mosaic):
    from concurrent.futures import ThreadPoolExecutor
    root, vrt_path = mosaic
    base = serve(root, tile_size=128)
    windows = [(x, y, 97, 83) for x in range(0, 800, 160) for y in range(0, 500, 120)]

    def read(window):
        ds = gdal.Open(f"/vsicurl/{base}/region/elevation.tif")
        try:
            return ds.ReadAsArray(*window)
        finally:
            ds.Close()

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(read, windows))
    vrt = gdal.Open(str(vrt_path))
    for window, got in zip(windows, results):
        np.testing.assert_array_equal(got, vrt.ReadAsArray(*window))
