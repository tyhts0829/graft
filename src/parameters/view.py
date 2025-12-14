# どこで: `src/parameters/view.py`。
# 何を: ParamStore スナップショットから UI 行モデルを生成し、UI 入力を正規化して状態へ適用する純粋関数群を提供する。
# なぜ: DPG 依存部と切り離し、型変換・検証を単体テスト可能に保つため。

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .key import ParameterKey
from .meta import ParamMeta
from .state import ParamState
from .store import ParamStore


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
    """UI から渡された入力を正規化し、対応する ParamState に反映する。

    Parameters
    ----------
    store : ParamStore
        対象ストア。
    key : ParameterKey
        更新対象キー。
    ui_input_value : Any
        UI からの入力値。
    meta : ParamMeta
        kind/choices などの正規化に使うメタ情報。
    override : bool | None
        指定時は state.override を更新する。None の場合は変更しない。
    cc_key : int | None | object
        指定時は state.cc_key を更新する。
        - int: その CC 番号へ設定
        - None: クリア
        - 省略: 変更しない

    Returns
    -------
    (success, error)
        success: 正常に反映した場合 True
        error: 失敗時のエラー種別文字列
    """

    normalized, err = normalize_input(ui_input_value, meta)
    if err and normalized is None:
        return False, err

    state = store.ensure_state(
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
