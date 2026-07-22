"""reaction_diffusion effect の実体変換に関するテスト群。"""

from __future__ import annotations

import hashlib

import numba
import numpy as np
import pytest

from grafix.api import E, G
from grafix.core.operation_diagnostics import operation_diagnostic_context
from grafix.core.preview_quality import preview_quality_context
from grafix.core.realize import RealizeError, realize


def _circle_ring(radius: float, sides: int) -> np.ndarray:
    angles = np.linspace(
        0.0,
        2.0 * np.pi,
        int(sides),
        endpoint=False,
        dtype=np.float64,
    )
    coords = np.stack(
        [
            float(radius) * np.cos(angles),
            float(radius) * np.sin(angles),
            np.zeros_like(angles),
        ],
        axis=1,
    ).astype(np.float32, copy=False)
    return np.concatenate([coords, coords[:1]], axis=0)


def _kernel_fixture() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    u0 = np.linspace(0.05, 0.95, num=30, dtype=np.float32).reshape(5, 6)
    v0 = np.linspace(0.9, 0.1, num=30, dtype=np.float32).reshape(5, 6)
    mask = np.asarray(
        [
            [0, 1, 1, 0, 2, 0],
            [1, 1, 0, 1, 1, 1],
            [1, 0, 1, 1, 0, 1],
            [2, 1, 1, 0, 1, 1],
            [0, 1, 0, 1, 1, 0],
        ],
        dtype=np.uint8,
    )
    return u0, v0, mask


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"grid_pitch": 0.0}, "grid_pitch"),
        ({"steps": -1}, "steps"),
        ({"du": -0.1}, "du/dv"),
        ({"dv": -0.1}, "du/dv"),
        ({"feed": -0.1}, "feed/kill"),
        ({"kill": -0.1}, "feed/kill"),
        ({"dt": 0.0}, "dt"),
        ({"seed": -1}, "seed"),
        ({"seed_radius": -1.0}, "seed_radius"),
        ({"noise": -0.1}, "noise"),
        ({"level": -0.1}, "level"),
        ({"level": 1.1}, "level"),
        ({"min_points": 0}, "min_points"),
    ],
)
def test_reaction_diffusion_rejects_invalid_ranges_through_public_effect(
    kwargs: dict[str, object],
    match: str,
) -> None:
    empty_mask = G.polygon(activate=False)

    with pytest.raises(RealizeError) as exc_info:
        realize(E.reaction_diffusion(**kwargs)(empty_mask))  # type: ignore[arg-type]
    assert isinstance(exc_info.value.__cause__, ValueError)
    assert match in str(exc_info.value.__cause__)


@pytest.mark.parametrize(
    ("steps", "boundary", "expected_sha256"),
    [
        (1, 0, "223724020648c7ce4e3f6fd4517fa5ea4883e5980969192d85466080eff7dd96"),
        (1, 1, "9e8e133944121a810dbabc3e90f9aad5025d1013823d003a0693fdd3968200a1"),
        (4, 0, "172b2af6b86a95f8aa79089d747bbe9ad8344e25afd3b512d3811f3ce2860ab6"),
        (4, 1, "3434cd5388d64b8e5089906c711ca148189b1a41351c71d906b909622b97c73a"),
    ],
)
def test_reaction_diffusion_kernels_match_frozen_iteration_snapshots(
    steps: int,
    boundary: int,
    expected_sha256: str,
) -> None:
    import grafix.core.effects.reaction_diffusion as module

    u0, v0, mask = _kernel_fixture()
    input_bytes = (u0.tobytes(), v0.tobytes(), mask.tobytes())
    kwargs = {
        "steps": steps,
        "du": 0.16,
        "dv": 0.08,
        "feed": 0.035,
        "kill": 0.062,
        "dt": 1.0,
        "boundary": boundary,
    }

    serial = module._gray_scott_simulate_masked_serial(u0, v0, mask, **kwargs)
    parallel = module._gray_scott_simulate_masked_parallel(u0, v0, mask, **kwargs)

    assert hashlib.sha256(serial.tobytes()).hexdigest() == expected_sha256
    assert parallel.tobytes() == serial.tobytes()
    assert serial.dtype == parallel.dtype == np.float32
    assert serial.shape == parallel.shape == v0.shape
    assert serial.strides == parallel.strides == v0.strides
    assert (u0.tobytes(), v0.tobytes(), mask.tobytes()) == input_bytes


def test_reaction_diffusion_parallel_thread_counts_are_exact_when_available() -> None:
    import grafix.core.effects.reaction_diffusion as module

    u0, v0, mask = _kernel_fixture()
    kwargs = {
        "steps": 9,
        "du": 0.16,
        "dv": 0.08,
        "feed": 0.035,
        "kill": 0.062,
        "dt": 1.0,
        "boundary": 0,
    }
    expected = module._gray_scott_simulate_masked_serial(u0, v0, mask, **kwargs)
    previous_threads = numba.get_num_threads()
    maximum_threads = int(numba.config.NUMBA_NUM_THREADS)
    try:
        for thread_count in (1, 2, 4):
            if thread_count > maximum_threads:
                continue
            numba.set_num_threads(thread_count)
            actual = module._gray_scott_simulate_masked_parallel(
                u0,
                v0,
                mask,
                **kwargs,
            )
            assert actual.tobytes() == expected.tobytes()
    finally:
        numba.set_num_threads(previous_threads)


def test_reaction_diffusion_kernel_dispatch_uses_safe_crossover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import grafix.core.effects.reaction_diffusion as module

    calls: list[str] = []

    def serial(*_args: object, **_kwargs: object) -> np.ndarray:
        calls.append("serial")
        return np.zeros((1, 1), dtype=np.float32)

    def parallel(*_args: object, **_kwargs: object) -> np.ndarray:
        calls.append("parallel")
        return np.ones((1, 1), dtype=np.float32)

    monkeypatch.setattr(module, "get_num_threads", lambda: 4)
    monkeypatch.setattr(module, "_gray_scott_simulate_masked_serial", serial)
    monkeypatch.setattr(module, "_gray_scott_simulate_masked_parallel", parallel)
    kwargs = {
        "du": 0.16,
        "dv": 0.08,
        "feed": 0.035,
        "kill": 0.062,
        "dt": 1.0,
        "boundary": 0,
    }
    large = np.zeros((256, 256), dtype=np.float32)
    mask = np.ones_like(large, dtype=np.uint8)

    module._gray_scott_simulate_masked(large, large, mask, steps=8, **kwargs)
    module._gray_scott_simulate_masked(large, large, mask, steps=7, **kwargs)
    nonfinite = large.copy()
    nonfinite[0, 0] = np.nan
    module._gray_scott_simulate_masked(nonfinite, large, mask, steps=8, **kwargs)

    assert calls == ["parallel", "serial", "serial"]


def test_reaction_diffusion_contour_returns_closed_loops() -> None:
    mask = G.polygon(n_sides=64, scale=50.0)
    out = realize(
        E.reaction_diffusion(
            grid_pitch=2.0,
            steps=0,
            seed=0,
            seed_radius=12.0,
            noise=0.0,
            level=0.5,
            min_points=4,
        )(mask)
    )
    assert out.coords.shape[0] > 0
    assert out.offsets.size >= 2
    for i in range(int(out.offsets.size) - 1):
        s = int(out.offsets[i])
        e = int(out.offsets[i + 1])
        ring = out.coords[s:e]
        assert ring.shape[0] >= 4
        np.testing.assert_allclose(ring[0], ring[-1], rtol=0.0, atol=1e-6)


def test_reaction_diffusion_activate_false_is_noop() -> None:
    mask = G.polygon(n_sides=8, scale=30.0)
    out = realize(E.reaction_diffusion(activate=False)(mask))
    expected = realize(mask)
    np.testing.assert_allclose(out.coords, expected.coords, rtol=0.0, atol=1e-6)
    assert out.offsets.tolist() == expected.offsets.tolist()


def test_reaction_diffusion_empty_mask_returns_empty() -> None:
    empty_mask = G.polygon(activate=False)
    out = realize(
        E.reaction_diffusion(
            grid_pitch=2.0,
            steps=0,
            noise=0.0,
            seed_radius=10.0,
            level=0.5,
            min_points=4,
        )(empty_mask)
    )
    assert out.coords.shape == (0, 3)
    assert out.offsets.tolist() == [0]


def test_reaction_diffusion_is_deterministic_for_fixed_seed() -> None:
    mask = G.polygon(n_sides=24, scale=24.0)
    effect = E.reaction_diffusion(
        grid_pitch=1.5,
        steps=4,
        seed=37,
        seed_radius=4.0,
        noise=0.03,
        level=0.5,
        min_points=4,
    )

    first = realize(effect(mask))
    second = realize(effect(mask))

    np.testing.assert_array_equal(first.coords, second.coords)
    np.testing.assert_array_equal(first.offsets, second.offsets)


def test_reaction_diffusion_draft_caps_steps_and_reports_effective_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import grafix.core.effects.reaction_diffusion as module

    seen_steps: list[int] = []

    def simulate(
        u0: np.ndarray,
        _v0: np.ndarray,
        _mask: np.ndarray,
        *,
        steps: int,
        **_kwargs: object,
    ) -> np.ndarray:
        seen_steps.append(int(steps))
        return np.zeros_like(u0)

    monkeypatch.setattr(module, "_gray_scott_simulate_masked", simulate)
    mask = G.polygon(n_sides=4, scale=10.0)
    with operation_diagnostic_context() as diagnostics:
        with preview_quality_context("draft"):
            realize(
                E.reaction_diffusion(
                    grid_pitch=2.0,
                    steps=5000,
                    seed_radius=0.0,
                    noise=0.0,
                    min_points=4,
                )(mask)
            )

    assert seen_steps == [600]
    assert any(
        item.op == "reaction_diffusion.steps"
        and item.original_value == 5000
        and item.effective_value == 600
        for item in diagnostics.snapshot()
    )


def test_reaction_diffusion_draft_bounds_point_step_work_and_keeps_final_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import grafix.core.effects.reaction_diffusion as module

    seen_work: list[tuple[int, int]] = []

    def simulate(
        u0: np.ndarray,
        _v0: np.ndarray,
        _mask: np.ndarray,
        *,
        steps: int,
        **_kwargs: object,
    ) -> np.ndarray:
        seen_work.append((int(u0.size), int(steps)))
        return np.zeros_like(u0)

    monkeypatch.setattr(module, "_gray_scott_simulate_masked", simulate)
    realized_mask = realize(G.polygon(n_sides=4, scale=400.0))
    mask = (realized_mask.coords, realized_mask.offsets)
    kwargs = {
        "grid_pitch": 1.0,
        "steps": 5000,
        "seed_radius": 0.0,
        "noise": 0.0,
        "min_points": 4,
    }

    with operation_diagnostic_context() as diagnostics:
        with preview_quality_context("draft"):
            module.reaction_diffusion(mask, **kwargs)
    with preview_quality_context("final"):
        module.reaction_diffusion(mask, **kwargs)

    draft_points, draft_steps = seen_work[0]
    final_points, final_steps = seen_work[1]
    assert draft_points * draft_steps <= module.DRAFT_MAX_POINT_STEPS
    assert draft_points < final_points
    assert final_steps == 5000
    assert any(
        item.op == "reaction_diffusion.grid_pitch"
        and item.original_value == 1.0
        and float(item.effective_value) > 1.0
        for item in diagnostics.snapshot()
    )


def test_reaction_diffusion_draft_with_center_hole_is_nonempty_and_deterministic() -> None:
    import grafix.core.effects.reaction_diffusion as module

    outer = _circle_ring(50.0, 128)
    hole = _circle_ring(20.0, 64)
    coords = np.concatenate([outer, hole], axis=0)
    offsets = np.asarray(
        [0, outer.shape[0], outer.shape[0] + hole.shape[0]],
        dtype=np.int32,
    )
    kwargs = {
        "grid_pitch": 1.0,
        "steps": 4500,
        "seed": 0,
        "seed_radius": 10.0,
        "noise": 0.02,
        "level": 0.2,
        "min_points": 4,
    }

    with preview_quality_context("draft"):
        first = module.reaction_diffusion((coords, offsets), **kwargs)
        second = module.reaction_diffusion((coords, offsets), **kwargs)

    assert first[0].shape[0] > 0
    np.testing.assert_array_equal(first[0], second[0])
    np.testing.assert_array_equal(first[1], second[1])
