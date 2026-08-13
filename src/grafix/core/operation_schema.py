"""
Purpose:
    operation の parameter metadata・default・表示順・UI visibility を evaluator から切り離した immutable schema にする。
Use when:
    decorator schema validation、Parameter GUI catalog、selector schema、schema fingerprint を変更・調査する場合。
Constraints:
    - meta/default は同じ argument 集合を持ち、``param_order`` はそれを過不足なく含める。
    - 入力 mapping は copy して固定し、evaluation catalog や mutable builder へ逆参照しない。
    - ``ui_visible`` predicate 自体の state ownership は登録側に留める。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from grafix.core.parameters.identity import identity_string
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.validation import validate_parameter_value

UiVisiblePred = Callable[[Mapping[str, Any]], bool]
"""現在の parameter 値から GUI 上の表示可否を返す述語。"""


@dataclass(frozen=True, slots=True)
class ParameterOpSchema:
    """1 operation の parameter metadata と default を束ねた不変 schema。

    Notes
    -----
    入力 mapping はコピーして固定する。``ui_visible`` の predicate 自体は実行時
    policy であるため同一 callable を保持し、closure 内部状態の所有は登録側が担う。
    """

    meta: Mapping[str, ParamMeta]
    defaults: Mapping[str, Any]
    param_order: tuple[str, ...]
    ui_visible: Mapping[str, UiVisiblePred]

    def __post_init__(self) -> None:
        """schema の整合性を検証し、値を正規化して固定する。"""

        meta: dict[str, ParamMeta] = {}
        for raw_name, raw_meta in self.meta.items():
            name = identity_string(raw_name, name="meta argument")
            if type(raw_meta) is not ParamMeta:
                raise TypeError(f"meta[{name!r}] は ParamMeta である必要があります")
            meta[name] = raw_meta

        raw_defaults: dict[str, Any] = {}
        for raw_name, raw_default in self.defaults.items():
            name = identity_string(raw_name, name="default argument")
            raw_defaults[name] = raw_default
        if set(raw_defaults) != set(meta):
            missing = sorted(set(meta) - set(raw_defaults))
            extra = sorted(set(raw_defaults) - set(meta))
            details: list[str] = []
            if missing:
                details.append(f"default が無い meta: {missing!r}")
            if extra:
                details.append(f"meta が無い default: {extra!r}")
            raise ValueError(
                "meta/default の引数集合が一致しません: " + "; ".join(details)
            )
        defaults = {
            name: validate_parameter_value(
                raw_defaults[name],
                kind=arg_meta.kind,
                choices=arg_meta.choices,
            )
            for name, arg_meta in meta.items()
        }

        param_order = tuple(
            identity_string(name, name="param_order item")
            for name in self.param_order
        )
        if len(param_order) != len(meta) or set(param_order) != set(meta):
            raise ValueError("param_order は meta の引数を過不足なく含める必要がある")

        ui_visible: dict[str, UiVisiblePred] = {}
        for raw_name, predicate in self.ui_visible.items():
            name = identity_string(raw_name, name="ui_visible argument")
            if not callable(predicate):
                raise TypeError(
                    f"ui_visible[{name!r}] は callable である必要があります"
                )
            ui_visible[name] = predicate
        unknown_visible = set(ui_visible) - set(meta)
        if unknown_visible:
            names = ", ".join(sorted(unknown_visible))
            raise ValueError(f"ui_visible の引数は meta に含める必要がある: {names}")

        object.__setattr__(self, "meta", MappingProxyType(meta))
        object.__setattr__(self, "defaults", MappingProxyType(defaults))
        object.__setattr__(self, "param_order", param_order)
        object.__setattr__(self, "ui_visible", MappingProxyType(ui_visible))


__all__ = ["ParameterOpSchema", "UiVisiblePred"]
