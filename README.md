# coglike-proxy

An HTTP service that serves each VRT in a directory as if it were a Cloud
Optimized GeoTIFF. Nothing is written to disk. The TIFF header is synthesized
from the VRT's metadata, and each tile is read from the VRT, through GDAL,
only when a client requests its bytes.

Clients such as GDAL (`/vsicurl/`), QGIS, and rasterio read it like any other
remote COG, using HTTP range requests.

## URL mapping

With `--root /data/vrt`:

| Request path            | VRT read                    |
|-------------------------|-----------------------------|
| `/landcover.tif`        | `/data/vrt/landcover.vrt`   |
| `/region/dem.tif`       | `/data/vrt/region/dem.vrt`  |
| `/region/dem.tiff`      | `/data/vrt/region/dem.vrt`  |

Anything else returns 404. That includes paths that do not end in `.tif` or
`.tiff`, paths that escape the root (`..` or symlinks out of it), and the
VRT's source files.

## Quick start

```bash
mamba env create -p ./env -f environment.yml
./env/bin/python -m pip install --no-deps -e .
./env/bin/python -m coglike_proxy --root /data/vrt --port 8000
```

```bash
gdalinfo /vsicurl/http://127.0.0.1:8000/region/dem.tif
```

The client can set `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR` so that GDAL does
not probe for sidecar files such as `.aux.xml` and `.msk`. Each probe costs
one 404 request.

### Options

| CLI flag                | Environment variable          | Default   |
|-------------------------|-------------------------------|-----------|
| `--root`                | `COGLIKE_ROOT`                | required  |
| `--tile-size`           | `COGLIKE_TILE_SIZE`           | `512`     |
| `--overview-resampling` | `COGLIKE_OVERVIEW_RESAMPLING` | `nearest` |
| `--bigtiff`             | `COGLIKE_BIGTIFF`             | `auto`    |
| `--tile-cache-mb`       | `COGLIKE_TILE_CACHE_BYTES`    | 256 MiB   |

Overview resampling defaults to `nearest`, which is safe for categorical data
such as land cover. Use `average`, `bilinear`, or `cubic` for continuous
data.

### Production

`python -m coglike_proxy` runs the standard library's threaded WSGI server,
which is meant for development. In production, host the WSGI app with a
threaded WSGI server, either through the factory or through the
environment-configured `application`:

```bash
gunicorn --threads 8 'coglike_proxy.wsgi:create_app("/data/vrt")'
COGLIKE_ROOT=/data/vrt waitress-serve --threads=8 coglike_proxy.wsgi:application
```

Only the standard library server is exercised by the tests. Each request opens
its own GDAL dataset, so worker threads are safe.

## How it works

The virtual file is an uncompressed, tiled, pixel-interleaved GeoTIFF in GDAL's
COG layout. GDAL reports it as `LAYOUT=COG`, and GDAL's
`validate_cloud_optimized_geotiff.py` passes it with no warnings.

```
header | GDAL structural metadata | IFD full-res | IFD ovr 1 … IFD ovr N | padding
tile slots: ovr N … ovr 1 … full-res          (row-major within each level)
tile slot:  leader (4) | tile data | trailer (4) | zero padding
```

* **Uncompressed tiles have a fixed size.** Every tile offset is therefore
  known before any pixel is read. The whole header comes from the VRT's
  dimensions, band count, and data type, so opening the file renders no
  tiles. See `layout.py`.
* **GDAL encodes the georeferencing.** GDAL writes the VRT's top-left pixel to
  an in-memory 1×1 GeoTIFF. Its GeoTIFF keys, nodata, color table,
  photometric interpretation, and `GDAL_METADATA` tags are copied into the
  virtual header. See `source.py`.
* **Tiles are rendered on demand.** A requested tile is read from the VRT with
  `ReadAsArray`. An overview tile is resampled from the matching
  full-resolution window, and GDAL uses the sources' own overviews when they
  exist. Rendered tiles go into an LRU cache bounded by size.
* **Tile slots align with `/vsicurl/` chunks.** GDAL fetches whole 16 KiB
  chunks. Tile data starts on a 16 KiB boundary, and the padding keeps each
  trailer out of the chunk that holds the next tile's leader. A chunk-rounded
  read of one tile then touches only constant bytes of its neighbors, so only
  that tile is rendered. GDAL reads tile sizes from the leaders, and its COG
  reader accepts gaps between tiles.
* **BigTIFF is used when needed.** It is selected automatically when the file
  would exceed 4 GiB, which uncompressed data reaches quickly.
* **HTTP behavior.** The service answers `HEAD` and `GET` with single and
  multipart byte ranges, returns `416` for unsatisfiable ranges, and sends an
  `ETag` and `Last-Modified` derived from the VRT file's mtime and size.

## Design decision: uncompressed tiles

With compressed tiles, the offset of tile *n* depends on the compressed sizes
of every tile before it. The header could then be produced only after
rendering the entire raster, which defeats on-demand serving. Uncompressed
tiles keep the header computable from metadata alone.

The cost is bandwidth. Clients transfer raw pixel bytes, and the virtual file
size equals the uncompressed raster size plus up to 32 KiB of padding per
tile. Reversing this decision would need a different offset strategy, such as
precomputing and storing compressed sizes.

## Limitations

* **Edits during a client session are not detected.** A client that opened
  the file before the VRT was edited will read inconsistent bytes. GDAL does
  not revalidate the `ETag` between range reads. Changes to the VRT's source
  files are not detected at all, because only the VRT file's mtime and size
  identify a version.
* **Deep overviews can be expensive.** The smallest overview tiles resample
  the whole raster. If the sources have no overviews of their own, that reads
  every source pixel the first time.
* **Some rasters are not supported.** Complex data types, bands with differing
  data types, and mask bands are not handled. Unsupported VRTs return `500`
  with a reason.
* **The alignment assumes GDAL's default chunk size.** If a client raises
  `CPL_VSIL_CURL_CHUNK_SIZE`, reads stay correct but may render neighboring
  tiles.
* **There is no authentication, directory listing, or request limit.** A plain
  `GET` of a whole file renders every tile.

## Tests

```bash
./env/bin/python -m pytest
```

The suite has three parts:

* **Unit tests** cover TIFF encoding, layout invariants, range parsing, path
  mapping, and the cache.
* **Round-trip tests** open the assembled bytes with GDAL. They compare pixels,
  overviews, georeferencing, nodata, palettes, and RGBA with the VRT, for
  several data types, ragged dimensions, and BigTIFF, and they run the COG
  validator.
* **End-to-end tests** use GDAL `/vsicurl/` against a live server. They cover
  several `GDAL_HTTP_MULTIRANGE` modes and chunk sizes, concurrent clients,
  HTTP status codes, and a check that opening a file renders no tiles.
