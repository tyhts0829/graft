"""
Purpose:
    parameterのloaded/observed状態とeffective値を追うruntime値、およびload/capture metadataを定義する。
Use when:
    reconcile可視性、effective source cache、またはparameter provenanceの受渡しを変更する場合。
Constraints:
    - runtime観測を永続adjustmentから分離し、outer layerには時点固定のParamRuntimeViewだけを渡す。
    - persistent revision、effective revision、visibility revisionの役割を混同しない。
    - load provenanceとdiagnosticsをParamStoreRuntimeへ格納せず、session側のvalueとして保つ。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Iterable, Literal

from .identity import GroupKey
from .key import ParameterKey
from .reconcile import ReconcileOrphan
from .source import ParameterLoadMode, ValueSource

LoadProvenance = Literal["primary", "session_recovery", "quarantined"]


@dataclass(slots=True)
class _GroupVisibilityTracker:
    revision: int = 0

    def touch(self) -> None:
        self.revision += 1


class _TrackedGroupSet(set[GroupKey]):
    """loaded/observed group mutation を共有 revision へ接続する set。"""

    __slots__ = ("_tracker",)

    def __init__(self, values: Iterable[GroupKey] = ()) -> None:
        super().__init__(values)
        self._tracker: _GroupVisibilityTracker | None = None

    def bind(self, tracker: _GroupVisibilityTracker) -> None:
        self._tracker = tracker

    def _touch(self) -> None:
        tracker = self._tracker
        if tracker is not None:
            tracker.touch()

    def add(self, element: GroupKey) -> None:
        if element in self:
            return
        super().add(element)
        self._touch()

    def discard(self, element: GroupKey) -> None:
        if element not in self:
            return
        super().discard(element)
        self._touch()

    def remove(self, element: GroupKey) -> None:
        super().remove(element)
        self._touch()

    def pop(self) -> GroupKey:
        element = super().pop()
        self._touch()
        return element

    def clear(self) -> None:
        if not self:
            return
        super().clear()
        self._touch()

    def update(self, *others: Iterable[GroupKey]) -> None:
        before = len(self)
        super().update(*others)
        if len(self) != before:
            self._touch()

    def difference_update(self, *others: Iterable[object]) -> None:
        before = len(self)
        super().difference_update(*others)
        if len(self) != before:
            self._touch()

    def intersection_update(self, *others: Iterable[object]) -> None:
        before = set(self)
        super().intersection_update(*others)
        if self != before:
            self._touch()

    def symmetric_difference_update(self, other: Iterable[GroupKey]) -> None:
        before = set(self)
        super().symmetric_difference_update(other)
        if self != before:
            self._touch()

    def __ior__(self, other: set[GroupKey]) -> _TrackedGroupSet:  # type: ignore[override,misc]
        self.update(other)
        return self

    def __iand__(self, other: set[object]) -> _TrackedGroupSet:  # type: ignore[override,misc]
        self.intersection_update(other)
        return self

    def __isub__(self, other: set[object]) -> _TrackedGroupSet:  # type: ignore[override,misc]
        self.difference_update(other)
        return self

    def __ixor__(self, other: set[GroupKey]) -> _TrackedGroupSet:  # type: ignore[override,misc]
        self.symmetric_difference_update(other)
        return self


@dataclass(frozen=True, slots=True)
class ParamStoreLoadDiagnostic:
    """ParamStore load で発生した user-facing 診断材料。"""

    code: str
    summary: str
    details: str = ""
    backup_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ParameterLoadState:
    """filesystem load が確定した provenance と診断。"""

    provenance: LoadProvenance = "primary"
    diagnostics: tuple[ParamStoreLoadDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if type(self.provenance) is not str or self.provenance not in {
            "primary",
            "session_recovery",
            "quarantined",
        }:
            raise ValueError("provenance は定義済み LoadProvenance です")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not ParamStoreLoadDiagnostic for item in self.diagnostics
        ):
            raise TypeError(
                "diagnostics は ParamStoreLoadDiagnostic の tuple です"
            )


@dataclass(frozen=True, slots=True)
class ParameterCaptureState:
    """capture manifest に同時記録する parameter load 状態。

    ``source`` と ``load_provenance`` は同じ session state の標本である。
    別々の provider から読むと Keep/Discard の境界で矛盾した組を作れるため、
    capture 層へはこの value を一つだけ渡す。
    """

    source: ParameterLoadMode
    load_provenance: LoadProvenance

    def __post_init__(self) -> None:
        source = self.source
        if not isinstance(source, Path):
            if type(source) is not str or source not in {
                "code",
                "saved",
                "recovery",
            }:
                raise ValueError(
                    "source は 'code'、'saved'、'recovery'、Path のいずれかです"
                )
        if type(self.load_provenance) is not str or self.load_provenance not in {
            "primary",
            "session_recovery",
            "quarantined",
        }:
            raise ValueError("load_provenance は定義済み LoadProvenance です")


@dataclass(frozen=True, slots=True)
class ParamRuntimeView:
    """outer layer が参照する ParamStore runtime の read-only view。"""

    loaded_groups: frozenset[GroupKey]
    observed_groups: frozenset[GroupKey]
    display_order_by_group: Mapping[GroupKey, int]
    last_effective_by_key: Mapping[ParameterKey, object]
    last_source_by_key: Mapping[ParameterKey, ValueSource]
    effective_revision: int
    visibility_revision: int

    def visibility_cache_token(self) -> tuple[int]:
        """可視性 cache 用の immutable revision token を返す。"""

        return (self.visibility_revision,)


@dataclass(slots=True, kw_only=True)
class ParamStoreRuntime:
    """ParamStore の実行時情報。"""

    loaded_groups: set[GroupKey] = field(default_factory=_TrackedGroupSet)
    observed_groups: set[GroupKey] = field(default_factory=_TrackedGroupSet)
    reconcile_applied: set[tuple[GroupKey, GroupKey]] = field(
        default_factory=set
    )
    display_order_by_group: dict[GroupKey, int] = field(default_factory=dict)
    next_display_order: int = 1
    last_effective_by_key: dict[ParameterKey, object] = field(default_factory=dict)
    warned_unknown_args: set[tuple[str, str]] = field(default_factory=set)
    last_source_by_key: dict[ParameterKey, ValueSource] = field(default_factory=dict)
    reconcile_orphans: dict[GroupKey, ReconcileOrphan] = field(
        default_factory=dict
    )
    # effective/source の最終 snapshot が変わった frame ごとに 1 回だけ進む。
    # 永続 store の revision と分け、毎 frame 更新され得る provenance/GUI cache の
    # 無効化に使う。
    effective_revision: int = 0
    _visibility_tracker: _GroupVisibilityTracker = field(
        default_factory=_GroupVisibilityTracker,
        init=False,
        repr=False,
        compare=False,
    )
    _effective_change_revision: int = field(
        default=-1,
        init=False,
        repr=False,
        compare=False,
    )
    _effective_changed_keys: tuple[ParameterKey, ...] = field(
        default=(),
        init=False,
        repr=False,
        compare=False,
    )

    _VISIBILITY_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"loaded_groups", "observed_groups"}
    )

    def __post_init__(self) -> None:
        for name in self._VISIBILITY_FIELDS:
            groups = _TrackedGroupSet(getattr(self, name))
            groups.bind(self._visibility_tracker)
            object.__setattr__(self, name, groups)

    def __setattr__(self, name: str, value: object) -> None:
        tracker = getattr(self, "_visibility_tracker", None)
        if tracker is not None and name in self._VISIBILITY_FIELDS:
            groups = _TrackedGroupSet(value)  # type: ignore[arg-type]
            groups.bind(tracker)
            previous = getattr(self, name)
            object.__setattr__(self, name, groups)
            if previous != groups:
                tracker.touch()
            return
        object.__setattr__(self, name, value)

    @property
    def visibility_revision(self) -> int:
        """tracked loaded/observed set が変化した回数を返す。"""

        return int(self._visibility_tracker.revision)

    def visibility_cache_token(self) -> tuple[int]:
        """可視性 cache 用の revision token を返す。"""

        return (self.visibility_revision,)

    def record_effective_changes(
        self,
        keys: Iterable[ParameterKey],
    ) -> None:
        """effective/source の最終差分を 1 frame 分として記録する。"""

        changed = tuple(dict.fromkeys(keys))
        if not changed:
            return
        self.effective_revision += 1
        # GUI は直前 frame との差分だけを使う。履歴を蓄積すると
        # all-key animation で key 数×frame 数の保持になるため、latest 1 件を
        # 上書きし、revision gap は呼び出し側の full fallback に委ねる。
        self._effective_change_revision = self.effective_revision
        self._effective_changed_keys = changed

    def effective_changes_since(
        self,
        revision: int,
    ) -> frozenset[ParameterKey] | None:
        """指定 revision 以降の変更 key を返し、log 欠落時は ``None``。"""

        since = int(revision)
        if since == self.effective_revision:
            return frozenset()
        if since < 0 or since > self.effective_revision:
            return None
        if (
            since == self.effective_revision - 1
            and self._effective_change_revision == self.effective_revision
        ):
            return frozenset(self._effective_changed_keys)
        return None


__all__ = [
    "LoadProvenance",
    "ParameterCaptureState",
    "ParameterLoadState",
    "ParamRuntimeView",
    "ParamStoreLoadDiagnostic",
    "ParamStoreRuntime",
]
