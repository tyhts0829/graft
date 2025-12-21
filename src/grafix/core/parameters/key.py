# どこで: `src/grafix/core/parameters/key.py`。
# 何を: ParameterKey と site_id 生成ヘルパを定義する。
# なぜ: GUI 行を安定に識別し、呼び出し箇所ごとにキーを分離するため。

from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from types import FrameType


@dataclass(frozen=True, slots=True)
class ParameterKey:
    """パラメータ GUI 行を一意に識別するキー。"""

    op: str
    site_id: str
    arg: str


def make_site_id(frame: FrameType | None = None) -> str:
    """フレーム情報から site_id を生成する。

    site_id の形式: ``\"{filename}:{co_firstlineno}:{f_lasti}\"``。
    """

    if frame is None:
        frame = inspect.currentframe()
        if frame is not None:
            frame = frame.f_back  # 呼び出し元を指す
    if frame is None:
        return "<unknown>:0:0"
    code = frame.f_code
    filename = str(code.co_filename)
    if filename and not filename.startswith("<"):
        try:
            filename = str(Path(filename).resolve())
        except Exception:
            pass
    return f"{filename}:{code.co_firstlineno}:{frame.f_lasti}"


def caller_site_id(skip: int = 1) -> str:
    """呼び出し元スタックから site_id を取得する。

    Parameters
    ----------
    skip : int
        何フレーム遡るか。1 でこの関数の呼び出し元。
    """

    frame: FrameType | None = inspect.currentframe()
    for _ in range(skip + 1):
        if frame is None:
            break
        frame = frame.f_back
    if frame is None:
        return "<unknown>:0:0"
    return make_site_id(frame)
