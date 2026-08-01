"""Filesystem helpers shared by the export, quantize and analysis paths."""

from __future__ import annotations

from pathlib import Path


def size_mb(path: str | Path) -> float:
    """Return the size of ``path`` in MB.

    A directory is summed recursively, because a HuggingFace export is a
    directory of shards plus a tokenizer and only the total is meaningful.

    Args:
        path (str | Path): File or directory to measure.

    Returns:
        float: Size in MB, or ``0.0`` when the path does not exist — a
        missing side artifact should not crash a report.
    """
    target = Path(path)
    if target.is_file():
        return target.stat().st_size / 1024.0**2
    if target.is_dir():
        total = sum(item.stat().st_size for item in target.rglob("*") if item.is_file())
        return total / 1024.0**2
    return 0.0


def size_ratio(source_mb: float, output_mb: float) -> float:
    """Return ``source_mb / output_mb``, guarding against a zero divisor.

    Args:
        source_mb (float): Size before the transformation.
        output_mb (float): Size after it.

    Returns:
        float: Ratio above 1.0 when the output shrank; ``1.0`` when either
        size is zero and the comparison would be meaningless.
    """
    if source_mb <= 0.0 or output_mb <= 0.0:
        return 1.0
    return source_mb / output_mb


__all__: list[str] = ["size_mb", "size_ratio"]
