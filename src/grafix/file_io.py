"""
Purpose:
    正式pathへ部分書き込みを見せない、小さなatomic file writing primitiveを提供する。
Use when:
    textまたは外部writerの成果物を、成功時だけ正式pathへ確定する場合。
Constraints:
    - work fileをtargetと同じdirectoryに置き、失敗時は正式pathを変更しない。
    - no-clobber経路は既存entryを上書きしない。
Side effects:
    sibling temporary fileを作成し、fsync後に正式pathへ公開する。
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO


@contextmanager
def atomic_output_path(path: str | Path) -> Iterator[Path]:
    """外部 writer 用の sibling temp path を返し、成功時だけ正式 path と置換する。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.stem}.",
        suffix=f".tmp{target.suffix}",
    )
    os.close(fd)
    temp_path = Path(temp_name)
    temp_path.unlink()
    try:
        yield temp_path
        with temp_path.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temp_path, target)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


@contextmanager
def atomic_text_writer(
    path: str | Path,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> Iterator[TextIO]:
    """同じ directory の一時ファイルへ書き、成功時だけ ``path`` と置換する。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline=newline) as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def atomic_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> None:
    """``text`` を atomic に保存する。"""

    with atomic_text_writer(path, encoding=encoding, newline=newline) as stream:
        stream.write(text)


def atomic_write_text_no_clobber(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> None:
    """完成済み一時ファイルを hard link し、既存 path を上書きせず保存する。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline=newline) as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)


__all__ = [
    "atomic_output_path",
    "atomic_text_writer",
    "atomic_write_text",
    "atomic_write_text_no_clobber",
]
