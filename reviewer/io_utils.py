"""Filesystem helpers for the reviewer CLI."""

from __future__ import annotations

from pathlib import Path

# Order matters: try strict before lossy. latin-1 always succeeds, so it is
# the final fallback that guarantees we never crash on a stray byte.
SOURCE_ENCODINGS: tuple[str, ...] = (
    # utf-8-sig first: strips an optional BOM and otherwise behaves like utf-8.
    "utf-8-sig",
    "cp949",
    "euc-kr",
    "latin-1",
)


def read_source_text(path: Path) -> tuple[str, str]:
    """Decode a source file, falling back through common encodings.

    Returns (text, encoding_used). Raises UnicodeDecodeError only if every
    encoding (including latin-1) somehow fails, which in practice never
    happens since latin-1 maps all 256 byte values.
    """
    raw = path.read_bytes()
    last_err: UnicodeDecodeError | None = None
    for enc in SOURCE_ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError as e:
            last_err = e
    assert last_err is not None
    raise last_err
