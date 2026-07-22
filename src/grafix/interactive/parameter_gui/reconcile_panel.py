# どこで: `src/grafix/interactive/parameter_gui/reconcile_panel.py`。
# 何を: ambiguous parameter reconcile orphan を明示的な 1:1 選択肢へ変換して描画する。
# なぜ: 自動選択で調整値を誤移行せず、ユーザーが旧 group を確認して再リンクできるようにするため。

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from types import ModuleType

from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.identity import GroupKey
from grafix.core.parameters.reconcile import ReconcileOrphan
from grafix.core.parameters.reconcile_ops import (
    list_reconcile_orphans,
    manual_migrate_orphan,
)
from grafix.core.parameters.store import ParamStore


@dataclass(frozen=True, slots=True)
class ReconcileOrphanView:
    """1 つの current group と、選択可能な saved group 候補。"""

    new_group: GroupKey
    candidate_old_groups: tuple[GroupKey, ...]
    reason: str
    reason_text: str
    score: int


@dataclass(frozen=True, slots=True)
class ReconcileOrphanPanelModel:
    """Inspector の reconcile popup に渡す immutable model。"""

    orphans: tuple[ReconcileOrphanView, ...]
    empty_message: str = "No ambiguous parameter links."

    @property
    def orphan_count(self) -> int:
        return len(self.orphans)

    @property
    def candidate_count(self) -> int:
        return sum(len(orphan.candidate_old_groups) for orphan in self.orphans)


@dataclass(frozen=True, slots=True)
class ReconcileMigrationRequest:
    """ユーザーが選択した 1:1 manual migration。"""

    old_group: GroupKey
    new_group: GroupKey


@dataclass(frozen=True, slots=True)
class ReconcileMigrationResult:
    """manual migration command の結果と最新 popup model。"""

    changed: bool
    model: ReconcileOrphanPanelModel
    error: str | None = None


def reconcile_reason_text(reason: object) -> str:
    """core reason code をユーザー向け説明へ変換する。"""

    descriptions = {
        "tie": "Multiple saved groups have the same match score.",
        "claimed": "Multiple current groups claim the same saved group.",
    }
    return descriptions.get(str(reason), "A manual 1:1 choice is required.")


def reconcile_orphan_panel_model(
    orphans: Sequence[ReconcileOrphan],
) -> ReconcileOrphanPanelModel:
    """core orphan 列から、安定順の純粋な popup model を構築する。"""

    views = [
        ReconcileOrphanView(
            new_group=orphan.new_group,
            candidate_old_groups=tuple(sorted(orphan.candidate_old_groups)),
            reason=orphan.reason,
            reason_text=reconcile_reason_text(orphan.reason),
            score=orphan.score,
        )
        for orphan in orphans
    ]
    views.sort(key=lambda view: view.new_group)
    return ReconcileOrphanPanelModel(orphans=tuple(views))


def apply_reconcile_migration(
    store: ParamStore,
    request: ReconcileMigrationRequest,
    *,
    history: ParamStoreHistory | None = None,
) -> ReconcileMigrationResult:
    """選択済み 1:1 migration を実行し、最新 model と error を返す。"""

    try:
        manual_migrate_orphan(
            store,
            request.old_group,
            request.new_group,
            history=history,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return ReconcileMigrationResult(
            changed=False,
            model=reconcile_orphan_panel_model(list_reconcile_orphans(store)),
            error=str(exc),
        )
    return ReconcileMigrationResult(
        changed=True,
        model=reconcile_orphan_panel_model(list_reconcile_orphans(store)),
    )


def format_parameter_group(group: GroupKey) -> str:
    """``(op, site_id)`` を popup の 1 行表示へ整形する。"""

    return f"{group[0]}  ·  {group[1]}"


def render_reconcile_orphan_popup(
    imgui: ModuleType,
    model: ReconcileOrphanPanelModel,
    *,
    error_message: str | None = None,
) -> ReconcileMigrationRequest | None:
    """orphan 候補を描画し、クリックされた 1:1 migration だけを返す。"""

    imgui.text("Ambiguous parameter links")
    imgui.text_disabled(
        "Choose which saved group should provide values for each current group."
    )
    if error_message:
        imgui.text_disabled(f"Could not relink: {error_message}")

    if not model.orphans:
        imgui.separator()
        imgui.text_disabled(model.empty_message)
        return None

    request: ReconcileMigrationRequest | None = None
    for orphan_index, orphan in enumerate(model.orphans):
        imgui.separator()
        imgui.text(f"Current group: {format_parameter_group(orphan.new_group)}")
        imgui.text_disabled(
            f"Reason: {orphan.reason_text}  ·  score {orphan.score}"
        )
        if not orphan.candidate_old_groups:
            imgui.text_disabled(
                "No saved candidates remain. Reload the current code to rescan."
            )
            continue
        for candidate_index, old_group in enumerate(orphan.candidate_old_groups):
            imgui.text(f"Saved old group: {format_parameter_group(old_group)}")
            imgui.same_line()
            label = (
                "Relink 1:1"
                f"##reconcile_{orphan_index}_{candidate_index}"
            )
            clicked = bool(imgui.small_button(label))
            if clicked and request is None:
                request = ReconcileMigrationRequest(
                    old_group=old_group,
                    new_group=orphan.new_group,
                )
    return request


__all__ = [
    "ReconcileMigrationResult",
    "ReconcileMigrationRequest",
    "ReconcileOrphanPanelModel",
    "ReconcileOrphanView",
    "apply_reconcile_migration",
    "format_parameter_group",
    "reconcile_orphan_panel_model",
    "reconcile_reason_text",
    "render_reconcile_orphan_popup",
]
