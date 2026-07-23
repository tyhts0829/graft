# どこで: `src/grafix/interactive/parameter_gui/variation_panel.py`。
# 何を: named variation と探索 scope を Inspector 用の immutable model へ整形する。
# なぜ: popup 描画と ParamStore 操作を分離し、一覧・差分・scope を単体テスト可能にするため。

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias

from grafix.core.parameters.favorites import favorite_parameter_keys
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.variations import (
    diff_variation,
    list_variations,
    locked_parameter_keys,
)

if TYPE_CHECKING:
    from .table_view import ParameterTableView

VariationScope = Literal["filtered", "favorites"]


class VariationThumbnailArtifact(Protocol):
    """Variation commit まで callback が所有する thumbnail artifact。"""

    @property
    def path(self) -> Path:
        """CaptureService が実際に公開した PNG path。"""

        ...

    def discard(self) -> None:
        """今回公開した artifact family だけを破棄する。"""

        ...


VariationThumbnailCapture: TypeAlias = Callable[
    [str],
    VariationThumbnailArtifact,
]
VariationThumbnailPreview: TypeAlias = Callable[[object, Path], None]


@dataclass(frozen=True, slots=True)
class VariationListItem:
    """named variation 一件の表示情報。"""

    name: str
    note: str
    created_at: float
    timestamp: str
    seed: int | None
    diff_count: int
    thumbnail_path: Path | None


@dataclass(frozen=True, slots=True)
class VariationPanelModel:
    """variation popup の read-only model。"""

    items: tuple[VariationListItem, ...]
    empty_message: str = "No saved variations yet."

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.items)


@dataclass(frozen=True, slots=True)
class VariationScopeSummary:
    """randomize/lock/morph が対象にする parameter scope の集計。"""

    scope: VariationScope
    keys: tuple[ParameterKey, ...]
    locked_count: int

    @property
    def parameter_count(self) -> int:
        return len(self.keys)


@dataclass(slots=True)
class VariationPanelState:
    """popup 内だけで保持する入力・選択状態。"""

    new_name: str = ""
    new_note: str = ""
    include_seed: bool = True
    random_seed: int = 0
    scope: VariationScope = "filtered"
    selected_name: str | None = None
    target_name: str = ""
    duplicate_name: str = ""
    pending_delete_name: str | None = None
    morph_a: str | None = None
    morph_b: str | None = None
    morph_amount: float = 0.5
    notice: str | None = None


def format_variation_timestamp(created_at: float) -> str:
    """timezone に依存しない UTC timestamp を返す。"""

    return datetime.fromtimestamp(float(created_at), tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def variation_panel_model(store: ParamStore) -> VariationPanelModel:
    """store の named variations と現在値との差分件数を一覧化する。"""

    items = tuple(
        VariationListItem(
            name=variation.name,
            note=variation.note,
            created_at=float(variation.created_at),
            timestamp=format_variation_timestamp(variation.created_at),
            seed=variation.seed,
            diff_count=len(diff_variation(store, variation.name)),
            thumbnail_path=(
                None
                if variation.thumbnail_path is None
                else Path(variation.thumbnail_path)
            ),
        )
        for variation in list_variations(store)
    )
    return VariationPanelModel(items=items)


def filtered_parameter_keys(view: ParameterTableView) -> tuple[ParameterKey, ...]:
    """現在の search/filter/visibility を通過した parameter key を返す。"""

    keys = [
        ParameterKey(row.op, row.site_id, row.arg)
        for row, visible in zip(
            view.model.rows,
            view.visible_mask,
            strict=True,
        )
        if visible
    ]
    return tuple(sorted(set(keys), key=lambda key: (key.op, key.site_id, key.arg)))


def variation_scope_summary(
    store: ParamStore,
    view: ParameterTableView,
    scope: VariationScope,
) -> VariationScopeSummary:
    """favorite または現在 filter 対象の探索 scope を返す。"""

    if scope == "favorites":
        keys = favorite_parameter_keys(store)
    elif scope == "filtered":
        keys = filtered_parameter_keys(view)
    else:
        raise ValueError(f"unknown variation scope: {scope!r}")
    locked = frozenset(locked_parameter_keys(store))
    return VariationScopeSummary(
        scope=scope,
        keys=keys,
        locked_count=sum(1 for key in keys if key in locked),
    )


def normalize_variation_selection(
    names: Iterable[str],
    selected: str | None,
) -> str | None:
    """削除/rename 後も有効な selection、または先頭を返す。"""

    ordered = tuple(str(name) for name in names)
    if selected in ordered:
        return selected
    return ordered[0] if ordered else None


__all__ = [
    "VariationListItem",
    "VariationPanelModel",
    "VariationPanelState",
    "VariationScope",
    "VariationScopeSummary",
    "VariationThumbnailArtifact",
    "VariationThumbnailCapture",
    "VariationThumbnailPreview",
    "filtered_parameter_keys",
    "format_variation_timestamp",
    "normalize_variation_selection",
    "variation_panel_model",
    "variation_scope_summary",
]
