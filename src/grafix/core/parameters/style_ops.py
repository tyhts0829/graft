# どこで: `src/grafix/core/parameters/style_ops.py`。
# 何を: ParamStore の Style エントリ（__style__/__global__）を作成する手続きを提供する。
# なぜ: 更新経路を ops に固定し、snapshot の純粋性（副作用なし）を保つため。

from __future__ import annotations

from typing import Any

from .key import ParameterKey
from .meta import ParamMeta
from .state import ParamState
from .store import ParamStore
from .style import (
    STYLE_BACKGROUND_COLOR,
    STYLE_GLOBAL_LINE_COLOR,
    STYLE_GLOBAL_THICKNESS,
    rgb01_to_rgb255,
    style_key,
)


def ensure_style_entries(
    store: ParamStore,
    *,
    background_color_rgb01: tuple[float, float, float],
    global_thickness: float,
    global_line_color_rgb01: tuple[float, float, float],
) -> None:
    """Style 行を ParamStore に作成し、meta/state を初期化する。"""

    bg255 = rgb01_to_rgb255(background_color_rgb01)
    line255 = rgb01_to_rgb255(global_line_color_rgb01)
    thickness = float(global_thickness)

    # RGB は 0..255 int を正とする（GUI は COLOR_EDIT_UINT8 前提）。
    items: list[tuple[str, Any, ParamMeta]] = [
        (
            STYLE_BACKGROUND_COLOR,
            bg255,
            ParamMeta(
                kind="rgb",
                ui_min=0,
                ui_max=255,
                description="キャンバス全体の背景色を RGB で指定する。",
            ),
        ),
        (
            STYLE_GLOBAL_THICKNESS,
            thickness,
            ParamMeta(
                kind="float",
                ui_min=1e-6,
                ui_max=0.01,
                description="個別指定がない線に適用する既定の線幅を指定する。",
            ),
        ),
        (
            STYLE_GLOBAL_LINE_COLOR,
            line255,
            ParamMeta(
                kind="rgb",
                ui_min=0,
                ui_max=255,
                description="個別指定がない線に適用する既定色を RGB で指定する。",
            ),
        ),
    ]

    base_revision = store.revision
    read = store._read()
    states = read.states()
    meta_by_key = read.all_meta()
    explicit_by_key = read.all_explicit()
    ordinals = read.ordinals()
    before_ordinals = ordinals.as_dict()
    history_keys: list[ParameterKey] = []
    changed = False
    for arg, base_value, meta in items:
        key = style_key(arg)
        if meta_by_key.get(key) != meta:
            meta_by_key[key] = meta
            history_keys.append(key)
            changed = True
        if key not in states:
            states[key] = ParamState(
                override=True,
                ui_value=base_value,
            )
            explicit_by_key[key] = False
            history_keys.append(key)
            changed = True
        ordinals.get_or_assign(key.op, key.site_id)
    changed = changed or ordinals.as_dict() != before_ordinals
    if not changed:
        return
    mutation = store._mutation()
    mutation.prepare_history(
        expected_revision=base_revision,
        keys=tuple(dict.fromkeys(history_keys)),
    )
    mutation.commit_style(
        expected_revision=base_revision,
        states=states,
        meta=meta_by_key,
        explicit_by_key=explicit_by_key,
        ordinals=ordinals,
        value_keys=(),
    )


__all__ = ["ensure_style_entries"]
