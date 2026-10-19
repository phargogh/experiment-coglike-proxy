from coglike_proxy.cache import LruCache


def test_evicts_least_recently_used_by_weight():
    cache = LruCache(10, len)
    cache.put("a", b"aaaa")
    cache.put("b", b"bbbb")
    assert cache.get("a") == b"aaaa"  # "b" becomes least recently used
    cache.put("c", b"cccc")
    assert cache.get("b") is None
    assert cache.get("a") == b"aaaa" and cache.get("c") == b"cccc"


def test_oversized_values_are_not_cached_and_evict_nothing():
    cache = LruCache(4, len)
    cache.put("a", b"aa")
    cache.put("big", b"x" * 5)
    assert cache.get("big") is None and cache.get("a") == b"aa"
