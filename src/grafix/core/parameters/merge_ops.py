# どこで: `src/grafix/core/parameters/merge_ops.py`。
# 何を: フレーム内で観測したパラメータレコードを ParamStore にマージする。
# なぜ: 書き込み経路を ops に固定し、不変条件の知識を 1 箇所へ寄せるため。

from __future__ import annotations

from collections.abc import Mapping

from .frame_params import FrameParamRecord
from .key import ParameterKey
from .reconcile_ops import reconcile_loaded_groups_for_runtime
from .store import ParamStore


def merge_frame_params(store: ParamStore, records: list[FrameParamRecord]) -> None:
    """フレーム内で観測したレコードをストアに保存し、関連情報を更新する。"""

    runtime = store._runtime_ref()
    ordinals = store._ordinals_ref()
    effects = store._effects_ref()

    explicit_by_key_this_frame: dict[ParameterKey, bool] = {}

    for rec in records:
        runtime.observed_groups.add((str(rec.key.op), str(rec.key.site_id)))
        explicit_by_key_this_frame[rec.key] = bool(rec.explicit)

        ordinals.get_or_assign(rec.key.op, rec.key.site_id)
        store._ensure_state(
            rec.key,
            base_value=rec.base,
            initial_override=(not bool(rec.explicit)),
        )

        # meta は初出時に確定し、以後は保持する（GUI 側で編集できるようにする）
        existing_meta = store._meta.get(rec.key)
        if existing_meta is None or existing_meta.kind != rec.meta.kind:
            store._meta[rec.key] = rec.meta

        if rec.chain_id is not None and rec.step_index is not None:
            effects.record_step(
                op=str(rec.key.op),
                site_id=str(rec.key.site_id),
                chain_id=str(rec.chain_id),
                step_index=int(rec.step_index),
            )

    reconcile_loaded_groups_for_runtime(store)
    _apply_explicit_override_follow_policy(store, explicit_by_key_this_frame)


def _apply_explicit_override_follow_policy(
    store: ParamStore, explicit_by_key_this_frame: Mapping[ParameterKey, bool]
) -> None:
    """explicit/implicit の変化に追従して override を条件付きで更新する。"""

    for key, new_explicit in explicit_by_key_this_frame.items():
        prev_explicit = store._explicit_by_key.get(key)
        new_explicit = bool(new_explicit)

        if prev_explicit is None:
            # 旧 JSON（explicit 情報なし）もあるので、unknown の場合は触らず記録だけ行う。
            store._explicit_by_key[key] = new_explicit
            continue

        prev_explicit = bool(prev_explicit)
        if prev_explicit == new_explicit:
            continue

        state = store._states.get(key)
        if state is None:
            store._explicit_by_key[key] = new_explicit
            continue

        default_override_prev = not prev_explicit
        default_override_new = not new_explicit

        # 既定値のままなら追従して切り替える。既にユーザーが切り替え済みなら触らない。
        if bool(state.override) == bool(default_override_prev):
            state.override = bool(default_override_new)

        store._explicit_by_key[key] = new_explicit


__all__ = ["merge_frame_params"]

