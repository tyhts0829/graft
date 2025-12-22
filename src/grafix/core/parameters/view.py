# どこで: `src/grafix/core/parameters/view.py`。
# 何を: ParamStore スナップショットから UI 行モデルを生成し、UI 入力を正規化する純粋関数群を提供する。
# なぜ: DPG 依存部と切り離し、型変換・検証を単体テスト可能に保つため。

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .key import ParameterKey
from .meta import ParamMeta
from .state import ParamState


@dataclass(frozen=True, slots=True)
class ParameterRow:
    """GUI 表示用の行モデル。"""

    label: str
    op: str
    site_id: str
    arg: str
    kind: str
    ui_value: Any
    ui_min: Any | None
    ui_max: Any | None
    choices: Sequence[str] | None
    cc_key: int | tuple[int | None, int | None, int | None] | None
    override: bool
    ordinal: int


def rows_from_snapshot(
    snapshot: dict[ParameterKey, tuple[ParamMeta, ParamState, int, str | None]],
) -> list[ParameterRow]:
    """Snapshot から ParameterRow を生成し、op→ordinal→arg の順で並べる。"""

    rows: list[ParameterRow] = []
    for key, (meta, state, ordinal, _label) in snapshot.items():
        rows.append(
            ParameterRow(
                label=f"{ordinal}:{key.arg}",
                op=key.op,
                site_id=key.site_id,
                arg=key.arg,
                kind=meta.kind,
                ui_value=state.ui_value,
                ui_min=meta.ui_min,
                ui_max=meta.ui_max,
                choices=meta.choices,
                cc_key=state.cc_key,
                override=state.override,
                ordinal=ordinal,
            )
        )
    rows.sort(key=lambda r: (r.op, r.ordinal, r.arg))
    return rows


def _as_iterable3(value: Any) -> tuple[Iterable[Any], str | None]:
    try:
        seq = list(value)
    except Exception:
        return [], "not_iterable"
    if len(seq) != 3:
        return seq, "invalid_length"
    return seq, None


def normalize_input(value: Any, meta: ParamMeta) -> tuple[Any | None, str | None]:
    """kind に応じて UI 入力を正規化し、(正規化値, エラー種別) を返す。"""

    kind = meta.kind

    if kind == "bool":
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "on", "yes"}:
                return True, None
            if lowered in {"false", "0", "off", "no"}:
                return False, None
        return bool(value), None

    if kind == "int":
        try:
            return int(value), None
        except Exception:
            return None, "invalid_int"

    if kind == "float":
        try:
            return float(value), None
        except Exception:
            return None, "invalid_float"

    if kind == "str":
        try:
            return str(value), None
        except Exception:
            return None, "invalid_string"

    if kind == "font":
        if value is None:
            return "", None
        try:
            return str(value), None
        except Exception:
            return None, "invalid_string"

    if kind == "choice":
        # choice は str として扱い、choices 外の場合は先頭に丸める
        try:
            text = str(value)
        except Exception:
            return None, "invalid_choice"
        choices = list(meta.choices) if meta.choices is not None else []
        if choices and text not in choices:
            return choices[0], "choice_coerced"
        return text, None

    if kind == "vec3":
        seq, err = _as_iterable3(value)
        if err is not None:
            return None, err
        out_vec: list[float] = []
        try:
            for v in seq:
                out_vec.append(float(v))
        except Exception:
            return None, "invalid_vec"
        return tuple(out_vec), None

    if kind == "rgb":
        seq, err = _as_iterable3(value)
        if err is not None:
            return None, err
        out_rgb: list[int] = []
        try:
            for v in seq:
                iv = int(v)
                iv = max(0, min(255, iv))
                out_rgb.append(iv)
        except Exception:
            return None, "invalid_rgb"
        return tuple(out_rgb), None

    # 未知 kind はそのまま返す
    return value, None


def canonicalize_ui_value(value: Any, meta: ParamMeta) -> Any:
    """meta.kind に従って ui_value を不変（immutable）へ正規化して返す。"""

    kind = str(meta.kind)
    if kind not in {"bool", "int", "float", "str", "font", "choice", "vec3", "rgb"}:
        try:
            return str(value)
        except Exception:
            return ""

    normalized, err = normalize_input(value, meta)
    if normalized is not None:
        return normalized

    # 正規化できない場合は kind に応じた安全な既定値へ寄せる。
    if kind == "bool":
        return False
    if kind == "int":
        return 0
    if kind == "float":
        return 0.0
    if kind in {"str", "font"}:
        return ""
    if kind == "choice":
        choices = list(meta.choices) if meta.choices is not None else []
        return str(choices[0]) if choices else ""
    if kind == "vec3":
        return (0.0, 0.0, 0.0)
    if kind == "rgb":
        return (0, 0, 0)

    # unreachable
    return "" if err else value
