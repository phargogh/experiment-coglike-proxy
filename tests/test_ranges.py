import pytest

from coglike_proxy.ranges import parse_range_header


@pytest.mark.parametrize("header, expected", [
    (None, None),
    ("bytes=0-99", [(0, 100)]),
    ("bytes=10-", [(10, 1000)]),
    ("bytes=-100", [(900, 1000)]),
    ("bytes=-5000", [(0, 1000)]),
    ("bytes=990-5000", [(990, 1000)]),
    ("bytes=0-0, 5-9", [(0, 1), (5, 10)]),
    ("Bytes = 0-1", [(0, 2)]),
    ("bytes=1000-", []),           # unsatisfiable
    ("bytes=-0", []),
    ("bytes=2000-3000, 0-1", [(0, 2)]),  # unsatisfiable parts are dropped
    ("bytes=5-1", None),           # invalid: ignored
    ("bytes=abc", None),
    ("bytes=-", None),
    ("bytes=1-2-3", None),
    ("items=0-1", None),
    ("0-1", None),
])
def test_parse_range_header(header, expected):
    assert parse_range_header(header, 1000) == expected
