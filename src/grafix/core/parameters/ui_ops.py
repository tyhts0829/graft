# どこで: `src/grafix/core/parameters/ui_ops.py`。
# 何を: UI 入力（文字列/数値/タプル等）を ParamState へ反映する更新手続きを提供する。
# なぜ: ParamState の参照リークを避け、更新経路を ops に固定するため。

from __future__ import annotations

from typing import Any

from .key import ParameterKey
from .meta import ParamMeta
from .store import ParamStore
from .view import normalize_input

_KEEP = object()


def update_state_from_ui(
    store: ParamStore,
    key: ParameterKey,
    ui_input_value: Any,
    *,
    meta: ParamMeta,
    override: bool | None = None,
    cc_key: int | tuple[int | None, int | None, int | None] | None | object = _KEEP,
) -> tuple[bool, str | None]:
    """UI から渡された入力を正規化し、対応する ParamState に反映する。"""

    normalized, err = normalize_input(ui_input_value, meta)
    if err and normalized is None:
        return False, err

    state = store._ensure_state(
        key, base_value=ui_input_value if normalized is None else normalized
    )
    if normalized is not None:
        state.ui_value = normalized
    if override is not None:
        state.override = bool(override)

    if cc_key is not _KEEP:
        if cc_key is None:
            state.cc_key = None
        elif isinstance(cc_key, int):
            state.cc_key = int(cc_key)
        else:
            if len(cc_key) != 3:  # type: ignore[arg-type]
                raise ValueError(f"vec3 cc_key must be length-3: {cc_key!r}")
            a, b, c = cc_key  # type: ignore[misc]
            cc_tuple = (
                None if a is None else int(a),
                None if b is None else int(b),
                None if c is None else int(c),
            )
            state.cc_key = None if cc_tuple == (None, None, None) else cc_tuple

    return True, err


__all__ = ["update_state_from_ui"]

