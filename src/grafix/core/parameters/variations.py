# どこで: `src/grafix/core/parameters/variations.py`。
# 何を: 名前付き parameter variation と、その編集・差分・復元操作を定義する。
# なぜ: 良い調整状態を失わず、再起動後も比較・再利用できるようにするため。

from __future__ import annotations

from collections.abc import Iterable, Sequence
from hashlib import sha256
import time
from dataclasses import dataclass, replace
from math import ceil, floor
from pathlib import Path
from random import Random
from typing import TYPE_CHECKING
from unicodedata import category

from grafix.core.value_validation import (
    exact_bool,
    exact_integer,
    exact_string,
    finite_real,
)

from .adjustment_snapshot import (
    ParameterAdjustment,
    ParameterAdjustmentSnapshot,
)
from .key import ParameterKey
from .meta import ParamMeta
from .view import canonicalize_ui_value

if TYPE_CHECKING:
    from .history import ParamStoreHistory
    from .store import ParamStore


_MAX_VARIATION_NAME_LENGTH = 80


@dataclass(frozen=True, slots=True)
class Variation:
    """名前付きで保存した parameter 調整状態。

    ``parameter_snapshot`` は GUI-owned adjustment の snapshot であり、復元時に
    現在の code-owned 構造へ merge される。parameter lock は variation ごとの
    値ではなく現在の探索を守る store-level UI state のため、この snapshot には含めない。
    """

    name: str
    created_at: float
    note: str
    seed: int | None
    t: float | None
    parameter_snapshot: ParameterAdjustmentSnapshot
    thumbnail_path: str | None

    def __post_init__(self) -> None:
        name = _validated_name(self.name)
        created_at = finite_real(self.created_at, name="created_at")
        note = exact_string(self.note, name="note")
        seed = (
            None
            if self.seed is None
            else exact_integer(self.seed, name="seed")
        )
        t = None if self.t is None else finite_real(self.t, name="t")
        if type(self.parameter_snapshot) is not ParameterAdjustmentSnapshot:
            raise TypeError(
                "parameter_snapshot must be a ParameterAdjustmentSnapshot"
            )
        thumbnail_path = (
            None
            if self.thumbnail_path is None
            else exact_string(self.thumbnail_path, name="thumbnail_path")
        )

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "note", note)
        object.__setattr__(self, "seed", seed)
        object.__setattr__(self, "t", t)
        object.__setattr__(self, "thumbnail_path", thumbnail_path)


@dataclass(frozen=True, slots=True)
class VariationDifference:
    """現在の store と variation 間の 1 parameter 分の差分。"""

    key: ParameterKey
    fields: tuple[str, ...]


def create_variation(
    store: ParamStore,
    name: str,
    *,
    note: str = "",
    seed: int | None = None,
    t: float | None = None,
    thumbnail_path: str | Path | None = None,
    created_at: float | None = None,
) -> Variation:
    """現在の調整状態を名前付き variation として保存する。

    同名 variation は暗黙に上書きしない。
    """

    validated_name = _validated_name(name)
    variations = store._variations_ref()
    if validated_name in variations:
        raise ValueError(f"variation already exists: {validated_name!r}")

    variation = Variation(
        name=validated_name,
        created_at=time.time() if created_at is None else created_at,
        note=note,
        seed=seed,
        t=t,
        parameter_snapshot=store.capture_adjustment_snapshot(),
        thumbnail_path=_thumbnail_path_text(thumbnail_path),
    )
    variations[variation.name] = variation
    store._touch()
    return variation


def duplicate_variation(
    store: ParamStore,
    name: str,
    new_name: str,
    *,
    created_at: float | None = None,
) -> Variation:
    """既存 variation の snapshot/metadata を独立した新名称で複製する。"""

    source_name = _validated_name(name)
    duplicate_name = _validated_name(new_name)
    variations = store._variations_ref()
    source = _require_variation(variations, source_name)
    if duplicate_name in variations:
        raise ValueError(f"variation already exists: {duplicate_name!r}")

    duplicate = replace(
        source,
        name=duplicate_name,
        created_at=time.time() if created_at is None else created_at,
        parameter_snapshot=source.parameter_snapshot,
    )
    variations[duplicate.name] = duplicate
    store._touch()
    return duplicate


def rename_variation(store: ParamStore, name: str, new_name: str) -> Variation:
    """variation の名前を変更し、内容と並び順は保つ。"""

    current_name = _validated_name(name)
    validated_new_name = _validated_name(new_name)
    variations = store._variations_ref()
    variation = _require_variation(variations, current_name)
    if validated_new_name == current_name:
        return variation
    if validated_new_name in variations:
        raise ValueError(f"variation already exists: {validated_new_name!r}")

    renamed = replace(variation, name=validated_new_name)
    items = [
        (validated_new_name, renamed) if key == current_name else (key, value)
        for key, value in variations.items()
    ]
    variations.clear()
    variations.update(items)
    store._touch()
    return renamed


def delete_variation(store: ParamStore, name: str) -> bool:
    """variation を削除する。存在しなければ False を返す。"""

    validated_name = _validated_name(name)
    variations = store._variations_ref()
    if validated_name not in variations:
        return False
    del variations[validated_name]
    store._touch()
    return True


def list_variations(store: ParamStore) -> tuple[Variation, ...]:
    """作成順の variation を読み取り専用 tuple で返す。"""

    return tuple(store._variations_ref().values())


def diff_variation(
    store: ParamStore,
    name: str,
) -> tuple[VariationDifference, ...]:
    """現在の parameter 状態と variation の差分を返す。"""

    variation = _require_variation(store._variations_ref(), _validated_name(name))
    return tuple(
        VariationDifference(key=key, fields=fields)
        for key, fields in variation.parameter_snapshot.difference_fields(
            store.capture_adjustment_snapshot()
        )
    )


def restore_variation(
    store: ParamStore,
    name: str,
    *,
    history: ParamStoreHistory | None = None,
) -> bool:
    """variation を現在の code-owned 構造へ merge 復元する。

    ``history`` を渡した場合は、1 回の Undo 操作として記録する。
    variation 作成後に発見された parameter は削除しない。
    """

    validated_name = _validated_name(name)
    variation = _require_variation(store._variations_ref(), validated_name)
    if history is None:
        return store.apply_adjustment_snapshot(variation.parameter_snapshot)
    if history._store is not store:
        raise ValueError("history must belong to the same ParamStore")

    changed = False
    with history.transaction(source=("variation", validated_name)):
        changed = store.apply_adjustment_snapshot(variation.parameter_snapshot)
    return changed


def locked_parameter_keys(store: ParamStore) -> tuple[ParameterKey, ...]:
    """現在 lock されている parameter key を安定順の tuple で返す。"""

    return tuple(
        sorted(
            store._locked_keys_ref(),
            key=lambda key: (key.op, key.site_id, key.arg),
        )
    )


def is_parameter_locked(store: ParamStore, key: ParameterKey) -> bool:
    """``key`` が exploration 操作から保護されていれば True。"""

    if not isinstance(key, ParameterKey):
        raise TypeError("key must be a ParameterKey")
    return key in store._locked_keys_ref()


def set_parameters_locked(
    store: ParamStore,
    keys: Iterable[ParameterKey],
    *,
    locked: bool,
) -> tuple[ParameterKey, ...]:
    """scope 内の parameter lock を一括変更し、実際に変わった key を返す。

    lock 追加は現在の state/meta の両方に存在する key だけへ適用する。
    unlock は、code reload 後の stale key も明示的に除去できる。
    複数 key を変更しても store revision は 1 回だけ進む。
    """

    locked = exact_bool(locked, name="locked")
    ordered_keys = _ordered_scope(keys)
    locked_keys = store._locked_keys_ref()
    available_keys = frozenset(store.capture_adjustment_snapshot().keys())
    changed: list[ParameterKey] = []
    for key in ordered_keys:
        if locked:
            if key not in available_keys or key in locked_keys:
                continue
            locked_keys.add(key)
            changed.append(key)
        elif key in locked_keys:
            locked_keys.discard(key)
            changed.append(key)
    if changed:
        store._touch()
    return tuple(changed)


def randomize_parameters(
    store: ParamStore,
    keys: Iterable[ParameterKey],
    *,
    seed: int,
    history: ParamStoreHistory | None = None,
) -> tuple[ParameterKey, ...]:
    """scope 内の numeric parameter を seed 付きで randomize する。

    ``float`` / ``int`` / ``vec3`` / ``rgb`` だけが対象で、lock 済み key と
    range を持たない key は変更しない。range は ``recommended_range`` を
    優先し、未指定なら ``ui_min`` / ``ui_max`` を使う。vec3/rgb の scalar
    range は 3 成分すべてへ適用する。

    乱数列は ``seed + ParameterKey`` から key ごとに導出するため、scope の順序や
    他 key の追加・lock に左右されない。同じ seed/key/range は常に同じ値になる。
    変更値は UI override として有効化し、MIDI assignment は維持する。
    ``history`` を渡した場合、全変更を 1 回の Undo 操作として記録する。
    """

    normalized_seed = exact_integer(seed, name="seed")
    _validate_history(store, history)
    ordered_keys = _ordered_scope(keys)

    def apply() -> tuple[ParameterKey, ...]:
        current = store.capture_adjustment_snapshot()
        current_by_key = dict(current.items())
        adjustments: list[tuple[ParameterKey, ParameterAdjustment]] = []
        locked = store._locked_keys_ref()
        for key in ordered_keys:
            if key in locked:
                continue
            adjustment = current_by_key.get(key)
            if adjustment is None:
                continue
            randomized = _randomized_value(
                adjustment.meta,
                seed=normalized_seed,
                key=key,
            )
            if randomized is _UNSUPPORTED:
                continue
            if adjustment.state.ui_value == randomized and adjustment.state.override:
                continue
            adjustments.append(
                (
                    key,
                    ParameterAdjustment(
                        state=replace(
                            adjustment.state,
                            ui_value=randomized,
                            override=True,
                        ),
                        meta=adjustment.meta,
                    ),
                )
            )
        return store._apply_adjustment_values(adjustments)

    if history is None:
        return apply()
    history.break_coalescing()
    with history.transaction(source=("variation-randomize", normalized_seed)):
        return apply()


def morph_variations(
    store: ParamStore,
    a_name: str,
    b_name: str,
    amount: float,
    *,
    keys: Iterable[ParameterKey],
    history: ParamStoreHistory | None = None,
) -> tuple[ParameterKey, ...]:
    """2 variation の共通 parameter を ``amount`` で現在 store へ適用する。

    ``amount`` は 0..1。A/B 両 snapshot と現在 store で key/kind が共通する
    scope 内 parameter だけを対象とし、lock 済み key は変更しない。

    Policy
    ------
    - ``float`` / ``vec3``: 線形補間。
    - ``int`` / ``rgb``: 線形補間後に .5 を 0 から遠い整数へ丸める。
    - ``bool`` / ``choice`` / ``str`` / ``font``: ``amount < 0.5`` は A、
      ``amount >= 0.5`` は B。
    - UI ownership (``override``) と MIDI assignment (``cc_key``) も同じ
      0.5 境界の離散 policy で A/B を選ぶ。

    UI range や code-owned metadata は変更しない。``history`` を渡した場合、
    scope 全体の適用を 1 回の Undo 操作として記録する。
    """

    normalized_amount = finite_real(amount, name="amount")
    if not 0.0 <= normalized_amount <= 1.0:
        raise ValueError("amount must be a finite number in [0, 1]")
    _validate_history(store, history)
    variation_a = _require_variation(store._variations_ref(), _validated_name(a_name))
    variation_b = _require_variation(store._variations_ref(), _validated_name(b_name))
    ordered_keys = _ordered_scope(keys)
    adjustments_a = dict(variation_a.parameter_snapshot.items())
    adjustments_b = dict(variation_b.parameter_snapshot.items())

    def apply() -> tuple[ParameterKey, ...]:
        current = store.capture_adjustment_snapshot()
        current_by_key = dict(current.items())
        adjustments: list[tuple[ParameterKey, ParameterAdjustment]] = []
        locked = store._locked_keys_ref()
        use_b = normalized_amount >= 0.5
        for key in ordered_keys:
            if key in locked:
                continue
            current_adjustment = current_by_key.get(key)
            adjustment_a = adjustments_a.get(key)
            adjustment_b = adjustments_b.get(key)
            if (
                current_adjustment is None
                or adjustment_a is None
                or adjustment_b is None
            ):
                continue
            if not (
                current_adjustment.meta.kind
                == adjustment_a.meta.kind
                == adjustment_b.meta.kind
            ):
                continue
            value = _morphed_value(
                current_adjustment.meta,
                adjustment_a.state.ui_value,
                adjustment_b.state.ui_value,
                normalized_amount,
            )
            if value is _UNSUPPORTED:
                continue
            selected_state = adjustment_b.state if use_b else adjustment_a.state
            if (
                current_adjustment.state.ui_value == value
                and current_adjustment.state.override == selected_state.override
                and current_adjustment.state.cc_key == selected_state.cc_key
            ):
                continue
            adjustments.append(
                (
                    key,
                    ParameterAdjustment(
                        state=replace(
                            current_adjustment.state,
                            ui_value=value,
                            override=selected_state.override,
                            cc_key=selected_state.cc_key,
                        ),
                        meta=current_adjustment.meta,
                    ),
                )
            )
        return store._apply_adjustment_values(adjustments)

    if history is None:
        return apply()
    history.break_coalescing()
    with history.transaction(
        source=("variation-morph", variation_a.name, variation_b.name)
    ):
        return apply()


_UNSUPPORTED = object()
_NUMERIC_KINDS = frozenset({"float", "int", "vec3", "rgb"})
_DISCRETE_KINDS = frozenset({"bool", "choice", "str", "font"})


def _ordered_scope(keys: Iterable[ParameterKey]) -> tuple[ParameterKey, ...]:
    if isinstance(keys, (str, bytes)) or not isinstance(keys, Iterable):
        raise TypeError("keys must be an iterable of ParameterKey")
    unique: set[ParameterKey] = set()
    for key in keys:
        if not isinstance(key, ParameterKey):
            raise TypeError("keys must contain only ParameterKey")
        unique.add(key)
    return tuple(sorted(unique, key=lambda key: (key.op, key.site_id, key.arg)))


def _validate_history(
    store: ParamStore,
    history: ParamStoreHistory | None,
) -> None:
    if history is not None and history._store is not store:
        raise ValueError("history must belong to the same ParamStore")


def _key_random(seed: int, key: ParameterKey) -> Random:
    payload = f"{seed}\0{key.op}\0{key.site_id}\0{key.arg}".encode("utf-8")
    digest = sha256(payload).digest()
    return Random(int.from_bytes(digest[:16], "big"))


def _numeric_components(value: object, count: int) -> tuple[float, ...] | None:
    try:
        normalized = finite_real(value, name="numeric component")
    except (TypeError, ValueError):
        normalized = None
    if normalized is not None:
        return (normalized,) * count
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return None
    if len(value) != count:
        return None
    out: list[float] = []
    for component in value:
        try:
            normalized = finite_real(component, name="numeric component")
        except (TypeError, ValueError):
            return None
        out.append(normalized)
    return tuple(out)


def _ranges_for_meta(
    meta: ParamMeta,
    *,
    count: int,
) -> tuple[tuple[float, float], ...] | None:
    recommended = meta.recommended_range
    lowers: tuple[float, ...]
    uppers: tuple[float, ...]
    if recommended is not None:
        lowers = (recommended[0],) * count
        uppers = (recommended[1],) * count
    else:
        ui_lowers = _numeric_components(meta.ui_min, count)
        ui_uppers = _numeric_components(meta.ui_max, count)
        if ui_lowers is None or ui_uppers is None:
            return None
        lowers = ui_lowers
        uppers = ui_uppers
    ranges = tuple(zip(lowers, uppers, strict=True))
    if any(lower > upper for lower, upper in ranges):
        return None
    return ranges


def _randomized_value(
    meta: ParamMeta,
    *,
    seed: int,
    key: ParameterKey,
) -> object:
    kind = meta.kind
    if kind not in _NUMERIC_KINDS:
        return _UNSUPPORTED
    count = 3 if kind in {"vec3", "rgb"} else 1
    ranges = _ranges_for_meta(meta, count=count)
    if ranges is None:
        return _UNSUPPORTED
    rng = _key_random(seed, key)
    if kind == "float":
        lower, upper = ranges[0]
        return rng.uniform(lower, upper)
    if kind == "int":
        lower, upper = ranges[0]
        integer_lower = ceil(lower)
        integer_upper = floor(upper)
        if integer_lower > integer_upper:
            return _UNSUPPORTED
        return rng.randint(integer_lower, integer_upper)
    if kind == "vec3":
        return tuple(rng.uniform(lower, upper) for lower, upper in ranges)

    rgb_ranges = tuple(
        (max(0, ceil(lower)), min(255, floor(upper)))
        for lower, upper in ranges
    )
    if any(lower > upper for lower, upper in rgb_ranges):
        return _UNSUPPORTED
    return tuple(rng.randint(lower, upper) for lower, upper in rgb_ranges)


def _round_half_away_from_zero(value: float) -> int:
    return floor(value + 0.5) if value >= 0.0 else ceil(value - 0.5)


def _vector3(value: object) -> tuple[float, float, float] | None:
    components = _numeric_components(value, 3)
    if components is None:
        return None
    return components[0], components[1], components[2]


def _morphed_value(
    meta: ParamMeta,
    value_a: object,
    value_b: object,
    amount: float,
) -> object:
    kind = meta.kind
    if kind in _DISCRETE_KINDS:
        selected = value_b if amount >= 0.5 else value_a
        return canonicalize_ui_value(selected, meta)
    if kind == "float":
        components_a = _numeric_components(value_a, 1)
        components_b = _numeric_components(value_b, 1)
        if components_a is None or components_b is None:
            return _UNSUPPORTED
        float_a = components_a[0]
        float_b = components_b[0]
        return float_a + (float_b - float_a) * amount
    if kind == "int":
        components_a = _numeric_components(value_a, 1)
        components_b = _numeric_components(value_b, 1)
        if components_a is None or components_b is None:
            return _UNSUPPORTED
        int_a = components_a[0]
        int_b = components_b[0]
        return _round_half_away_from_zero(int_a + (int_b - int_a) * amount)
    if kind in {"vec3", "rgb"}:
        vector_a = _vector3(value_a)
        vector_b = _vector3(value_b)
        if vector_a is None or vector_b is None:
            return _UNSUPPORTED
        interpolated = tuple(
            component_a + (component_b - component_a) * amount
            for component_a, component_b in zip(vector_a, vector_b, strict=True)
        )
        if kind == "vec3":
            return interpolated
        return tuple(
            max(0, min(255, _round_half_away_from_zero(component)))
            for component in interpolated
        )
    return _UNSUPPORTED


def _validated_name(name: object) -> str:
    """variation 名を変更せず、canonical な exact str として検証する。"""

    name = exact_string(name, name="variation name")
    if any(category(character) in {"Cc", "Cs", "Zl", "Zp"} for character in name):
        raise ValueError(
            "variation name must not contain line breaks or control characters"
        )
    if not name.strip():
        raise ValueError("variation name must not be empty")
    if len(name) > _MAX_VARIATION_NAME_LENGTH:
        raise ValueError(
            "variation name must be at most "
            f"{_MAX_VARIATION_NAME_LENGTH} characters"
        )
    return name


def _thumbnail_path_text(value: str | Path | None) -> str | None:
    """thumbnail path の宣言型だけを受け、暗黙 Path/string 化を行わない。"""

    if value is None:
        return None
    if type(value) is str:
        return value
    if isinstance(value, Path):
        return str(value)
    raise TypeError("thumbnail_path must be a str, Path, or None")


def _require_variation(
    variations: dict[str, Variation],
    name: str,
) -> Variation:
    try:
        return variations[name]
    except KeyError:
        raise KeyError(f"unknown variation: {name!r}") from None


__all__ = [
    "Variation",
    "VariationDifference",
    "create_variation",
    "delete_variation",
    "diff_variation",
    "duplicate_variation",
    "is_parameter_locked",
    "list_variations",
    "locked_parameter_keys",
    "morph_variations",
    "randomize_parameters",
    "rename_variation",
    "restore_variation",
    "set_parameters_locked",
]
