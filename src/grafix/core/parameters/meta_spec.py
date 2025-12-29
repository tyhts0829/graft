# どこで: `src/grafix/core/parameters/meta_spec.py`。
# 何を: ユーザー入力（dict spec）を `ParamMeta` へ正規化する関数を提供する。
# なぜ: 公開 API が `ParamMeta` import を要求せずに meta を受け取れるようにしつつ、内部表現を `ParamMeta` に統一するため。

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .meta import ParamMeta

_ALLOWED_META_SPEC_KEYS = {"kind", "ui_min", "ui_max", "choices"}


def meta_from_spec(spec: ParamMeta | Mapping[str, object]) -> ParamMeta:
    """dict spec または `ParamMeta` から `ParamMeta` を返す。

    Parameters
    ----------
    spec : ParamMeta | Mapping[str, object]
        `ParamMeta` または dict spec。

        dict spec の形式:
        - kind: str（必須）
        - ui_min/ui_max: object（任意）
        - choices: Sequence[str] | None（任意）

    Raises
    ------
    TypeError
        spec の型が不正な場合。
    ValueError
        必須キー欠落や未知キーなど、spec の内容が不正な場合。
    """

    if isinstance(spec, ParamMeta):
        return spec
    if not isinstance(spec, Mapping):
        raise TypeError("meta spec は ParamMeta または dict である必要があります")

    unknown = set(spec.keys()) - _ALLOWED_META_SPEC_KEYS
    if unknown:
        names = ", ".join(sorted(str(k) for k in unknown))
        raise ValueError(f"meta spec に未知キーがあります: {names}")

    if "kind" not in spec:
        raise ValueError("meta spec には 'kind' が必要です")
    kind = spec["kind"]
    if not isinstance(kind, str):
        raise TypeError("meta spec の 'kind' は str である必要があります")

    ui_min = spec.get("ui_min", None)
    ui_max = spec.get("ui_max", None)

    raw_choices = spec.get("choices", None)
    choices: Sequence[str] | None
    if raw_choices is None:
        choices = None
    else:
        if isinstance(raw_choices, (str, bytes)):
            raise TypeError("meta spec の 'choices' は Sequence[str] である必要があります")
        if not isinstance(raw_choices, Sequence):
            raise TypeError("meta spec の 'choices' は Sequence[str] である必要があります")
        choices = tuple(str(x) for x in raw_choices)

    return ParamMeta(kind=str(kind), ui_min=ui_min, ui_max=ui_max, choices=choices)


def meta_dict_from_user(
    meta: Mapping[str, ParamMeta | Mapping[str, object]],
) -> dict[str, ParamMeta]:
    """ユーザー入力 meta を `dict[str, ParamMeta]` へ正規化して返す。"""

    out: dict[str, ParamMeta] = {}
    for arg, spec in meta.items():
        if not isinstance(arg, str):
            raise TypeError("meta のキー（引数名）は str である必要があります")
        out[arg] = meta_from_spec(spec)
    return out


__all__ = ["meta_from_spec", "meta_dict_from_user"]

