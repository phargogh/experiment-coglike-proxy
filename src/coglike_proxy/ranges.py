"""Parse HTTP ``Range`` headers (RFC 9110, section 14)."""

from __future__ import annotations

UNSATISFIABLE: list[tuple[int, int]] = []


def parse_range_header(value: str | None, size: int) -> list[tuple[int, int]] | None:
    """Return half-open byte ranges ``[(start, stop), ...]`` within ``size``.

    * ``None``: no usable Range header; serve the whole representation.
      Syntactically invalid headers and non-byte units are ignored, as RFC 9110
      requires.
    * ``[]`` (``UNSATISFIABLE``): valid, but no range overlaps the content.
    """
    if value is None:
        return None
    unit, sep, specs = value.partition("=")
    if not sep or unit.strip().lower() != "bytes":
        return None
    ranges: list[tuple[int, int]] = []
    for spec in specs.split(","):
        spec = spec.strip()
        first, dash, last = spec.partition("-")
        if not dash or not (first.isdigit() or last.isdigit()):
            return None
        if first and not first.isdigit() or last and not last.isdigit():
            return None
        if first:
            start = int(first)
            if last:
                end = int(last)
                if end < start:
                    return None
                stop = min(end + 1, size)
            else:
                stop = size
            if start >= size:
                continue  # This range is unsatisfiable; others may not be.
        else:
            suffix = int(last)
            if suffix == 0 or size == 0:
                continue
            start, stop = max(0, size - suffix), size
        ranges.append((start, stop))
    return ranges
