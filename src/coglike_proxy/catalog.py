"""Map request paths to VRT files under a root directory."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

SERVED_SUFFIXES = (".tif", ".tiff")


class VrtCatalog:
    """``/<dir>/<name>.tif`` (or ``.tiff``) maps to ``<root>/<dir>/<name>.vrt``.

    Only regular files that resolve inside ``root`` are served; anything else,
    including path traversal attempts and symlinks leading outside ``root``,
    resolves to ``None``.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise NotADirectoryError(self.root)

    def resolve(self, url_path: str) -> Path | None:
        """``url_path`` is the already percent-decoded request path."""
        if "\0" in url_path or "\\" in url_path:
            return None
        parts = PurePosixPath(url_path).parts
        if parts[:1] == ("/",):
            parts = parts[1:]
        if not parts or any(p in ("..", ".") for p in parts):
            return None
        name = parts[-1]
        suffix = next((s for s in SERVED_SUFFIXES if name.lower().endswith(s)), None)
        if suffix is None or len(name) == len(suffix):
            return None
        candidate = self.root.joinpath(*parts[:-1], name[:-len(suffix)] + ".vrt")
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            return None
        return resolved
