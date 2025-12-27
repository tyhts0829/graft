# どこで: `src/grafix/core/parameters/merge_ops.py`。
# 何を: フレーム内で観測したパラメータレコードを ParamStore にマージする。
# なぜ: 書き込み経路を ops に固定し、不変条件の知識を 1 箇所へ寄せるため。

from __future__ import annotations

from collections.abc import Mapping

from .frame_params import FrameParamRecord
from .key import ParameterKey
from .reconcile_ops import reconcile_loaded_groups_for_runtime
from .store import ParamStore
from .view import canonicalize_ui_value


def merge_frame_params(store: ParamStore, records: list[FrameParamRecord]) -> None:
    """フレーム内で観測したレコードをストアに保存し、関連情報を更新する。"""

    # `runtime` は永続化しない「実行時キャッシュ」。
    # - GUI の表示順（コード順の安定化）
    # - reconcile の材料（loaded/observed）
    # - 直近フレームの effective 値など
    # といった、フレーム境界で更新される情報を集約する。
    runtime = store._runtime_ref()
    # `ordinals` は GUI 用の group 連番（同じ (op, site_id) を安定に並べるための ID）。
    # フレームで観測した group はここで必ず割り当てる。
    ordinals = store._ordinals_ref()
    # `effects` は EffectChain のチェーン境界/ステップ順序の索引。
    # Parameter GUI 側で「チェーン単位のヘッダ」や表示順を作るために使う。
    effects = store._effects_ref()

    # このフレームで観測した「explicit/implicit」フラグを key ごとに集める。
    # ※ explicit は「ユーザーが kwargs を明示指定したか」（API 層で判定）を表す。
    #    - explicit=True: コード側の指定を尊重するのが自然なので、初期 override は False に寄せる。
    #    - explicit=False: GUI で触れる前提の値（暗黙値）なので、初期 override は True に寄せる。
    #
    # ただし、explicit が後から変化し得る（例: 引数を外した/追加した）ため、
    # フレーム境界で「既定値のままなら追従する」ポリシーを別関数で適用する。
    explicit_by_key_this_frame: dict[ParameterKey, bool] = {}

    for rec in records:
        # --- 1) このフレームで観測した group を runtime に記録 ---
        #
        # group=(op, site_id) は GUI のヘッダ単位（primitive/effect のまとまり）でもある。
        # observed_groups は「今フレームに実際に出現した group」。
        group = (str(rec.key.op), str(rec.key.site_id))
        runtime.observed_groups.add(group)
        if group not in runtime.display_order_by_group:
            # 初出 group は「初めて観測された順」を display_order に固定する。
            # これによりフレーム間で行が並び替わりにくくなり、GUI の視認性が安定する。
            runtime.display_order_by_group[group] = int(runtime.next_display_order)
            runtime.next_display_order += 1

        # --- 2) このフレームの explicit を集計 ---
        explicit_by_key_this_frame[rec.key] = bool(rec.explicit)

        # --- 3) 直近フレームの effective 値をキャッシュ ---
        #
        # `effective` は resolver が base/GUI/CC を統合して最終的に採用した値。
        # UI 側で「CC 割当解除時に、その時点の実効値を ui_value に焼き込む」用途などに使う。
        #
        # 注意: record には effective=None のケースがあり得る（テストなど）。
        #       その場合はキャッシュを更新しない（直近値を保持）。
        if rec.effective is not None:
            runtime.last_effective_by_key[rec.key] = rec.effective

        # --- 4) group ordinal を確保 ---
        ordinals.get_or_assign(rec.key.op, rec.key.site_id)

        # --- 5) ParamState を確保（初出のみ初期化）---
        #
        # `base_value` は「コードが与えた base」を canonicalize したものを使う。
        # ここで canonicalize しておくと、JSON roundtrip や型正規化の基準が一箇所に揃う。
        #
        # `initial_override` は初出時のみ効く（既に state があれば尊重する）。
        store._ensure_state(
            rec.key,
            base_value=canonicalize_ui_value(rec.base, rec.meta),
            initial_override=(not bool(rec.explicit)),
        )

        # meta は初出時に確定し、以後は保持する（GUI 側で編集できるようにする）。
        # ただし kind が変わった場合は、古い ui_value をそのまま使うと破綻しやすいので、
        # 「kind 不一致」は別のパラメータとして扱い、新しい meta を採用する。
        existing_meta = store._meta.get(rec.key)
        if existing_meta is None or existing_meta.kind != rec.meta.kind:
            store._meta[rec.key] = rec.meta

        # --- 6) effect chain 索引（GUI の表示順/ヘッダ単位）を更新 ---
        #
        # chain_id/step_index は「この key がどのチェーンの何番目か」を表す。
        # effect 以外の op では None のままなので、その場合は何もしない。
        if rec.chain_id is not None and rec.step_index is not None:
            effects.record_step(
                op=str(rec.key.op),
                site_id=str(rec.key.site_id),
                chain_id=str(rec.chain_id),
                step_index=int(rec.step_index),
            )

    # loaded/observed の差分を見て「site_id が揺れた group」を再リンクする。
    # この呼び出しは「このフレームで observed_groups を積んだ後」である必要がある。
    reconcile_loaded_groups_for_runtime(store)
    # explicit/implicit の変化に応じて、override を “必要なときだけ” 追従させる。
    # ここでの追従はユーザー操作を上書きしないよう、既定値判定つきで行う。
    _apply_explicit_override_follow_policy(store, explicit_by_key_this_frame)


def _apply_explicit_override_follow_policy(
    store: ParamStore, explicit_by_key_this_frame: Mapping[ParameterKey, bool]
) -> None:
    """explicit/implicit の変化に追従して override を条件付きで更新する。"""

    for key, new_explicit in explicit_by_key_this_frame.items():
        # `store._explicit_by_key` は「前回までの explicit」を覚えておく永続データ。
        # ここで差分が出たときだけ follow を検討する。
        prev_explicit = store._explicit_by_key.get(key)
        new_explicit = bool(new_explicit)

        if prev_explicit is None:
            # 旧 JSON（explicit 情報なし）もあるので、unknown の場合は触らず記録だけ行う。
            store._explicit_by_key[key] = new_explicit
            continue

        prev_explicit = bool(prev_explicit)
        if prev_explicit == new_explicit:
            continue

        # state が無ければ override を更新できないので、explicit の記録だけ更新して終える。
        state = store._states.get(key)
        if state is None:
            store._explicit_by_key[key] = new_explicit
            continue

        # explicit の意味は「コード側が値を明示指定したか」。
        # override の既定値は、それに反転して決める:
        # - explicit=True  : 初期 override=False（コードを採用するのが自然）
        # - explicit=False : 初期 override=True（GUI を採用するのが自然）
        default_override_prev = not prev_explicit
        default_override_new = not new_explicit

        # 追従ルール:
        # - 現在の override が「前回の既定値のまま」のときだけ、新しい既定値へ追従させる。
        # - 既にユーザーが override を切り替えた（=既定値から外れた）場合は尊重し、触らない。
        #
        # 例:
        # - もともと implicit（override=True が既定）で、ユーザーが触っていない状態のまま
        #   explicit に変わったら、override は False に追従して「コード優先」に戻る。
        # - ユーザーが明示的に override を切り替えていたら、explicit が変わっても維持する。
        if bool(state.override) == bool(default_override_prev):
            state.override = bool(default_override_new)

        # 次フレーム以降の差分判定のため、explicit の記録は必ず更新する。
        store._explicit_by_key[key] = new_explicit


__all__ = ["merge_frame_params"]
