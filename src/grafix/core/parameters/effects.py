# どこで: `src/grafix/core/parameters/effects.py`。
# 何を: effect chain 情報（chain_id / step_index / chain ordinal）を管理する。
# なぜ: ParamStore から effect 情報管理の責務を分離し、GUI 表示順の仕様を局所化するため。

from __future__ import annotations


class EffectChainIndex:
    """(op, site_id) -> (chain_id, step_index) と chain_id -> ordinal を管理する。"""

    def __init__(self) -> None:
        self._step_by_site: dict[tuple[str, str], tuple[str, int]] = {}
        self._chain_ordinals: dict[str, int] = {}

    def record_step(self, *, op: str, site_id: str, chain_id: str, step_index: int) -> None:
        """effect ステップ情報を保存する。"""

        op = str(op)
        site_id = str(site_id)
        chain_id = str(chain_id)
        step_index = int(step_index)

        if chain_id not in self._chain_ordinals:
            # 既存 chain ordinal が穴あきでも「重複しない」ことを優先する。
            self._chain_ordinals[chain_id] = max(self._chain_ordinals.values(), default=0) + 1
        self._step_by_site[(op, site_id)] = (chain_id, step_index)

    def get_step(self, op: str, site_id: str) -> tuple[str, int] | None:
        """(op, site_id) の effect ステップ情報を返す。未登録なら None。"""

        return self._step_by_site.get((str(op), str(site_id)))

    def step_info_by_site(self) -> dict[tuple[str, str], tuple[str, int]]:
        """(op, site_id) -> (chain_id, step_index) のコピーを返す。"""

        return dict(self._step_by_site)

    def chain_ordinals(self) -> dict[str, int]:
        """chain_id -> ordinal のコピーを返す。"""

        return dict(self._chain_ordinals)

    def delete_step(self, op: str, site_id: str) -> None:
        """指定ステップ情報を削除する。"""

        self._step_by_site.pop((str(op), str(site_id)), None)

    def prune_unused_chains(self) -> None:
        """参照されなくなった chain_id を chain_ordinals から削除する。"""

        used_chain_ids = {str(chain_id) for chain_id, _step in self._step_by_site.values()}
        for chain_id in list(self._chain_ordinals.keys()):
            if str(chain_id) not in used_chain_ids:
                del self._chain_ordinals[chain_id]

    def replace_from_json(
        self,
        *,
        effect_steps: object,
        chain_ordinals: object,
    ) -> None:
        """JSON 由来の値で内部状態を置き換える。"""

        step_by_site: dict[tuple[str, str], tuple[str, int]] = {}
        chain_ids_in_order: list[str] = []
        seen_chain_ids: set[str] = set()
        if isinstance(effect_steps, list):
            for item in effect_steps:
                if not isinstance(item, dict):
                    continue
                try:
                    op = str(item["op"])
                    site_id = str(item["site_id"])
                    chain_id = str(item["chain_id"])
                    step_index = int(item["step_index"])
                except Exception:
                    continue
                step_by_site[(op, site_id)] = (chain_id, step_index)
                if chain_id not in seen_chain_ids:
                    seen_chain_ids.add(chain_id)
                    chain_ids_in_order.append(chain_id)

        chain_ordinal_by_id: dict[str, int] = {}
        if isinstance(chain_ordinals, dict):
            for chain_id, ordinal in chain_ordinals.items():
                try:
                    chain_ordinal_by_id[str(chain_id)] = int(ordinal)  # type: ignore[arg-type]
                except Exception:
                    continue

        # 既存 JSON の不整合（重複/0/負値など）がある場合は、ロード時に修復して汚染を止める。
        values = list(chain_ordinal_by_id.values())
        needs_repair = any(int(v) <= 0 for v in values) or (len(set(values)) != len(values))
        if needs_repair:
            ordered = sorted(
                chain_ordinal_by_id.items(), key=lambda it: (int(it[1]), str(it[0]))
            )
            chain_ordinal_by_id = {
                str(chain_id): int(i) for i, (chain_id, _old) in enumerate(ordered, start=1)
            }

        # chain_ordinals が欠けている/不完全な場合でも、step 情報から補完する。
        # 目的: GUI の “effect#N” とチェーン並びを安定させる。
        next_ordinal = max(chain_ordinal_by_id.values(), default=0) + 1
        for chain_id in chain_ids_in_order:
            if chain_id in chain_ordinal_by_id:
                continue
            chain_ordinal_by_id[chain_id] = int(next_ordinal)
            next_ordinal += 1

        self._step_by_site = step_by_site
        self._chain_ordinals = chain_ordinal_by_id


__all__ = ["EffectChainIndex"]
