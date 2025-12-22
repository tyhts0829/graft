# どこで: `src/grafix/core/parameters/ordinals.py`。
# 何を: (op, site_id) の GUI 用 ordinal（連番）を管理する。
# なぜ: 採番/圧縮/移設ロジックを ParamStore から分離し、仕様の置き場所を明確にするため。

from __future__ import annotations


class GroupOrdinals:
    """op ごとの site_id -> ordinal を管理する。"""

    def __init__(self) -> None:
        self._by_op: dict[str, dict[str, int]] = {}

    def get(self, op: str, site_id: str) -> int | None:
        """既存 ordinal を返す。未登録なら None。"""

        mapping = self._by_op.get(str(op))
        if mapping is None:
            return None
        return mapping.get(str(site_id))

    def get_or_assign(self, op: str, site_id: str) -> int:
        """既存 ordinal を返し、未登録なら採番して返す。"""

        op = str(op)
        site_id = str(site_id)
        mapping = self._by_op.setdefault(op, {})
        if site_id in mapping:
            return int(mapping[site_id])
        ordinal = len(mapping) + 1
        mapping[site_id] = int(ordinal)
        return int(ordinal)

    def migrate(self, op: str, old_site_id: str, new_site_id: str) -> None:
        """op 内で old_site_id の ordinal を new_site_id へ移す（old も ordinal を保つ）。"""

        op = str(op)
        old_site_id = str(old_site_id)
        new_site_id = str(new_site_id)

        mapping = self._by_op.get(op)
        if mapping is None:
            return
        old_ordinal = mapping.get(old_site_id)
        if old_ordinal is None:
            return

        new_ordinal = mapping.get(new_site_id)
        mapping[new_site_id] = int(old_ordinal)

        # migrate は「新グループへ旧 ordinal を引き継ぐ」目的だが、
        # stale グループは prune まで残るため、snapshot 不変条件として old も ordinal を持ち続ける。
        if new_ordinal is not None:
            mapping[old_site_id] = int(new_ordinal)
        else:
            mapping[old_site_id] = int(max(mapping.values(), default=0) + 1)

    def delete(self, op: str, site_id: str) -> None:
        """指定グループの ordinal を削除する。"""

        op = str(op)
        site_id = str(site_id)
        mapping = self._by_op.get(op)
        if mapping is None:
            return
        mapping.pop(site_id, None)
        if not mapping:
            self._by_op.pop(op, None)

    def compact(self, op: str) -> None:
        """op の ordinal を 1..N の連番へ詰め直す（相対順は維持）。"""

        op = str(op)
        mapping = self._by_op.get(op)
        if not mapping:
            self._by_op.pop(op, None)
            return
        self._compact_mapping_in_place(mapping)

    def compact_all(self) -> None:
        """すべての op について ordinal を 1..N に詰め直す。"""

        for op in list(self._by_op.keys()):
            mapping = self._by_op.get(op)
            if not isinstance(mapping, dict) or not mapping:
                self._by_op.pop(op, None)
                continue
            self._compact_mapping_in_place(mapping)

    def as_dict(self) -> dict[str, dict[str, int]]:
        """内部辞書のコピーを返す。"""

        return {op: dict(mapping) for op, mapping in self._by_op.items()}

    def replace_from_dict(self, by_op: object) -> None:
        """dict 由来の値で内部辞書を置き換える。"""

        if not isinstance(by_op, dict):
            self._by_op = {}
            return

        out: dict[str, dict[str, int]] = {}
        for op, raw_mapping in by_op.items():
            if not isinstance(raw_mapping, dict):
                continue
            cleaned: dict[str, int] = {}
            for site_id, ordinal in raw_mapping.items():
                try:
                    cleaned[str(site_id)] = int(ordinal)  # type: ignore[arg-type]
                except Exception:
                    continue
            if cleaned:
                out[str(op)] = cleaned
        self._by_op = out

    @staticmethod
    def _compact_mapping_in_place(mapping: dict[str, int]) -> None:
        def _sort_key(item: tuple[str, int]) -> tuple[int, str]:
            site_id, ordinal = item
            try:
                ordinal_i = int(ordinal)
            except Exception:
                ordinal_i = 0
            return ordinal_i, str(site_id)

        ordered_site_ids = [site_id for site_id, _ in sorted(mapping.items(), key=_sort_key)]
        mapping.clear()
        for i, site_id in enumerate(ordered_site_ids, start=1):
            mapping[str(site_id)] = int(i)


__all__ = ["GroupOrdinals"]
