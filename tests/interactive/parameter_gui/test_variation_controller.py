from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from grafix.core.parameters.favorites import set_parameters_favorite
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.parameters.variations import (
    create_variation,
    list_variations,
    set_parameters_locked,
)
from grafix.interactive.parameter_gui.parameter_filter import ParameterFilterState
from grafix.interactive.parameter_gui.catalog import current_parameter_gui_catalog
from grafix.interactive.parameter_gui.table_view import (
    ParameterTableViewCache,
    parameter_table_view_for_store,
)
from grafix.interactive.parameter_gui.variation_controller import VariationController
from grafix.interactive.transport import TransportClock


META = ParamMeta(
    kind="float",
    ui_min=0.0,
    ui_max=10.0,
    recommended_range=(1.0, 9.0),
)


@dataclass(slots=True)
class _ThumbnailArtifact:
    path: Path
    discarded: bool = False
    discard_error: Exception | None = None

    def discard(self) -> None:
        self.discarded = True
        if self.discard_error is not None:
            raise self.discard_error


def _store() -> tuple[ParamStore, ParameterKey, ParameterKey]:
    store = ParamStore()
    key_a = ParameterKey("circle", "site-a", "radius")
    key_b = ParameterKey("circle", "site-b", "radius")
    merge_frame_params(
        store,
        (
            FrameParamRecord(
                key=key_a,
                base=2.0,
                meta=META,
                effective=2.0,
                source="code",
                explicit=False,
            ),
            FrameParamRecord(
                key=key_b,
                base=4.0,
                meta=META,
                effective=4.0,
                source="code",
                explicit=False,
            ),
        ),
    )
    return store, key_a, key_b


def _set(store: ParamStore, key: ParameterKey, value: float) -> None:
    ok, error = update_state_from_ui(store, key, value, meta=META, override=True)
    assert ok and error is None


def _value(store: ParamStore, key: ParameterKey) -> float:
    state = store.get_state(key)
    assert state is not None
    return float(state.ui_value)


def _scope_view(
    store: ParamStore,
    *,
    query: str = "",
):
    return parameter_table_view_for_store(
        store,
        cache=ParameterTableViewCache(current_parameter_gui_catalog()),
        show_inactive_params=True,
        filter_state=ParameterFilterState(query=query),
    )


def test_controller_has_no_imgui_import() -> None:
    path = (
        Path(__file__).parents[3] / "src/grafix/interactive/parameter_gui/variation_controller.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))

    imported_roots = {
        alias.name.partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").partition(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert "imgui" not in imported_roots


def test_save_owns_transport_and_thumbnail_boundaries_then_load_is_undoable(
    tmp_path: Path,
) -> None:
    store, key_a, _key_b = _store()
    history = ParamStoreHistory(store)
    thumbnail = tmp_path / "variation.png"
    captured: list[str] = []
    controller = VariationController(
        store,
        history=history,
        transport=TransportClock(initial_t=3.25, playing=False),
        thumbnail_capture=lambda name: (
            captured.append(name) or _ThumbnailArtifact(thumbnail)
        ),
    )
    state = controller.state
    state.new_name = "  candidate  "
    state.new_note = "keep this"
    state.random_seed = 23

    assert controller.save() is True

    saved = list_variations(store)[0]
    assert captured == ["candidate"]
    assert saved.name == "candidate"
    assert saved.note == "keep this"
    assert saved.seed == 23
    assert saved.t == pytest.approx(3.25)
    assert saved.thumbnail_path == str(thumbnail)
    assert state.new_name == ""
    assert state.new_note == ""

    revision = store.revision
    assert controller.load("candidate") is False
    assert store.revision == revision
    assert history.undo_depth == 0
    assert state.notice == "candidate already matches the current values."

    _set(store, key_a, 8.0)
    history.synchronize()
    assert controller.load("candidate") is True
    assert _value(store, key_a) == 2.0
    assert history.undo_depth == 1
    assert history.undo() is True
    assert _value(store, key_a) == 8.0


def test_save_rejects_empty_or_duplicate_before_capture_and_survives_capture_failure() -> None:
    store, _key_a, _key_b = _store()
    calls: list[str] = []

    def failing_capture(name: str) -> None:
        calls.append(name)
        raise RuntimeError("preview unavailable")

    controller = VariationController(store, thumbnail_capture=failing_capture)
    state = controller.state

    assert controller.save() is False
    assert calls == []
    assert state.notice == "Enter a variation name before saving."

    state.new_name = "candidate"
    assert controller.save() is True
    assert calls == ["candidate"]
    assert list_variations(store)[0].thumbnail_path is None
    assert state.notice == "Saved candidate; thumbnail failed: preview unavailable"

    state.new_name = "candidate"
    assert controller.save() is False
    assert calls == ["candidate"]
    assert state.notice == "Variation already exists: candidate."


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("new_name", "x" * 81),
        ("new_note", object()),
        ("random_seed", True),
    ),
)
def test_save_validates_domain_input_before_thumbnail_capture(
    field: str,
    value: object,
) -> None:
    store, _key_a, _key_b = _store()
    calls: list[str] = []
    controller = VariationController(
        store,
        thumbnail_capture=lambda name: (
            calls.append(name) or _ThumbnailArtifact(Path("unused.png"))
        ),
    )
    state = controller.state
    state.new_name = "candidate"
    setattr(state, field, value)
    revision = store.revision

    assert controller.save() is False

    assert calls == []
    assert list_variations(store) == ()
    assert store.revision == revision


def test_save_validates_transport_time_before_thumbnail_capture() -> None:
    store, _key_a, _key_b = _store()
    calls: list[str] = []
    invalid_transport = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(t=True),
    )
    controller = VariationController(
        store,
        transport=cast(Any, invalid_transport),
        thumbnail_capture=lambda name: (
            calls.append(name) or _ThumbnailArtifact(Path("unused.png"))
        ),
    )
    controller.state.new_name = "candidate"
    revision = store.revision

    assert controller.save() is False

    assert calls == []
    assert list_variations(store) == ()
    assert store.revision == revision


def test_invalid_thumbnail_result_is_capture_failure_and_saves_without_path() -> None:
    store, _key_a, _key_b = _store()
    controller = VariationController(
        store,
        thumbnail_capture=lambda _name: Path("old-contract.png"),  # type: ignore[arg-type,return-value]
    )
    controller.state.new_name = "candidate"
    revision = store.revision

    assert controller.save() is True

    assert list_variations(store)[0].thumbnail_path is None
    assert store.revision == revision + 1
    assert controller.state.notice is not None
    assert "must return an artifact" in controller.state.notice


def test_invalid_owned_thumbnail_result_is_discarded_before_fallback_save() -> None:
    store, _key_a, _key_b = _store()

    class _InvalidArtifact:
        path = "not-a-path"
        discarded = False

        def discard(self) -> None:
            self.discarded = True

    artifact = _InvalidArtifact()
    controller = VariationController(
        store,
        thumbnail_capture=lambda _name: artifact,  # type: ignore[arg-type,return-value]
    )
    controller.state.new_name = "candidate"

    assert controller.save() is True

    assert artifact.discarded is True
    assert list_variations(store)[0].thumbnail_path is None
    assert controller.state.notice is not None
    assert "path must be a Path" in controller.state.notice


def test_thumbnail_path_is_sampled_once_before_commit(tmp_path: Path) -> None:
    store, _key_a, _key_b = _store()
    first_path = tmp_path / "published.png"
    second_path = tmp_path / "changed.png"

    class _DynamicArtifact:
        reads = 0

        @property
        def path(self) -> Path:
            self.reads += 1
            return first_path if self.reads == 1 else second_path

        def discard(self) -> None:
            return None

    artifact = _DynamicArtifact()
    controller = VariationController(
        store,
        thumbnail_capture=lambda _name: artifact,
    )
    controller.state.new_name = "candidate"

    assert controller.save() is True

    assert artifact.reads == 1
    assert list_variations(store)[0].thumbnail_path == str(first_path)


def test_capture_must_not_mutate_store_and_commit_failure_discards_artifact() -> None:
    store, _key_a, _key_b = _store()
    artifact = _ThumbnailArtifact(Path("candidate.png"))

    def mutate_store(_name: str) -> _ThumbnailArtifact:
        create_variation(store, "callback mutation", created_at=100.0)
        return artifact

    controller = VariationController(store, thumbnail_capture=mutate_store)
    controller.state.new_name = "candidate"

    assert controller.save() is False

    assert artifact.discarded is True
    assert [variation.name for variation in list_variations(store)] == [
        "callback mutation"
    ]
    assert controller.state.notice is not None
    assert "changed after variation was prepared" in controller.state.notice


def test_commit_and_discard_failures_are_both_visible_in_notice() -> None:
    store, _key_a, _key_b = _store()
    artifact = _ThumbnailArtifact(
        Path("candidate.png"),
        discard_error=OSError("manifest cleanup unavailable"),
    )

    def mutate_store(_name: str) -> _ThumbnailArtifact:
        create_variation(store, "callback mutation", created_at=100.0)
        return artifact

    controller = VariationController(store, thumbnail_capture=mutate_store)
    controller.state.new_name = "candidate"

    assert controller.save() is False

    assert artifact.discarded is True
    assert controller.state.notice is not None
    assert "changed after variation was prepared" in controller.state.notice
    assert "thumbnail cleanup failed: manifest cleanup unavailable" in (
        controller.state.notice
    )


def test_synchronize_select_rename_duplicate_and_confirm_fixed_delete_target() -> None:
    store, _key_a, _key_b = _store()
    create_variation(store, "first", created_at=100.0)
    create_variation(store, "second", created_at=200.0)
    controller = VariationController(store)

    model = controller.synchronize_panel()
    state = controller.state
    assert model.names == ("first", "second")
    assert state.selected_name == "first"
    assert state.target_name == "first"
    assert state.duplicate_name == "first copy"
    assert (state.morph_a, state.morph_b) == ("first", "second")

    state.target_name = "renamed"
    assert controller.rename_selected() is True
    assert [variation.name for variation in list_variations(store)] == [
        "renamed",
        "second",
    ]
    assert state.morph_a == "renamed"

    state.duplicate_name = "copy"
    assert controller.duplicate_selected() is True
    assert state.selected_name == "copy"
    assert controller.request_delete_selected() is True
    assert state.pending_delete_name == "copy"

    # Modal を開いた後に一覧 selection が変わっても、固定した対象だけを消す。
    controller.select("second")
    assert controller.confirm_delete_pending() is True
    assert [variation.name for variation in list_variations(store)] == [
        "renamed",
        "second",
    ]
    assert state.pending_delete_name is None


def test_rename_and_delete_noops_do_not_change_store_revision() -> None:
    store, _key_a, _key_b = _store()
    create_variation(store, "first")
    controller = VariationController(store)
    controller.synchronize_panel()
    state = controller.state

    revision = store.revision
    assert controller.rename_selected() is False
    assert store.revision == revision
    assert state.notice == "Renamed first to first."

    controller.cancel_delete()
    assert controller.confirm_delete_pending() is False
    assert store.revision == revision
    assert state.notice == "No variation is awaiting deletion."


def test_randomize_and_lock_use_supplied_scope_and_randomize_is_one_undo_unit() -> None:
    store, key_a, key_b = _store()
    set_parameters_favorite(store, (key_a,), favorite=True)
    history = ParamStoreHistory(store)
    controller = VariationController(store, history=history)
    controller.state.scope = "favorites"
    controller.state.random_seed = 91

    favorite_scope = controller.scope_summary(_scope_view(store))
    before_b = _value(store, key_b)
    assert controller.randomize(favorite_scope) is True
    assert _value(store, key_a) != 2.0
    assert _value(store, key_b) == before_b
    assert history.undo_depth == 1
    assert history.undo() is True
    assert _value(store, key_a) == 2.0

    favorite_scope = controller.scope_summary(_scope_view(store))
    assert controller.set_scope_locked(favorite_scope, locked=True) is True
    favorite_scope = controller.scope_summary(_scope_view(store))
    revision = store.revision
    assert controller.randomize(favorite_scope) is False
    assert store.revision == revision
    assert controller.state.notice is not None
    assert "locked" in controller.state.notice
    assert controller.set_scope_locked(favorite_scope, locked=True) is False
    assert store.revision == revision

    assert controller.set_scope_locked(favorite_scope, locked=False) is True
    favorite_scope = controller.scope_summary(_scope_view(store))
    assert controller.set_scope_locked(favorite_scope, locked=False) is False
    assert controller.state.notice is not None
    assert "No parameters" in controller.state.notice


def test_morph_applies_only_scope_as_one_undo_unit() -> None:
    store, key_a, key_b = _store()
    set_parameters_favorite(store, (key_a,), favorite=True)
    _set(store, key_a, 1.0)
    _set(store, key_b, 3.0)
    create_variation(store, "A", created_at=100.0)
    _set(store, key_a, 9.0)
    _set(store, key_b, 7.0)
    create_variation(store, "B", created_at=200.0)
    history = ParamStoreHistory(store)
    controller = VariationController(store, history=history)
    state = controller.state
    state.scope = "favorites"
    state.morph_a = "A"
    state.morph_b = "B"
    state.morph_amount = 0.5

    assert controller.morph(controller.scope_summary(_scope_view(store))) is True
    assert _value(store, key_a) == pytest.approx(5.0)
    assert _value(store, key_b) == pytest.approx(7.0)
    assert history.undo_depth == 1
    assert history.undo() is True
    assert _value(store, key_a) == pytest.approx(9.0)


def test_empty_and_all_locked_scope_commands_are_stable_explicit_noops() -> None:
    store, key_a, _key_b = _store()
    _set(store, key_a, 1.0)
    create_variation(store, "A")
    _set(store, key_a, 9.0)
    create_variation(store, "B")
    history = ParamStoreHistory(store)
    controller = VariationController(store, history=history)
    state = controller.state
    state.morph_a = "A"
    state.morph_b = "B"

    empty_scope = controller.scope_summary(_scope_view(store, query="does-not-exist"))
    revision = store.revision
    assert controller.randomize(empty_scope) is False
    assert state.notice is not None and "No parameters" in state.notice
    assert controller.set_scope_locked(empty_scope, locked=True) is False
    assert state.notice is not None and "No parameters" in state.notice
    assert controller.morph(empty_scope) is False
    assert state.notice is not None and "No parameters" in state.notice
    assert store.revision == revision
    assert history.undo_depth == 0

    filtered_scope = controller.scope_summary(_scope_view(store, query="site-a"))
    set_parameters_locked(store, (key_a,), locked=True)
    history.synchronize()
    locked_scope = controller.scope_summary(_scope_view(store, query="site-a"))
    revision = store.revision
    assert controller.randomize(locked_scope) is False
    assert state.notice is not None and "locked" in state.notice
    assert controller.morph(locked_scope) is False
    assert state.notice is not None and "locked" in state.notice
    assert store.revision == revision
    assert history.undo_depth == 0
    assert filtered_scope.locked_count == 0


def test_thumbnail_preview_callback_success_fallback_and_failure(tmp_path: Path) -> None:
    path = tmp_path / "candidate.png"
    target = object()
    calls: list[tuple[object, Path]] = []
    store, _key_a, _key_b = _store()

    controller = VariationController(
        store,
        thumbnail_preview=lambda surface, image_path: calls.append((surface, image_path)),
    )
    assert controller.preview_thumbnail(target, path) is None
    assert calls == [(target, path)]

    fallback = VariationController(store)
    assert fallback.preview_thumbnail(target, path) == f"Thumbnail: {path}"

    def fail(_target: object, _path: Path) -> None:
        raise RuntimeError("decode failed")

    failure = VariationController(store, thumbnail_preview=fail)
    assert failure.preview_thumbnail(target, path) == ("Thumbnail unavailable: decode failed")
