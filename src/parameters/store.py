# どこで: `src/parameters/store.py`。
# 何を: ParamStore を実装し、ParamState と ParamMeta の永続管理および ordinal 割り当てを行う。
# なぜ: GUI 用の既定状態とメタ情報を保持し、フレーム単位のバッファを統合するため。

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from .frame_params import FrameParamRecord
from .key import ParameterKey
from .meta import ParamMeta
from .reconcile import build_group_fingerprints, match_groups
from .state import ParamState

MAX_LABEL_LENGTH = 64


class ParamStore:
    """ParameterKey -> ParamState を保持する永続ストア。"""

    def __init__(self) -> None:
        self._states: dict[ParameterKey, ParamState] = {}
        self._meta: dict[ParameterKey, ParamMeta] = {}
        self._labels: dict[tuple[str, str], str] = {}
        # op ごとの site_id -> ordinal
        self._ordinals: dict[str, dict[str, int]] = {}
        # effect ステップ (op, site_id) -> (chain_id, step_index)
        self._effect_steps: dict[tuple[str, str], tuple[str, int]] = {}
        # chain_id -> ordinal（GUI の effect#N 用）
        self._chain_ordinals: dict[str, int] = {}
        # JSON ロード直後の (op, site_id) 集合（再リンク判定用）。
        self._loaded_groups: set[tuple[str, str]] = set()
        # 実行中に観測した (op, site_id) 集合（再リンク判定用）。
        self._observed_groups: set[tuple[str, str]] = set()
        # 再リンク適用済みの対応（同じペアを何度も上書きしないため）。
        self._reconcile_applied: set[tuple[tuple[str, str], tuple[str, str]]] = set()
        # ParameterKey ごとの「前回観測した explicit フラグ」。
        # - True: ユーザーが kwargs で明示指定した
        # - False: defaults 由来（省略）
        self._explicit_by_key: dict[ParameterKey, bool] = {}

    def loaded_groups(self) -> set[tuple[str, str]]:
        """ロード済み (op, site_id) グループ集合のコピーを返す。"""

        return set(self._loaded_groups)

    def observed_groups(self) -> set[tuple[str, str]]:
        """観測済み (op, site_id) グループ集合のコピーを返す。"""

        return set(self._observed_groups)

    def get_state(self, key: ParameterKey) -> ParamState | None:
        """登録済みの ParamState を返す。未登録なら None。"""
        return self._states.get(key)

    def get_meta(self, key: ParameterKey) -> ParamMeta | None:
        """登録済みの ParamMeta を返す。未登録なら None。"""
        return self._meta.get(key)

    def set_meta(self, key: ParameterKey, meta: ParamMeta) -> None:
        """ParamMeta を上書き保存する。"""
        self._meta[key] = meta

    def ensure_state(
        self,
        key: ParameterKey,
        *,
        base_value: Any,
        initial_override: bool | None = None,
    ) -> ParamState:
        """ParamState を確保し、無ければ base_value で初期化して返す。"""
        state = self._states.get(key)
        if state is None:
            state = ParamState(ui_value=base_value)
            if initial_override is not None:
                state.override = bool(initial_override)
            self._states[key] = state
            self._assign_ordinal(key.op, key.site_id)
        return state

    def _assign_ordinal(self, op: str, site_id: str) -> int:
        """op 単位で初出順に ordinal を付与する内部関数。"""
        mapping = self._ordinals.setdefault(op, {})
        if site_id in mapping:
            return mapping[site_id]
        ordinal = len(mapping) + 1
        mapping[site_id] = ordinal
        return ordinal

    def _assign_chain_ordinal(self, chain_id: str) -> int:
        if chain_id in self._chain_ordinals:
            return self._chain_ordinals[chain_id]
        ordinal = len(self._chain_ordinals) + 1
        self._chain_ordinals[chain_id] = ordinal
        return ordinal

    def get_chain_ordinal(self, chain_id: str) -> int:
        """chain_id の ordinal を返し、未登録なら採番して返す。"""

        return self._chain_ordinals.get(chain_id, self._assign_chain_ordinal(chain_id))

    def get_ordinal(self, op: str, site_id: str) -> int:
        """既存 ordinal を返し、未登録なら採番して返す。"""
        mapping = self._ordinals.get(op, {})
        return mapping.get(site_id, self._assign_ordinal(op, site_id))

    def snapshot(
        self,
    ) -> dict[ParameterKey, tuple[ParamMeta, ParamState, int, str | None]]:
        """(key -> (meta, state, ordinal, label)) のスナップショットを返す。"""

        result: dict[ParameterKey, tuple[ParamMeta, ParamState, int, str | None]] = {}
        for key, state in self._states.items():
            meta = self._meta.get(key)
            if meta is None:
                # meta を持たないキーはスナップショットに含めない（実質的に GUI 対象外）
                continue
            label = self._labels.get((key.op, key.site_id))
            # ParamState はミュータブルなのでコピーを返す
            state_copy = ParamState(**vars(state))
            result[key] = (
                meta,
                state_copy,
                self.get_ordinal(key.op, key.site_id),
                label,
            )
        return result

    def store_frame_params(self, records: list[FrameParamRecord]) -> None:
        """フレーム内で観測したレコードをストアに保存し、meta を最新化する。"""
        explicit_by_key_this_frame: dict[ParameterKey, bool] = {}
        for rec in records:
            self._observed_groups.add((str(rec.key.op), str(rec.key.site_id)))
            explicit_by_key_this_frame[rec.key] = bool(rec.explicit)
            # 新規なら生成、既存は保持
            self.ensure_state(
                rec.key,
                base_value=rec.base,
                initial_override=(not bool(rec.explicit)),
            )
            # meta は初出時に確定し、以後は保持する（GUI 側で編集できるようにする）
            if rec.key not in self._meta or self._meta[rec.key].kind != rec.meta.kind:
                self._meta[rec.key] = rec.meta
            # cc/ui_value/override はここでは変更しない
            if rec.chain_id is not None and rec.step_index is not None:
                chain_id = str(rec.chain_id)
                step_index = int(rec.step_index)
                self._assign_chain_ordinal(chain_id)
                self._effect_steps[(rec.key.op, rec.key.site_id)] = (chain_id, step_index)
        self._reconcile_loaded_groups_for_runtime()
        self._apply_explicit_override_follow_policy(explicit_by_key_this_frame)

    def _apply_explicit_override_follow_policy(
        self, explicit_by_key_this_frame: dict[ParameterKey, bool]
    ) -> None:
        """explicit/implicit の変化に追従して override を条件付きで更新する。"""

        for key, new_explicit in explicit_by_key_this_frame.items():
            prev_explicit = self._explicit_by_key.get(key)
            new_explicit = bool(new_explicit)

            if prev_explicit is None:
                # 旧 JSON（explicit 情報なし）もあるので、unknown の場合は触らず記録だけ行う。
                self._explicit_by_key[key] = new_explicit
                continue

            prev_explicit = bool(prev_explicit)
            if prev_explicit == new_explicit:
                continue

            state = self._states.get(key)
            if state is None:
                self._explicit_by_key[key] = new_explicit
                continue

            default_override_prev = not prev_explicit
            default_override_new = not new_explicit

            # 既定値のままなら追従して切り替える。既にユーザーが切り替え済みなら触らない。
            if bool(state.override) == bool(default_override_prev):
                state.override = bool(default_override_new)

            self._explicit_by_key[key] = new_explicit

    def _reconcile_loaded_groups_for_runtime(self) -> None:
        """ロード済みグループと観測済みグループの差分を再リンクする（削除はしない）。"""

        if not self._loaded_groups or not self._observed_groups:
            return

        # Style（global）は常設キーとして扱うため scope 外。
        from .style import STYLE_OP

        loaded_targets = {
            (op, site_id)
            for op, site_id in self._loaded_groups
            if op not in {STYLE_OP}
        }
        observed_targets = {
            (op, site_id)
            for op, site_id in self._observed_groups
            if op not in {STYLE_OP}
        }

        fresh = observed_targets - loaded_targets
        if not fresh:
            return

        stale = loaded_targets - observed_targets
        fresh_ops = {op for op, _site_id in fresh}
        stale_candidates = {g for g in stale if g[0] in fresh_ops}
        if not stale_candidates:
            return

        snapshot = self.snapshot()
        fingerprints = build_group_fingerprints(snapshot)
        mapping = match_groups(
            stale=sorted(stale_candidates),
            fresh=sorted(fresh),
            fingerprints=fingerprints,
        )
        for old_group, new_group in mapping.items():
            pair = (old_group, new_group)
            if pair in self._reconcile_applied:
                continue
            self.migrate_group(old_group, new_group)
            self._reconcile_applied.add(pair)

    def prune_stale_loaded_groups(self) -> None:
        """実行終了時に、今回の実行で観測されなかったロード済みグループを削除する。"""

        if not self._loaded_groups:
            return

        # 保存直前にもう一度だけ再リンクを試みる（最後まで観測した集合で最善を尽くす）。
        self._reconcile_loaded_groups_for_runtime()

        from .style import STYLE_OP

        loaded_targets = {
            (op, site_id)
            for op, site_id in self._loaded_groups
            if op not in {STYLE_OP}
        }
        observed_targets = {
            (op, site_id)
            for op, site_id in self._observed_groups
            if op not in {STYLE_OP}
        }

        stale = loaded_targets - observed_targets
        self.prune_groups(stale)

    def snapshot_for_gui(
        self,
    ) -> dict[ParameterKey, tuple[ParamMeta, ParamState, int, str | None]]:
        """Parameter GUI 表示用のスナップショットを返す。

        Notes
        -----
        実行中に観測されなかった「ロード済みグループ」は GUI から隠す。
        （ストア内から削除するのは保存時の `prune_stale_loaded_groups()` が担う）
        """

        snapshot = self.snapshot()
        if not self._loaded_groups:
            return snapshot

        from .style import STYLE_OP

        loaded_targets = {
            (op, site_id)
            for op, site_id in self._loaded_groups
            if op not in {STYLE_OP}
        }
        observed_targets = {
            (op, site_id)
            for op, site_id in self._observed_groups
            if op not in {STYLE_OP}
        }

        hide_groups = loaded_targets - observed_targets
        if not hide_groups:
            return snapshot

        return {
            key: value
            for key, value in snapshot.items()
            if (str(key.op), str(key.site_id)) not in hide_groups
        }

    def group_keys(self, op: str, site_id: str) -> list[ParameterKey]:
        """(op, site_id) グループに属する ParameterKey の一覧を返す。"""

        keys: set[ParameterKey] = set()
        for key in self._states.keys():
            if key.op == op and key.site_id == site_id:
                keys.add(key)
        for key in self._meta.keys():
            if key.op == op and key.site_id == site_id:
                keys.add(key)
        return sorted(keys, key=lambda k: str(k.arg))

    def migrate_group(
        self,
        old_group: tuple[str, str],
        new_group: tuple[str, str],
    ) -> None:
        """old_group の GUI 状態/メタを new_group へ可能な範囲で移す。"""

        old_op, old_site_id = old_group
        new_op, new_site_id = new_group
        if old_op != new_op:
            raise ValueError(f"op mismatch: {old_group!r} -> {new_group!r}")
        op = str(old_op)

        old_label = self._labels.get((op, str(old_site_id)))
        if old_label is not None and (op, str(new_site_id)) not in self._labels:
            self._labels[(op, str(new_site_id))] = old_label

        # ordinal は GUI のグループ安定化に寄与するので、可能なら付け替える。
        ordinal_map = self._ordinals.get(op)
        if ordinal_map is not None and str(old_site_id) in ordinal_map:
            ordinal = int(ordinal_map.pop(str(old_site_id)))
            ordinal_map[str(new_site_id)] = ordinal

        # arg 単位で meta/state を移す（kind 一致のみ）。
        for old_key in self.group_keys(op, str(old_site_id)):
            new_key = ParameterKey(op=op, site_id=str(new_site_id), arg=str(old_key.arg))
            old_meta = self._meta.get(old_key)
            new_meta = self._meta.get(new_key)
            if old_meta is None or new_meta is None:
                continue
            if old_meta.kind != new_meta.kind:
                continue

            old_state = self._states.get(old_key)
            new_state = self._states.get(new_key)
            if old_state is not None and new_state is not None:
                new_state.override = bool(old_state.override)
                new_state.ui_value = old_state.ui_value
                new_state.cc_key = old_state.cc_key

            old_explicit = self._explicit_by_key.get(old_key)
            if old_explicit is not None and new_key not in self._explicit_by_key:
                self._explicit_by_key[new_key] = bool(old_explicit)

            ui_min = old_meta.ui_min if old_meta.ui_min is not None else new_meta.ui_min
            ui_max = old_meta.ui_max if old_meta.ui_max is not None else new_meta.ui_max
            if ui_min != new_meta.ui_min or ui_max != new_meta.ui_max:
                self._meta[new_key] = ParamMeta(
                    kind=str(new_meta.kind),
                    ui_min=ui_min,
                    ui_max=ui_max,
                    choices=new_meta.choices,
                )

    def prune_groups(self, groups_to_remove: Iterable[tuple[str, str]]) -> None:
        """指定された (op, site_id) グループをストアから削除する。"""

        groups = {(str(op), str(site_id)) for op, site_id in groups_to_remove}
        if not groups:
            return

        for key in list(self._states.keys()):
            if (str(key.op), str(key.site_id)) in groups:
                del self._states[key]
        for key in list(self._meta.keys()):
            if (str(key.op), str(key.site_id)) in groups:
                del self._meta[key]
        for key in list(self._explicit_by_key.keys()):
            if (str(key.op), str(key.site_id)) in groups:
                del self._explicit_by_key[key]

        for group in groups:
            self._labels.pop(group, None)

        for op, site_id in groups:
            mapping = self._ordinals.get(op)
            if mapping is not None:
                mapping.pop(site_id, None)
                if not mapping:
                    self._ordinals.pop(op, None)
            self._effect_steps.pop((op, site_id), None)

        used_chain_ids = {str(chain_id) for chain_id, _step in self._effect_steps.values()}
        for chain_id in list(self._chain_ordinals.keys()):
            if str(chain_id) not in used_chain_ids:
                del self._chain_ordinals[chain_id]

    def to_json(self) -> str:
        """状態・メタ・ordinal を JSON 文字列として保存する。"""
        data = {
            "states": [
                {
                    "op": k.op,
                    "site_id": k.site_id,
                    "arg": k.arg,
                    # 明示 kwargs は「起動時はコードが勝つ」が期待値なので、
                    # override=True を永続化しない（次回起動で override=False から開始する）。
                    "override": (
                        False if self._explicit_by_key.get(k) is True else bool(v.override)
                    ),
                    "ui_value": v.ui_value,
                    "cc_key": v.cc_key,
                }
                for k, v in self._states.items()
            ],
            "meta": [
                {
                    "op": k.op,
                    "site_id": k.site_id,
                    "arg": k.arg,
                    "kind": m.kind,
                    "ui_min": m.ui_min,
                    "ui_max": m.ui_max,
                    "choices": list(m.choices) if m.choices is not None else None,
                }
                for k, m in self._meta.items()
            ],
            "labels": [
                {"op": op, "site_id": site_id, "label": label}
                for (op, site_id), label in self._labels.items()
            ],
            "ordinals": self._ordinals,
            "effect_steps": [
                {
                    "op": op,
                    "site_id": site_id,
                    "chain_id": chain_id,
                    "step_index": step_index,
                }
                for (op, site_id), (chain_id, step_index) in self._effect_steps.items()
            ],
            "chain_ordinals": self._chain_ordinals,
            "explicit": [
                {
                    "op": k.op,
                    "site_id": k.site_id,
                    "arg": k.arg,
                    "explicit": bool(v),
                }
                for k, v in self._explicit_by_key.items()
            ],
        }
        return json.dumps(data)

    @classmethod
    def from_json(cls, payload: str) -> "ParamStore":
        """to_json の出力からストアを復元する。"""
        obj = json.loads(payload)
        store = cls()
        for item in obj.get("states", []):
            key = ParameterKey(op=item["op"], site_id=item["site_id"], arg=item["arg"])
            raw_cc = item.get("cc_key")
            if isinstance(raw_cc, list) and len(raw_cc) == 3:
                cc_tuple = tuple(None if v is None else int(v) for v in raw_cc)
                cc_key = None if cc_tuple == (None, None, None) else cc_tuple
            elif raw_cc is None:
                cc_key = None
            else:
                cc_key = int(raw_cc)
            state = ParamState(ui_value=item.get("ui_value"), cc_key=cc_key)
            if "override" in item:
                state.override = bool(item["override"])
            store._states[key] = state

        for item in obj.get("meta", []):
            key = ParameterKey(op=item["op"], site_id=item["site_id"], arg=item["arg"])
            meta = ParamMeta(
                kind=item["kind"],
                ui_min=item.get("ui_min"),
                ui_max=item.get("ui_max"),
                choices=item.get("choices"),
            )
            store._meta[key] = meta
        for item in obj.get("labels", []):
            store._labels[(item["op"], item["site_id"])] = item["label"]
        store._ordinals = obj.get("ordinals", {})
        for item in obj.get("effect_steps", []):
            store._effect_steps[(item["op"], item["site_id"])] = (
                item["chain_id"],
                int(item["step_index"]),
            )
        store._chain_ordinals = obj.get("chain_ordinals", {})
        for item in obj.get("explicit", []):
            try:
                key = ParameterKey(
                    op=str(item["op"]),
                    site_id=str(item["site_id"]),
                    arg=str(item["arg"]),
                )
            except Exception:
                continue
            store._explicit_by_key[key] = bool(item.get("explicit", False))
        # explicit=True のキーは再起動時に override=False から開始する。
        for key, is_explicit in store._explicit_by_key.items():
            if is_explicit is True and key in store._states:
                store._states[key].override = False
        store._loaded_groups = {
            (str(k.op), str(k.site_id)) for k in set(store._states) | set(store._meta)
        }
        return store

    def ordinals(self) -> dict[str, dict[str, int]]:
        """op ごとの ordinal マップを返す。"""
        return self._ordinals

    def effect_steps(self) -> dict[tuple[str, str], tuple[str, int]]:
        """(op, site_id) -> (chain_id, step_index) のコピーを返す。"""

        return dict(self._effect_steps)

    def get_effect_step(self, op: str, site_id: str) -> tuple[str, int] | None:
        """effect ステップ情報を返す。未登録なら None。"""

        return self._effect_steps.get((op, site_id))

    def chain_ordinals(self) -> dict[str, int]:
        """chain_id -> ordinal のコピーを返す。"""

        return dict(self._chain_ordinals)

    def meta_items(self) -> Iterable[tuple[ParameterKey, ParamMeta]]:
        """(ParameterKey, ParamMeta) のイテレータを返す。"""
        return self._meta.items()

    def set_label(self, op: str, site_id: str, label: str) -> None:
        """ヘッダ表示用のラベルを設定（上書き可）。"""
        self._labels[(op, site_id)] = self._trim_label(label)

    def get_label(self, op: str, site_id: str) -> str | None:
        return self._labels.get((op, site_id))

    @staticmethod
    def _trim_label(label: str) -> str:
        if len(label) <= MAX_LABEL_LENGTH:
            return label
        return label[:MAX_LABEL_LENGTH]
