from __future__ import annotations

from dataclasses import replace

import pytest

from grafix import G, RuntimeLimitProfiles, RuntimeLimits
from grafix.core.layer import LayerStyleDefaults
from grafix.core.pipeline import realize_scene
from grafix.core.realize import RealizeSession
from grafix.core.resource_budget import ResourceBudget, ResourceLimitError
from grafix.runtime_config_loader import runtime_config


_TEST_RUNTIME_CONFIG = replace(runtime_config(), font_dirs=())


def _budget(*, vertices: int) -> ResourceBudget:
    return ResourceBudget(
        max_output_vertices=vertices,
        max_output_lines=100,
        max_output_bytes=1_000_000,
    )


def _defaults() -> LayerStyleDefaults:
    return LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)


def test_runtime_limits_are_immutable_and_validate_integral_limits() -> None:
    limits = RuntimeLimits(
        cpu_cache_bytes=123,
        cpu_cache_entries=7,
        capture_queue_pending_jobs=2,
    )

    assert limits.cpu_cache_bytes == 123
    assert limits.cpu_cache_entries == 7
    assert limits.capture_queue_pending_jobs == 2
    with pytest.raises(AttributeError):
        limits.cpu_cache_bytes = 1  # type: ignore[misc]
    with pytest.raises(TypeError, match="cpu_cache_bytes"):
        RuntimeLimits(cpu_cache_bytes=True)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="cpu_cache_entries"):
        RuntimeLimits(cpu_cache_entries=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cpu_cache_entries"):
        RuntimeLimits(cpu_cache_entries=-1)
    with pytest.raises(ValueError, match="gpu_cache_bytes"):
        RuntimeLimits(gpu_cache_bytes=-1)


def test_runtime_limits_default_cpu_cache_entry_bound_is_4096() -> None:
    limits = RuntimeLimits()

    assert limits.cpu_cache_entries == 4096
    with RealizeSession() as session:
        assert session.runtime_limits.cpu_cache_entries == 4096


def test_runtime_limit_profiles_keep_preview_and_final_independent() -> None:
    preview = RuntimeLimits(scene=_budget(vertices=3))
    final = RuntimeLimits(scene=_budget(vertices=30))
    profiles = RuntimeLimitProfiles(preview=preview, final=final)

    assert profiles.for_quality("draft") is preview
    assert profiles.for_quality("final") is final
    with pytest.raises(ValueError, match="quality"):
        profiles.for_quality("fast")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("scene_budget", "message"),
    [
        (_budget(vertices=7), "vertices=8"),
        (
            ResourceBudget(
                max_output_vertices=100,
                max_output_lines=1,
                max_output_bytes=1_000_000,
            ),
            "lines=2",
        ),
        (
            ResourceBudget(
                max_output_vertices=100,
                max_output_lines=100,
                max_output_bytes=111,
            ),
            "estimated_bytes=112",
        ),
    ],
)
def test_scene_aggregate_is_rejected_before_new_results_enter_cpu_cache(
    scene_budget: ResourceBudget,
    message: str,
) -> None:
    # triangle は閉点を含めて 4 vertices。各 operation は上限内だが、2 layers は8。
    first = G.polygon(n_sides=3, key="scene-limit-first")
    second = G.polygon(n_sides=3, key="scene-limit-second")
    limits = RuntimeLimits(
        per_operation=_budget(vertices=10),
        scene=scene_budget,
        cpu_cache_bytes=1_000_000,
    )

    with RealizeSession(runtime_limits=limits) as session:
        with pytest.raises(ResourceLimitError, match=message):
                realize_scene(
                    lambda _t: [first, second],
                    0.0,
                    _defaults(),
                    config=_TEST_RUNTIME_CONFIG,
                    session=session,
            )
        stats = session.stats()

    assert stats.entries == 0
    assert stats.bytes == 0


def test_scene_aggregate_within_limit_commits_cache_transaction() -> None:
    geometry = G.polygon(n_sides=3, key="scene-limit-ok")
    limits = RuntimeLimits(
        per_operation=_budget(vertices=10),
        scene=_budget(vertices=4),
        cpu_cache_bytes=1_000_000,
    )

    with RealizeSession(runtime_limits=limits) as session:
        layers = realize_scene(
            lambda _t: geometry,
            0.0,
            _defaults(),
            config=_TEST_RUNTIME_CONFIG,
            session=session,
        )
        stats = session.stats()

    assert len(layers) == 1
    assert stats.entries == 1
    assert stats.bytes == layers[0].realized.byte_size


def test_runtime_limits_configure_per_operation_and_cpu_cache() -> None:
    geometry = G.polygon(n_sides=3, key="runtime-limit-operation")
    too_small = RuntimeLimits(
        per_operation=_budget(vertices=3),
        scene=_budget(vertices=100),
    )
    with RealizeSession(runtime_limits=too_small) as session:
        with pytest.raises(Exception) as exc_info:
            session.realize(geometry)
    assert any(
        isinstance(item, ResourceLimitError)
        for item in _cause_chain(exc_info.value)
    )

    no_cache = RuntimeLimits(
        per_operation=_budget(vertices=10),
        scene=_budget(vertices=100),
        cpu_cache_bytes=0,
    )
    with RealizeSession(runtime_limits=no_cache) as session:
        session.realize(geometry)
        assert session.stats().entries == 0


def test_runtime_limits_configure_cpu_cache_entry_bound() -> None:
    limits = RuntimeLimits(
        per_operation=_budget(vertices=10),
        scene=_budget(vertices=100),
        cpu_cache_bytes=1_000_000,
        cpu_cache_entries=1,
    )
    geometries = [
        G.polygon(
            n_sides=3 + index,
            key=f"runtime-entry-limit-{index}",
        )
        for index in range(2)
    ]

    with RealizeSession(runtime_limits=limits) as session:
        for geometry in geometries:
            session.realize(geometry)
        stats = session.stats()

        assert session.runtime_limits is limits
        assert session.runtime_limits.cpu_cache_entries == 1

    assert stats.entries == 1
    assert stats.evictions == 1


def _cause_chain(error: BaseException) -> tuple[BaseException, ...]:
    out: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in out:
        out.append(current)
        current = current.__cause__
    return tuple(out)
