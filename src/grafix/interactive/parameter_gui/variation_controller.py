# どこで: `src/grafix/interactive/parameter_gui/variation_controller.py`。
# 何を: named variation popup の状態と command 実行を所有する。
# なぜ: ImGui 描画から store mutation、history、transport、thumbnail 境界を分離するため。

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, cast

from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.variations import (
    commit_variation,
    delete_variation,
    duplicate_variation,
    morph_variations,
    prepare_variation,
    randomize_parameters,
    rename_variation,
    restore_variation,
    set_parameters_locked,
)
from grafix.interactive.transport import TransportClock

from .variation_panel import (
    VariationPanelModel,
    VariationPanelState,
    VariationScopeSummary,
    VariationThumbnailCapture,
    VariationThumbnailPreview,
    normalize_variation_selection,
    variation_panel_model,
    variation_scope_summary,
)

if TYPE_CHECKING:
    from .table_view import ParameterTableView


@dataclass(frozen=True, slots=True)
class _ThumbnailArtifactOwnership:
    """Capture callback から一度だけ読み取った exact path と rollback command。"""

    path: Path
    discard: Callable[[], None]


class VariationController:
    """Named variation の状態寿命と command 境界を所有する。

    ImGui や window を参照せず、描画側から渡された immutable table view を
    scope へ変換して core operation を実行する。parameter 値を変更する command は
    ``ParamStoreHistory`` をそのまま利用し、1 command を 1 undo 単位に保つ。
    """

    def __init__(
        self,
        store: ParamStore,
        *,
        history: ParamStoreHistory | None = None,
        transport: TransportClock | None = None,
        thumbnail_capture: VariationThumbnailCapture | None = None,
        thumbnail_preview: VariationThumbnailPreview | None = None,
    ) -> None:
        if not isinstance(store, ParamStore):
            raise TypeError("store must be a ParamStore")
        self._store = store
        self._history = history
        self._transport = transport
        self._thumbnail_capture = thumbnail_capture
        self._thumbnail_preview = thumbnail_preview
        self._state = VariationPanelState()

    @property
    def state(self) -> VariationPanelState:
        """Popup の frame 間入力・選択状態を返す。"""

        return self._state

    @property
    def count(self) -> int:
        """保存済み variation 件数を返す。"""

        return self._store.variation_count()

    def synchronize_panel(self) -> VariationPanelModel:
        """現在の variation 一覧へ selection と morph pair を同期する。"""

        model = variation_panel_model(self._store)
        names = model.names
        state = self._state
        previous_selection = state.selected_name
        state.selected_name = normalize_variation_selection(names, state.selected_name)
        if state.selected_name is not None and (
            state.selected_name != previous_selection or not state.target_name
        ):
            state.target_name = state.selected_name
        if state.selected_name is not None and (
            state.selected_name != previous_selection or not state.duplicate_name
        ):
            state.duplicate_name = f"{state.selected_name} copy"
        state.morph_a = normalize_variation_selection(names, state.morph_a)
        state.morph_b = normalize_variation_selection(names, state.morph_b)
        if len(names) > 1 and state.morph_b == state.morph_a:
            state.morph_b = names[1] if names[0] == state.morph_a else names[0]
        return model

    def normalized_selection(
        self,
        names: tuple[str, ...],
        selected: str | None,
    ) -> str | None:
        """Combo の選択値を現在の一覧へ正規化する。"""

        return normalize_variation_selection(names, selected)

    def select(self, name: str) -> None:
        """一覧で選ばれた variation を rename/duplicate 対象へ反映する。"""

        selected = str(name)
        self._state.selected_name = selected
        self._state.target_name = selected
        self._state.duplicate_name = f"{selected} copy"

    def scope_summary(self, view: ParameterTableView) -> VariationScopeSummary:
        """現在の controller scope と table view から対象を集計する。"""

        return variation_scope_summary(self._store, view, self._state.scope)

    def save(self) -> bool:
        """現在値を draft 名で保存する。thumbnail 失敗は保存を妨げない。"""

        state = self._state
        name = state.new_name.strip()
        if not name:
            state.notice = "Enter a variation name before saving."
            return False
        transport = self._transport
        try:
            t = None if transport is None else transport.snapshot().t
            draft = prepare_variation(
                self._store,
                name,
                note=state.new_note,
                seed=state.random_seed if state.include_seed else None,
                t=t,
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            if str(exc).startswith("variation already exists:"):
                state.notice = f"Variation already exists: {name}."
            else:
                state.notice = f"Could not save variation: {exc}"
            return False

        artifact: _ThumbnailArtifactOwnership | None = None
        thumbnail_error: str | None = None
        capture = self._thumbnail_capture
        if callable(capture):
            try:
                artifact = _validated_thumbnail_artifact(capture(draft.name))
            except Exception as exc:
                # CaptureService boundary の失敗で parameter snapshot 自体を失わない。
                thumbnail_error = _error_detail(exc)

        try:
            variation = commit_variation(
                self._store,
                draft,
                thumbnail_path=None if artifact is None else artifact.path,
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            cleanup_error: str | None = None
            if artifact is not None:
                try:
                    artifact.discard()
                except Exception as discard_error:
                    cleanup_error = _error_detail(discard_error)
            details = [f"Could not save variation: {exc}"]
            if thumbnail_error is not None:
                details.append(f"thumbnail failed: {thumbnail_error}")
            if cleanup_error is not None:
                details.append(f"thumbnail cleanup failed: {cleanup_error}")
            state.notice = "; ".join(details)
            return False

        state.selected_name = variation.name
        state.target_name = variation.name
        state.duplicate_name = f"{variation.name} copy"
        state.morph_a = normalize_variation_selection(
            variation_panel_model(self._store).names,
            state.morph_a,
        )
        state.new_name = ""
        state.new_note = ""
        state.notice = (
            f"Saved {variation.name}; thumbnail failed: {thumbnail_error}"
            if thumbnail_error
            else f"Saved {variation.name}."
        )
        return True

    def load(self, name: str) -> bool:
        """Named variation を現在値へ restore する。"""

        try:
            changed = restore_variation(
                self._store,
                name,
                history=self._history,
            )
        except (KeyError, TypeError, ValueError) as exc:
            self._state.notice = f"Could not load variation: {exc}"
            return False
        self._state.notice = (
            f"Loaded {name}." if changed else f"{name} already matches the current values."
        )
        return changed

    def rename_selected(self) -> bool:
        """選択中 variation を draft 名へ変更する。"""

        state = self._state
        if state.selected_name is None:
            state.notice = "Select a variation to rename."
            return False
        previous = state.selected_name
        try:
            renamed = rename_variation(self._store, previous, state.target_name)
        except (KeyError, TypeError, ValueError) as exc:
            state.notice = f"Could not rename variation: {exc}"
            return False
        state.selected_name = renamed.name
        state.target_name = renamed.name
        state.duplicate_name = f"{renamed.name} copy"
        if state.morph_a == previous:
            state.morph_a = renamed.name
        if state.morph_b == previous:
            state.morph_b = renamed.name
        state.notice = f"Renamed {previous} to {renamed.name}."
        return renamed.name != previous

    def duplicate_selected(self) -> bool:
        """選択中 variation を draft 名へ複製する。"""

        state = self._state
        if state.selected_name is None:
            state.notice = "Select a variation to duplicate."
            return False
        try:
            duplicate = duplicate_variation(
                self._store,
                state.selected_name,
                state.duplicate_name,
            )
        except (KeyError, TypeError, ValueError) as exc:
            state.notice = f"Could not duplicate variation: {exc}"
            return False
        state.selected_name = duplicate.name
        state.target_name = duplicate.name
        state.duplicate_name = f"{duplicate.name} copy"
        state.notice = f"Duplicated as {duplicate.name}."
        return True

    def request_delete_selected(self) -> bool:
        """選択名を確認対象へ固定し、まだ削除は行わない。"""

        name = self._state.selected_name
        if name is None:
            self._state.notice = "Select a variation to delete."
            return False
        self._state.pending_delete_name = name
        return True

    def cancel_delete(self) -> None:
        """保留中の削除対象を破棄する。"""

        self._state.pending_delete_name = None

    def confirm_delete_pending(self) -> bool:
        """確認時に固定した variation だけを削除する。"""

        state = self._state
        name = state.pending_delete_name
        state.pending_delete_name = None
        if name is None:
            state.notice = "No variation is awaiting deletion."
            return False
        if not delete_variation(self._store, name):
            state.notice = f"Variation no longer exists: {name}."
            return False
        names = variation_panel_model(self._store).names
        state.selected_name = normalize_variation_selection(names, None)
        state.target_name = "" if state.selected_name is None else state.selected_name
        state.duplicate_name = "" if state.selected_name is None else f"{state.selected_name} copy"
        state.morph_a = normalize_variation_selection(names, state.morph_a)
        state.morph_b = normalize_variation_selection(names, state.morph_b)
        state.notice = f"Deleted {name}."
        return True

    def randomize(self, scope: VariationScopeSummary) -> bool:
        """Scope 内の unlocked numeric parameter を seed 付きで変更する。"""

        state = self._state
        if not scope.keys:
            state.notice = f"No parameters in {scope.scope} scope."
            return False
        if scope.locked_count == scope.parameter_count:
            state.notice = (
                f"All {scope.parameter_count} parameters in {scope.scope} scope are locked; "
                "nothing was randomized."
            )
            return False
        changed = randomize_parameters(
            self._store,
            scope.keys,
            seed=int(state.random_seed),
            history=self._history,
        )
        if changed:
            state.notice = (
                f"Randomized {len(changed)} / {scope.parameter_count} parameters "
                f"with seed {state.random_seed}."
            )
        else:
            state.notice = (
                f"No eligible unlocked numeric parameters in {scope.scope} scope; "
                "nothing was randomized."
            )
        return bool(changed)

    def set_scope_locked(
        self,
        scope: VariationScopeSummary,
        *,
        locked: bool,
    ) -> bool:
        """Scope の parameter lock を一括変更する。"""

        if not scope.keys:
            self._state.notice = f"No parameters in {scope.scope} scope."
            return False
        if locked and scope.locked_count == scope.parameter_count:
            self._state.notice = (
                f"All {scope.parameter_count} parameters in {scope.scope} scope are already locked."
            )
            return False
        if not locked and scope.locked_count == 0:
            self._state.notice = f"No parameters in {scope.scope} scope are locked."
            return False
        changed = set_parameters_locked(
            self._store,
            scope.keys,
            locked=bool(locked),
        )
        if changed:
            self._state.notice = (
                f"{'Locked' if locked else 'Unlocked'} {len(changed)} parameters "
                f"in {scope.scope} scope."
            )
        else:
            self._state.notice = f"No lock state changed in {scope.scope} scope."
        return bool(changed)

    def morph(self, scope: VariationScopeSummary) -> bool:
        """選択した 2 variation を現在 scope へ補間適用する。"""

        state = self._state
        if state.morph_a is None or state.morph_b is None:
            state.notice = "Save and select two variations before morphing."
            return False
        if state.morph_a == state.morph_b:
            state.notice = "Choose two different variations to morph."
            return False
        if not scope.keys:
            state.notice = f"No parameters in {scope.scope} scope; nothing was morphed."
            return False
        if scope.locked_count == scope.parameter_count:
            state.notice = (
                f"All {scope.parameter_count} parameters in {scope.scope} scope are locked; "
                "nothing was morphed."
            )
            return False
        try:
            changed = morph_variations(
                self._store,
                state.morph_a,
                state.morph_b,
                float(state.morph_amount),
                keys=scope.keys,
                history=self._history,
            )
        except (KeyError, TypeError, ValueError) as exc:
            state.notice = f"Could not morph variations: {exc}"
            return False
        if changed:
            state.notice = f"Morphed {len(changed)} parameters at {state.morph_amount:.2f}."
        else:
            state.notice = (
                f"No compatible unlocked parameters in {scope.scope} scope; nothing was morphed."
            )
        return bool(changed)

    def preview_thumbnail(self, target: object, path: Path) -> str | None:
        """Thumbnail callback を呼び、fallback/error 表示文字列を返す。"""

        preview = self._thumbnail_preview
        if not callable(preview):
            return f"Thumbnail: {path}"
        try:
            preview(target, path)
        except Exception as exc:
            return f"Thumbnail unavailable: {exc}"
        return None


def _validated_thumbnail_artifact(
    value: object,
) -> _ThumbnailArtifactOwnership:
    """Capture callback の最小 ownership contract を実行前に検証する。"""

    try:
        discard = getattr(value, "discard")
    except AttributeError:
        raise TypeError(
            "thumbnail capture must return an artifact with path and discard()"
        ) from None
    if not callable(discard):
        raise TypeError("thumbnail artifact discard must be callable")

    try:
        path = getattr(value, "path")
    except AttributeError:
        error = TypeError(
            "thumbnail capture must return an artifact with path and discard()"
        )
        _discard_invalid_thumbnail(discard, error)
    if not isinstance(path, Path):
        error = TypeError("thumbnail artifact path must be a Path")
        _discard_invalid_thumbnail(discard, error)

    return _ThumbnailArtifactOwnership(
        path=path,
        discard=cast(Callable[[], None], discard),
    )


def _discard_invalid_thumbnail(
    discard: Callable[..., object],
    error: Exception,
) -> NoReturn:
    """不正 contract でも rollback command があれば試し、元の診断を保つ。"""

    try:
        discard()
    except BaseException as cleanup_error:
        error.add_note(
            "Secondary cleanup failure (discard invalid thumbnail artifact): "
            f"{type(cleanup_error).__name__}: {cleanup_error}"
        )
    raise error


def _error_detail(error: BaseException) -> str:
    """Primary error と cleanup note を一つの user-facing 診断へ整形する。"""

    message = str(error) or type(error).__name__
    notes = tuple(str(note) for note in getattr(error, "__notes__", ()))
    return "; ".join((message, *notes))


__all__ = ["VariationController"]
