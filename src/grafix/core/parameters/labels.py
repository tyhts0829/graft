# どこで: `src/grafix/core/parameters/labels.py`。
# 何を: (op, site_id) -> label の管理を提供する。
# なぜ: ParamStore から label 管理の責務を分離し、変更の波及を減らすため。

from __future__ import annotations

MAX_LABEL_LENGTH = 64


class ParamLabels:
    """(op, site_id) -> label の薄い辞書ラッパ。"""

    def __init__(self) -> None:
        self._by_group: dict[tuple[str, str], str] = {}

    def get(self, op: str, site_id: str) -> str | None:
        """ラベルを返す。未登録なら None。"""

        return self._by_group.get((str(op), str(site_id)))

    def set(self, op: str, site_id: str, label: str) -> None:
        """ラベルを設定（上書き可）する。"""

        self._by_group[(str(op), str(site_id))] = self._trim(str(label))

    def delete(self, op: str, site_id: str) -> None:
        """指定グループのラベルを削除する。"""

        self._by_group.pop((str(op), str(site_id)), None)

    def as_dict(self) -> dict[tuple[str, str], str]:
        """内部辞書のコピーを返す。"""

        return dict(self._by_group)

    def replace_from_items(self, items: list[tuple[tuple[str, str], str]]) -> None:
        """(group, label) の列で内部辞書を置き換える。"""

        self._by_group = {group: self._trim(label) for group, label in items}

    @staticmethod
    def _trim(label: str) -> str:
        return label if len(label) <= MAX_LABEL_LENGTH else label[:MAX_LABEL_LENGTH]


__all__ = ["ParamLabels", "MAX_LABEL_LENGTH"]

