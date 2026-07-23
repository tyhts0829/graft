from __future__ import annotations

import hashlib
import importlib
import json
import random
from dataclasses import FrozenInstanceError, dataclass
from enum import Enum
from pathlib import Path

import pytest

import grafix.core.capture_provenance as provenance_values
from grafix import G, RenderSession, save
from grafix.core.capture_provenance import (
    FrameProvenance,
    GitProvenance,
    ParameterSnapshotProvenance,
)
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.runtime import ParameterCaptureState
from grafix.runtime_config_loader import runtime_config
from grafix.core.parameters.style import style_key
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.interactive.runtime.source_reload import ReloadedDraw

provenance_module = importlib.import_module("grafix.export.capture_provenance")


def _draw(_t: float):
    return G.line(
        center=(0.0, 0.0, 0.0),
        anchor="left",
        length=10.0,
        angle=0.0,
    )


def test_render_session_snapshots_session_and_frame_provenance_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_calls = 0
    git_calls = 0
    snapshot_source = provenance_module._snapshot_source
    snapshot_git = provenance_module._snapshot_git

    def count_source(draw):
        nonlocal source_calls
        source_calls += 1
        return snapshot_source(draw)

    def count_git(source_path):
        nonlocal git_calls
        git_calls += 1
        return snapshot_git(source_path)

    monkeypatch.setattr(provenance_module, "_snapshot_source", count_source)
    monkeypatch.setattr(provenance_module, "_snapshot_git", count_git)

    with RenderSession(_draw, seed=1847) as session:
        first = session.render(0.0)
        session_source = first.provenance.session.source
        assert session_source.available is True
        assert first.provenance.session.seed == 1847
        assert first.provenance.frame.frame_index == 0
        assert first.provenance.frame.origin == "headless"
        assert first.provenance.frame.quality == "final"

        # Frame/worker publication は source/Git を再探索せず、session snapshot を運ぶ。
        monkeypatch.setattr(
            provenance_module,
            "_snapshot_source",
            lambda _draw: (_ for _ in ()).throw(AssertionError("source rediscovery")),
        )
        monkeypatch.setattr(
            provenance_module,
            "_snapshot_git",
            lambda _path: (_ for _ in ()).throw(AssertionError("git rediscovery")),
        )
        second = session.render(1.0)
        result = save(second, tmp_path / "frame.svg")

    assert source_calls == 1
    assert git_calls == 1
    assert second.provenance.session is first.provenance.session
    assert second.provenance.session.source is session_source
    assert second.provenance.frame.frame_index == 1
    assert result.manifest_path is not None
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert payload["source"]["hash"]["value"] == session_source.sha256
    assert payload["git"]["available"] is first.provenance.session.git.available
    assert payload["seed"] == 1847


def test_interactive_builder_reads_current_load_provenance_for_each_frame() -> None:
    current = ParameterCaptureState("recovery", "session_recovery")
    provider_calls = 0

    def current_parameter_state() -> ParameterCaptureState:
        nonlocal provider_calls
        provider_calls += 1
        return current

    store = ParamStore()
    builder = provenance_module.CaptureProvenanceBuilder(
        _draw,
        config=runtime_config(),
        parameter_state=current_parameter_state,
        parameter_store_path=None,
    )

    recovered = builder.frame(
        store,
        t=0.0,
        frame_index=0,
        quality="final",
        origin="interactive",
    )
    current = ParameterCaptureState("saved", "primary")
    kept = builder.frame(
        store,
        t=1.0,
        frame_index=1,
        quality="final",
        origin="interactive",
    )

    assert recovered.session.parameter_load_provenance == "session_recovery"
    assert kept.session.parameter_load_provenance == "primary"
    assert recovered.session.parameter_source == "recovery"
    assert kept.session.parameter_source == "saved"
    assert recovered.session.source is kept.session.source
    assert recovered.session.git is kept.session.git
    assert provider_calls == 3


def test_interactive_builder_validates_each_provided_load_provenance() -> None:
    current: object = ParameterCaptureState("saved", "primary")

    def current_parameter_state() -> ParameterCaptureState:
        return current  # type: ignore[return-value]

    builder = provenance_module.CaptureProvenanceBuilder(
        _draw,
        config=runtime_config(),
        parameter_state=current_parameter_state,
        parameter_store_path=None,
    )
    current = "legacy"

    with pytest.raises(TypeError, match="ParameterCaptureState"):
        builder.frame(
            ParamStore(),
            t=0.0,
            frame_index=0,
            quality="final",
            origin="interactive",
        )


def test_parameter_snapshot_hash_tracks_effective_frame_values() -> None:
    with RenderSession(_draw) as session:
        first = session.render(0.0)
        key = style_key("background_color")
        meta = session._store.get_meta(key)
        assert meta is not None
        ok, error = update_state_from_ui(
            session._store,
            key,
            (255, 0, 0),
            meta=meta,
        )
        assert ok, error
        second = session.render(1.0)

    first_parameters = first.provenance.frame.parameters
    second_parameters = second.provenance.frame.parameters
    assert first_parameters.sha256 != second_parameters.sha256
    assert second_parameters.revision >= first_parameters.revision
    with pytest.raises(FrozenInstanceError):
        first.provenance.frame.t = 9.0  # type: ignore[misc]


def test_provenance_json_rejects_unknown_duck_typed_and_dataclass_values() -> None:
    @dataclass
    class ArbitraryRecord:
        value: int

    class DuckArray:
        def item(self) -> int:
            return 1

        def tolist(self) -> list[int]:
            return [1]

    class ArbitraryEnum(Enum):
        ITEM = "item"

    for value in (
        object(),
        ArbitraryRecord(1),
        ArbitraryEnum.ITEM,
        DuckArray(),
        {1, 2},
    ):
        with pytest.raises(TypeError):
            provenance_values.canonical_provenance_json(value)


def test_provenance_json_rejects_non_string_keys_and_nonfinite_numbers() -> None:
    with pytest.raises(TypeError, match="keys"):
        provenance_values.canonical_provenance_json({1: "integer", "1": "string"})
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="finite"):
            provenance_values.canonical_provenance_json({"value": value})


def test_parameter_snapshot_is_cached_until_store_or_effective_revision_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_calls = 0
    parameter_snapshot = provenance_module._parameter_snapshot
    length = 10.0

    def changing_draw(_t: float):
        return G.line(
            center=(0.0, 0.0, 0.0),
            anchor="left",
            length=length,
            angle=0.0,
        )

    def count_parameter_snapshot(store):
        nonlocal snapshot_calls
        snapshot_calls += 1
        return parameter_snapshot(store)

    monkeypatch.setattr(
        provenance_module,
        "_parameter_snapshot",
        count_parameter_snapshot,
    )

    with RenderSession(changing_draw) as session:
        # 初回 merge では初期 override policy が確定するため、source が安定する
        # 次 frame を cache 比較の起点にする。
        session.render(0.0)
        first = session.render(1.0)
        stable = session.render(2.0)
        assert stable.provenance.frame.parameters is first.provenance.frame.parameters
        assert snapshot_calls == 2

        store_revision = session._store.revision
        length = 11.0
        effective_changed = session.render(3.0)
        assert session._store.revision == store_revision
        assert snapshot_calls == 3
        assert (
            effective_changed.provenance.frame.parameters.sha256
            != first.provenance.frame.parameters.sha256
        )

        key = style_key("background_color")
        meta = session._store.get_meta(key)
        assert meta is not None
        ok, error = update_state_from_ui(
            session._store,
            key,
            (255, 0, 0),
            meta=meta,
        )
        assert ok, error
        changed = session.render(4.0)

    assert snapshot_calls == 4
    assert changed.provenance.frame.parameters is not first.provenance.frame.parameters
    assert (
        changed.provenance.frame.parameters.sha256
        != first.provenance.frame.parameters.sha256
    )


def test_cached_parameter_snapshot_does_not_rebuild_runtime_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_view_calls = 0
    runtime_view = ParamStore.runtime_view

    def count_runtime_view(store: ParamStore):
        nonlocal runtime_view_calls
        runtime_view_calls += 1
        return runtime_view(store)

    monkeypatch.setattr(ParamStore, "runtime_view", count_runtime_view)

    with RenderSession(_draw) as session:
        # 初回 merge で source policy を確定させ、次の miss から計測する。
        session.render(0.0)
        runtime_view_calls = 0
        first = session.render(1.0)
        stable = session.render(2.0)

    assert stable.provenance.frame.parameters is first.provenance.frame.parameters
    assert runtime_view_calls == 1


def test_git_unavailable_is_explicit_in_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provenance_module,
        "_snapshot_git",
        lambda _path: GitProvenance(
            available=False,
            unavailable_reason="Git executable is unavailable",
        ),
    )

    with RenderSession(_draw) as session:
        frame = session.render(0.0)
        result = save(frame, tmp_path / "frame.svg")

    assert result.manifest_path is not None
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert payload["git"] == {
        "available": False,
        "root": None,
        "commit": None,
        "dirty": None,
        "unavailable_reason": "Git executable is unavailable",
    }
    assert payload["config"]["effective"]
    assert payload["config"]["snapshot_hash"]["algorithm"] == "sha256"
    assert payload["parameters"]["snapshot_hash"]["algorithm"] == "sha256"
    assert payload["output"]["size"] == {"width": 800, "height": 800}


def test_validated_reload_source_bytes_take_priority_over_later_disk_edit(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    validated = b"def draw(t):\n    return ('validated', t)\n"
    source_path.write_bytes(b"def draw(t):\n    return ('later edit', t)\n")

    draw = ReloadedDraw(
        path=source_path,
        source_bytes=validated,
        module_name="_grafix_provenance_test",
        draw_attribute="draw",
        loaded_draw=lambda _t: None,
    )
    source = provenance_module._snapshot_source(draw)

    assert source.path == source_path.resolve()
    assert source.hash_scope == "validated_source_bytes"
    assert source.sha256 == hashlib.sha256(validated).hexdigest()
    assert source.sha256 != hashlib.sha256(source_path.read_bytes()).hexdigest()


def test_seed_rejects_bool_and_non_integer() -> None:
    with pytest.raises(TypeError, match="seed"):
        RenderSession(_draw, seed=True)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="seed"):
        RenderSession(_draw, seed=1.5)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"t": True},
        {"frame_index": 1.5},
        {"quality": "preview"},
        {"origin": "worker"},
    ],
)
def test_frame_provenance_rejects_implicit_or_unknown_fields(
    kwargs: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "t": 0.0,
        "frame_index": 0,
        "quality": "final",
        "origin": "headless",
        "parameters": ParameterSnapshotProvenance(
            revision=0,
            entry_count=0,
            sha256="sha256",
        ),
    }
    values.update(kwargs)

    with pytest.raises((TypeError, ValueError)):
        FrameProvenance(**values)  # type: ignore[arg-type]


def test_git_provenance_rejects_non_bool_flags() -> None:
    with pytest.raises(TypeError):
        GitProvenance(available=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        GitProvenance(available=True, dirty=0)  # type: ignore[arg-type]


def test_frame_seed_override_is_explicit_and_does_not_touch_global_rng(
    tmp_path: Path,
) -> None:
    with RenderSession(_draw, seed=7) as session:
        rng_before = random.getstate()
        inherited = session.render(0.0)
        overridden = session.render(0.0, provenance_seed=42)
        cleared = session.render(0.0, provenance_seed=None)
        rng_after = random.getstate()

        overridden_export = save(
            overridden,
            tmp_path / "overridden.svg",
        )
        cleared_export = save(
            cleared,
            tmp_path / "cleared.svg",
        )

    assert rng_after == rng_before
    assert session.metadata.provenance.seed == 7
    assert inherited.provenance.session is session.metadata.provenance
    assert inherited.provenance.session.seed == 7
    assert overridden.provenance.session.seed == 42
    assert cleared.provenance.session.seed is None
    assert overridden.provenance.session.source is inherited.provenance.session.source
    assert overridden.provenance.session.git is inherited.provenance.session.git
    assert overridden.provenance.session.config is inherited.provenance.session.config
    assert inherited.layers[0].realized is overridden.layers[0].realized

    assert overridden_export.manifest_path is not None
    assert cleared_export.manifest_path is not None
    overridden_manifest = json.loads(overridden_export.manifest_path.read_text())
    cleared_manifest = json.loads(cleared_export.manifest_path.read_text())
    assert overridden_manifest["seed"] == 42
    assert cleared_manifest["seed"] is None


def test_invalid_frame_seed_override_is_rejected_before_draw() -> None:
    calls = 0

    def draw(t: float):
        nonlocal calls
        calls += 1
        return _draw(t)

    with RenderSession(draw) as session:
        with pytest.raises(TypeError, match="provenance_seed"):
            session.render(0.0, provenance_seed=True)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="provenance_seed"):
            session.render(0.0, provenance_seed="bad")  # type: ignore[arg-type]

    assert calls == 0
