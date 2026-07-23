# どこで: `src/grafix/core/parameters/favorites.py`。
# 何を: parameter の favorite/pin 状態を読み書きする。
# なぜ: GUI 固有の永続状態を描画コードから分離し、codec/reconcile/prune と共有するため。

from __future__ import annotations

from collections.abc import Iterable

from .key import ParameterKey
from .store import ParamStore


def favorite_parameter_keys(store: ParamStore) -> tuple[ParameterKey, ...]:
    """favorite に登録されている key を安定順の tuple で返す。"""

    return store._read().favorite_keys_tuple()


def favorite_parameter_key_set(store: ParamStore) -> frozenset[ParameterKey]:
    """favorite key の revision 内で不変な immutable view を返す。"""

    return store._read().favorite_keys()


def is_parameter_favorite(store: ParamStore, key: ParameterKey) -> bool:
    """``key`` が favorite に登録されていれば True を返す。"""

    if not isinstance(key, ParameterKey):
        raise TypeError("key must be a ParameterKey")
    return key in store._read().favorite_keys()


def set_parameters_favorite(
    store: ParamStore,
    keys: Iterable[ParameterKey],
    *,
    favorite: bool,
) -> tuple[ParameterKey, ...]:
    """複数 parameter の favorite 状態を変更し、実際に変わった key を返す。

    favorite への追加は、現在の state/meta の両方に存在する key だけを対象とする。
    解除時は code reload 後の stale key も明示的に取り除ける。
    """

    if not isinstance(favorite, bool):
        raise TypeError("favorite must be a bool")

    raw_keys = tuple(keys)
    if not all(isinstance(key, ParameterKey) for key in raw_keys):
        raise TypeError("keys must contain only ParameterKey values")
    ordered_keys = tuple(
        sorted(
            set(raw_keys),
            key=lambda key: (key.op, key.site_id, key.arg),
        )
    )

    base_revision = store.revision
    read = store._read()
    favorites = set(read.favorite_keys())
    changed: list[ParameterKey] = []
    for key in ordered_keys:
        if favorite:
            if read.state(key) is None or read.meta(key) is None or key in favorites:
                continue
            favorites.add(key)
            changed.append(key)
        elif key in favorites:
            favorites.discard(key)
            changed.append(key)

    if changed:
        store._mutation().commit_favorites(
            expected_revision=base_revision,
            favorite_keys=favorites,
        )
    return tuple(changed)


__all__ = [
    "favorite_parameter_keys",
    "favorite_parameter_key_set",
    "is_parameter_favorite",
    "set_parameters_favorite",
]
