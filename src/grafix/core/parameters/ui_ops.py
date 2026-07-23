# どこで: `src/grafix/core/parameters/ui_ops.py`。
# 何を: UI 入力（文字列/数値/タプル等）を ParamState へ反映する更新手続きを提供する。
# なぜ: ParamState の参照リークを避け、更新経路を ops に固定するため。

from __future__ import annotations

from typing import Any

from .key import ParameterKey
from .meta import ParamMeta
from .state import ParamState
from .store import ParamStore
from .validation import validate_cc_key, validate_parameter_value


class _KeepCcKey:
    pass


_KEEP = _KeepCcKey()


def update_state_from_ui(
    store: ParamStore,
    key: ParameterKey,
    ui_input_value: Any,
    *,
    meta: ParamMeta,
    override: bool | None = None,
    cc_key: int | tuple[int | None, int | None, int | None] | None | _KeepCcKey = _KEEP,
) -> tuple[bool, str | None]:
    """UI から渡された入力を正規化し、対応する ParamState に反映する。"""

    try:
        canonical = validate_parameter_value(
            ui_input_value,
            kind=meta.kind,
            choices=meta.choices,
        )
        if override is not None and type(override) is not bool:
            raise TypeError("override must be an exact bool or None")
        canonical_cc = (
            cc_key
            if isinstance(cc_key, _KeepCcKey)
            else validate_cc_key(cc_key, kind=meta.kind, op=key.op)
        )
    except (TypeError, ValueError) as exc:
        return False, str(exc)

    base_revision = store.revision
    read = store._read()
    current = read.state(key)
    state = ParamState(ui_value=canonical) if current is None else current
    before = (state.ui_value, state.override, state.cc_key)
    state.ui_value = canonical
    if override is not None:
        state.override = override

    if not isinstance(canonical_cc, _KeepCcKey):
        state.cc_key = canonical_cc

    structure_changed = current is None
    value_changed = (state.ui_value, state.override, state.cc_key) != before
    if structure_changed or value_changed:
        mutation = store._mutation()
        mutation.prepare_history(
            expected_revision=base_revision,
            keys=(key,),
        )
        if not structure_changed:
            mutation.commit_existing_parameter_state(
                expected_revision=base_revision,
                key=key,
                state=state,
                value_changed=value_changed,
            )
            return True, None

        states = read.states()
        meta_by_key = read.all_meta()
        explicit_by_key = read.all_explicit()
        states[key] = state
        explicit_by_key[key] = False
        mutation.commit_parameter_state(
            expected_revision=base_revision,
            states=states,
            meta=meta_by_key,
            explicit_by_key=explicit_by_key,
            structure=structure_changed,
            value_keys=(key,) if value_changed else (),
        )

    return True, None


__all__ = ["update_state_from_ui"]
