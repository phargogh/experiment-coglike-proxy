from __future__ import annotations

import threading
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import numpy as np
import pytest
from osgeo import gdal, osr

from coglike_proxy.wsgi import create_app

gdal.UseExceptions()

UTM10N = 32610


def write_raster(path: Path, pixels: np.ndarray, *, origin=(500000.0, 4200000.0),
                 pixel_size=30.0, epsg=UTM10N, nodata=None,
                 color_table: gdal.ColorTable | None = None,
                 color_interp: list[int] | None = None) -> Path:
    """Write ``pixels`` (bands, rows, cols) as a GeoTIFF."""
    from osgeo import gdal_array
    bands, rows, cols = pixels.shape
    gdal_type = gdal_array.NumericTypeCodeToGDALTypeCode(pixels.dtype)
    ds = gdal.GetDriverByName("GTiff").Create(str(path), cols, rows, bands, gdal_type)
    ds.SetGeoTransform([origin[0], pixel_size, 0, origin[1], 0, -pixel_size])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    ds.SetSpatialRef(srs)
    if color_table is not None:  # Must precede pixel writes in GTiff.
        ds.GetRasterBand(1).SetColorTable(color_table)
    for i in range(bands):
        band = ds.GetRasterBand(i + 1)
        if color_interp:
            band.SetColorInterpretation(color_interp[i])
        if nodata is not None:
            band.SetNoDataValue(nodata)
        band.WriteArray(pixels[i])
    ds.Close()
    return path


def mosaic_vrt(root: Path, name: str, pixels: np.ndarray, *, parts: int = 2,
               **raster_kwargs) -> Path:
    """Split ``pixels`` into ``parts`` side-by-side GeoTIFFs and mosaic them in a VRT."""
    root.mkdir(parents=True, exist_ok=True)
    cols = pixels.shape[2]
    edges = np.linspace(0, cols, parts + 1).astype(int)
    pixel_size = raster_kwargs.pop("pixel_size", 30.0)
    x0, y0 = raster_kwargs.pop("origin", (500000.0, 4200000.0))
    sources = []
    for i, (a, b) in enumerate(zip(edges[:-1], edges[1:])):
        sources.append(str(write_raster(
            root / f"{name}_part{i}.tif", pixels[:, :, a:b],
            origin=(x0 + a * pixel_size, y0), pixel_size=pixel_size, **raster_kwargs)))
    vrt = root / f"{name}.vrt"
    gdal.BuildVRT(str(vrt), sources).Close()
    return vrt


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - signature from stdlib
        pass


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


@pytest.fixture
def serve():
    """Start the service over ``root`` on an ephemeral port; yield its base URL."""
    servers = []

    def start(root: Path, **options) -> str:
        app = create_app(root, **options)
        httpd = make_server("127.0.0.1", 0, app, server_class=_ThreadingWSGIServer,
                            handler_class=_QuietHandler)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02},
                                  daemon=True)
        thread.start()
        servers.append((httpd, thread))
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield start
    for httpd, thread in servers:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    gdal.VSICurlClearCache()
