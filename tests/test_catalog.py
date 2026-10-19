import os

import pytest

from coglike_proxy.catalog import VrtCatalog


@pytest.fixture
def catalog(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "a.vrt").write_text("<VRTDataset/>")
    (root / "sub" / "b.vrt").write_text("<VRTDataset/>")
    (tmp_path / "outside.vrt").write_text("<VRTDataset/>")
    os.symlink(tmp_path / "outside.vrt", root / "link.vrt")
    (root / "dir.vrt").mkdir()
    return VrtCatalog(root)


def test_maps_tif_paths_to_vrts(catalog):
    assert catalog.resolve("/a.tif") == catalog.root / "a.vrt"
    assert catalog.resolve("/a.TIFF") == catalog.root / "a.vrt"
    assert catalog.resolve("/sub/b.tif") == catalog.root / "sub" / "b.vrt"


@pytest.mark.parametrize("path", [
    "/", "", "/missing.tif", "/a.vrt", "/a", "/.tif", "/../outside.tif",
    "/sub/../a.tif", "/link.tif", "/dir.tif", "/a.tif\0", "/sub\\b.tif",
])
def test_rejects_paths_that_do_not_name_a_vrt_inside_root(catalog, path):
    assert catalog.resolve(path) is None
