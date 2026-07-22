from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import numpy as np
import pytest

from grafix.api import E, G
from grafix.core.evaluation_config import EvaluationConfig
from grafix.core.evaluation_context import EvaluationContext, EvaluationResources
from grafix.core.font_resolver import resolve_font_path
from grafix.core.font_resources import FontResources, ResolvedFontLease, TextRenderer
from grafix.core.operation_catalog import current_operation_catalog
from grafix.core.operation_declaration import operation_declaration
from grafix.core.primitives.text import text
from grafix.core.realize import RealizeCacheStore, RealizeSession
from grafix.core.runtime_config import RuntimeConfig
from grafix.runtime_config_loader import load_runtime_config
from grafix.core.runtime_limits import DEFAULT_FINAL_RUNTIME_LIMITS


def _config_with_font_dirs(tmp_path: Path, *font_dirs: Path) -> RuntimeConfig:
    cfg_path = tmp_path / f"config-{len(tuple(tmp_path.iterdir()))}.yaml"
    rows = ["version: 1", "paths:", '  output_dir: "data/output"', "  font_dirs:"]
    rows.extend(f'    - "{directory}"' for directory in font_dirs)
    cfg_path.write_text("\n".join((*rows, "")), encoding="utf-8")
    return load_runtime_config(cfg_path)


@contextmanager
def _realize_session(
    config: RuntimeConfig,
) -> Iterator[tuple[RealizeSession, EvaluationResources]]:
    resources = EvaluationResources()
    store = RealizeCacheStore.from_runtime_limits(DEFAULT_FINAL_RUNTIME_LIMITS)
    context = EvaluationContext(
        catalog=current_operation_catalog(),
        quality="final",
        config=EvaluationConfig(font_dirs=config.font_dirs),
    )
    try:
        with RealizeSession(
            context=context,
            resources=resources,
            cache_store=store,
        ) as session:
            yield session, resources
    finally:
        resources.close()
        store.close()


def _packaged_fonts() -> tuple[Path, Path]:
    config = load_runtime_config()
    evaluation_config = EvaluationConfig(font_dirs=config.font_dirs)
    return (
        resolve_font_path("GoogleSans-Regular.ttf", config=evaluation_config),
        resolve_font_path("NotoSansJP-Regular.ttf", config=evaluation_config),
    )


def test_text_declaration_exposes_one_external_font_dependency_hook() -> None:
    declaration = operation_declaration(text)

    assert callable(declaration.external_dependency_hook)
    assert declaration.evaluation_spec.external_dependency_hook is (
        declaration.external_dependency_hook
    )


def test_preflight_and_evaluator_share_exact_lease_and_warm_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regular, _other = _packaged_fonts()
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    font_path = font_dir / "Session.ttf"
    font_path.write_bytes(regular.read_bytes())
    config = _config_with_font_dirs(tmp_path, font_dir)
    geometry = G.text(text="LEASE", font="Session.ttf", scale=10.0)
    resolved: list[ResolvedFontLease] = []
    evaluated: list[ResolvedFontLease] = []
    original_resolve = FontResources.resolve
    original_get_font = TextRenderer.get_font
    original_open = Path.open
    asset_opens: list[Path] = []

    def recording_resolve(self, font, face_index, *, config):
        lease = original_resolve(self, font, face_index, config=config)
        resolved.append(lease)
        return lease

    def recording_get_font(self, lease):
        evaluated.append(lease)
        return original_get_font(self, lease)

    def recording_open(self, *args, **kwargs):
        if self == font_path:
            asset_opens.append(self)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(FontResources, "resolve", recording_resolve)
    monkeypatch.setattr(TextRenderer, "get_font", recording_get_font)
    monkeypatch.setattr(Path, "open", recording_open)

    with _realize_session(config) as (session, _resources):
        first, first_key = session.realize_with_key(geometry)
        after_first = session.stats()
        second, second_key = session.realize_with_key(geometry)
        after_second = session.stats()

    assert len(resolved) == 2
    assert resolved[1] is resolved[0]
    assert evaluated
    assert all(lease is resolved[0] for lease in evaluated)
    assert asset_opens == [font_path]
    assert first is second
    assert first_key == second_key
    assert after_second.hits > after_first.hits


def test_same_session_observes_font_replacement_in_key_and_output(tmp_path: Path) -> None:
    regular, other = _packaged_fonts()
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    font_path = font_dir / "Mutable.ttf"
    font_path.write_bytes(regular.read_bytes())
    config = _config_with_font_dirs(tmp_path, font_dir)
    geometry = E.translate(delta=(1.0, 2.0, 0.0))(G.text(text="A", font="Mutable.ttf", scale=100.0))

    with _realize_session(config) as (session, _resources):
        first, first_key = session.realize_with_key(geometry)
        warm, warm_key = session.realize_with_key(geometry)
        replacement = font_dir / "replacement.tmp"
        replacement.write_bytes(other.read_bytes())
        os.replace(replacement, font_path)
        changed, changed_key = session.realize_with_key(geometry)

    assert warm is first
    assert warm_key == first_key
    assert changed_key.external_dependencies != first_key.external_dependencies
    assert changed is not first
    assert not np.array_equal(changed.coords, first.coords)


def test_priority_font_appearance_and_disappearance_are_observed(
    tmp_path: Path,
) -> None:
    regular, other = _packaged_fonts()
    preferred = tmp_path / "preferred"
    fallback = tmp_path / "fallback"
    preferred.mkdir()
    fallback.mkdir()
    fallback_path = fallback / "SwitchableFallback.ttf"
    preferred_path = preferred / "SwitchablePreferred.ttf"
    fallback_path.write_bytes(regular.read_bytes())
    config = _config_with_font_dirs(tmp_path, preferred, fallback)
    geometry = G.text(text="A", font="Switchable", scale=100.0)

    with _realize_session(config) as (session, _resources):
        fallback_result, fallback_key = session.realize_with_key(geometry)
        preferred_path.write_bytes(other.read_bytes())
        preferred_result, preferred_key = session.realize_with_key(geometry)
        preferred_path.unlink()
        restored_result, restored_key = session.realize_with_key(geometry)

    assert preferred_key.external_dependencies != fallback_key.external_dependencies
    assert not np.array_equal(preferred_result.coords, fallback_result.coords)
    assert restored_key == fallback_key
    assert restored_result is fallback_result


def test_two_configs_with_same_font_name_are_isolated(tmp_path: Path) -> None:
    regular, other = _packaged_fonts()
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / "Shared.ttf").write_bytes(regular.read_bytes())
    (dir_b / "Shared.ttf").write_bytes(other.read_bytes())
    config_a = _config_with_font_dirs(tmp_path, dir_a)
    config_b = _config_with_font_dirs(tmp_path, dir_b)
    geometry = G.text(text="A", font="Shared.ttf", scale=100.0)

    with _realize_session(config_a) as (session_a, _resources_a):
        result_a, key_a = session_a.realize_with_key(geometry)
    with _realize_session(config_b) as (session_b, _resources_b):
        result_b, key_b = session_b.realize_with_key(geometry)

    assert key_a.evaluation != key_b.evaluation
    assert key_a.external_dependencies != key_b.external_dependencies
    assert not np.array_equal(result_a.coords, result_b.coords)


def test_repeated_resource_owner_close_releases_all_font_state(tmp_path: Path) -> None:
    regular, _other = _packaged_fonts()
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    (font_dir / "Repeated.ttf").write_bytes(regular.read_bytes())
    config = _config_with_font_dirs(tmp_path, font_dir)
    geometry = G.text(text="ABC", font="Repeated.ttf")

    closed_fonts: list[FontResources] = []
    for _ in range(5):
        with _realize_session(config) as (session, resources):
            session.realize(geometry)
            fonts = resources.fonts
            assert fonts.stats().fonts == 1
            closed_fonts.append(fonts)

    assert all(fonts.closed for fonts in closed_fonts)
    assert all(fonts.stats().fonts == 0 for fonts in closed_fonts)
    assert all(fonts.stats().glyph_commands == 0 for fonts in closed_fonts)
    assert all(fonts.stats().glyph_polylines == 0 for fonts in closed_fonts)


def test_inactive_text_does_not_resolve_unused_font(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config_with_font_dirs(tmp_path, tmp_path / "missing-fonts")
    geometry = G.text(
        activate=False,
        text="A",
        font="DefinitelyMissing.ttf",
    )

    def unexpected(*_args, **_kwargs):
        raise AssertionError("inactive text must not resolve a font")

    monkeypatch.setattr(FontResources, "resolve", unexpected)
    with _realize_session(config) as (session, _resources):
        result = session.realize(geometry)

    assert result.coords.shape == (0, 3)
    assert result.offsets.tolist() == [0]


def test_evaluation_resources_clear_and_close_delegate_to_font_owner(
    tmp_path: Path,
) -> None:
    regular, _other = _packaged_fonts()
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    (font_dir / "Owned.ttf").write_bytes(regular.read_bytes())
    config = _config_with_font_dirs(tmp_path, font_dir)
    resources = EvaluationResources()
    fonts = resources.fonts
    lease = fonts.resolve(
        "Owned.ttf",
        0,
        config=EvaluationConfig(font_dirs=config.font_dirs),
    )
    fonts.renderer.get_font(lease)

    resources.clear()
    assert fonts.stats().assets == 0
    assert fonts.stats().fonts == 0

    resources.close()
    resources.close()
    assert fonts.closed
    with pytest.raises(RuntimeError, match="close"):
        _ = resources.fonts
