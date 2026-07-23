"""Parameter GUI などから渡された immutable edit command を適用する。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .key import ParameterKey
from .meta import ParamMeta
from .state import ParamState
from .store import ParamStore
from .validation import CcKey, validate_cc_key, validate_parameter_value


@dataclass(frozen=True, slots=True)
class ParameterEdit:
    """一つの parameter の最終 UI-owned state。"""

    key: ParameterKey
    meta: ParamMeta
    ui_value: Any
    override: bool
    cc_key: CcKey
    favorite: bool

    def __post_init__(self) -> None:
        if not isinstance(self.key, ParameterKey):
            raise TypeError("key must be a ParameterKey")
        if not isinstance(self.meta, ParamMeta):
            raise TypeError("meta must be a ParamMeta")
        if type(self.override) is not bool:
            raise TypeError("override must be an exact bool")
        if type(self.favorite) is not bool:
            raise TypeError("favorite must be an exact bool")
        object.__setattr__(
            self,
            "ui_value",
            validate_parameter_value(
                self.ui_value,
                kind=self.meta.kind,
                choices=self.meta.choices,
            ),
        )
        object.__setattr__(
            self,
            "cc_key",
            validate_cc_key(
                self.cc_key,
                kind=self.meta.kind,
                op=self.key.op,
            ),
        )


def apply_parameter_edits(
    store: ParamStore,
    edits: tuple[ParameterEdit, ...],
) -> tuple[ParameterKey, ...]:
    """複数 edit を一つの core command として適用する。

    実差分がない edit は無視する。複数 key を変更しても store revision は
    一度だけ進み、history observer は各 key の変更前値を取得できる。
    """

    if not isinstance(store, ParamStore):
        raise TypeError("store must be a ParamStore")
    if not isinstance(edits, tuple) or not all(
        isinstance(edit, ParameterEdit) for edit in edits
    ):
        raise TypeError("edits must be a tuple of ParameterEdit values")
    keys = tuple(edit.key for edit in edits)
    if len(set(keys)) != len(keys):
        raise ValueError("edits must contain at most one command per key")

    base_revision = store.revision
    read = store._read()
    states = read.states()
    meta_by_key = read.all_meta()
    explicit_by_key = read.all_explicit()
    favorites = set(read.favorite_keys())
    favorites_before = frozenset(favorites)
    changed: list[ParameterEdit] = []
    history_keys: list[ParameterKey] = []
    value_keys: list[ParameterKey] = []
    structure_changed = False
    for edit in edits:
        state = states.get(edit.key)
        meta_changed = meta_by_key.get(edit.key) != edit.meta
        state_changed = (
            state is None
            or state.ui_value != edit.ui_value
            or state.override != edit.override
            or state.cc_key != edit.cc_key
        )
        favorite_changed = (edit.key in favorites_before) != edit.favorite
        if not meta_changed and not state_changed and not favorite_changed:
            continue

        changed.append(edit)
        if meta_changed:
            meta_by_key[edit.key] = edit.meta
            history_keys.append(edit.key)
            structure_changed = True
        if state_changed:
            if state is None:
                state = ParamState(ui_value=edit.ui_value)
                explicit_by_key[edit.key] = False
                structure_changed = True
            state.ui_value = edit.ui_value
            state.override = edit.override
            state.cc_key = edit.cc_key
            states[edit.key] = state
            history_keys.append(edit.key)
            value_keys.append(edit.key)
        if favorite_changed:
            if edit.favorite:
                favorites.add(edit.key)
            else:
                favorites.discard(edit.key)
    if not changed:
        return ()

    mutation = store._mutation()
    mutation.prepare_history(
        expected_revision=base_revision,
        keys=tuple(dict.fromkeys(history_keys)),
    )
    mutation.commit_parameter_edits(
        expected_revision=base_revision,
        states=states,
        meta=meta_by_key,
        explicit_by_key=explicit_by_key,
        favorite_keys=favorites,
        structure=structure_changed,
        value_keys=tuple(value_keys),
        favorites_changed=favorites != favorites_before,
    )
    return tuple(edit.key for edit in changed)


__all__ = ["ParameterEdit", "apply_parameter_edits"]
