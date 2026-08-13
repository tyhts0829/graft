"""
Purpose:
    seed付きGaussian地形fieldから、open・closedの等高線群を新規生成する。
Use when:
    既存maskのisocontourではなく、procedural terrainの焦点・level・warpを扱う場合。
Constraints:
    - 同じseedと引数から同じfieldを作り、focus_countを増やしても既存focusのprefixを保つ。
    - Marching Squaresの決定的なpath順とopen/closed状態を維持する。
    - grid、focus評価、level抽出、scratch、最大出力を配列確保前にbudget検査する。
"""

from __future__ import annotations

import math

import numpy as np

from grafix.core.geometry_kernels.grid import DEFAULT_MAX_GRID_POINTS
from grafix.core.geometry_kernels.marching import marching_squares_paths
from grafix.core.geometry_kernels.packed import empty_packed_geometry, pack_polylines
from grafix.core.operation_authoring import primitive
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple
from grafix.core.resource_budget import (
    ResourceLimitError,
    ensure_geometry_output,
)

_DEFAULT_SEED = 271
_MAX_FOCUS_COUNT = 4_096
_MAX_FIELD_POINT_FOCI = 64_000_000
_MAX_MARCHING_CELL_LEVELS = 64_000_000
_MAX_LEVEL_COUNT = 4_096
_FLOAT32_MAX = float(np.finfo(np.float32).max)

# Fault Gardenで採用した非線形なlevel間隔をdimensionless profileとして保持する。
_CONTOUR_LEVEL_TEMPLATE = (
    0.065,
    0.095,
    0.135,
    0.185,
    0.250,
    0.330,
    0.425,
    0.535,
    0.660,
    0.800,
    0.940,
    1.080,
    1.220,
)

# (x, y, strength, sigma_x, sigma_y)。位置とsigmaは0..1 domain基準。
_REFERENCE_FOCI = np.asarray(
    (
        (0.2516, 0.2954, 1.00, 0.2055, 0.2470),
        (0.7400, 0.5325, 0.96, 0.1040, 0.1502),
        (0.8367, 0.1531, 0.56, 0.0556, 0.0632),
        (0.0945, 0.0978, 0.48, 0.0556, 0.0632),
        (0.8416, 0.8486, 0.43, 0.0701, 0.0731),
        (0.6264, 0.8882, 0.34, 0.0653, 0.0553),
    ),
    dtype=np.float64,
)


topographic_contours_meta = {
    "width": ParamMeta(
        kind="float",
        ui_min=1.0,
        ui_max=300.0,
        display_name="Width",
        description="等高線fieldを生成する矩形領域の幅を指定します。",
        unit="mm",
        step=1.0,
        category="Layout",
    ),
    "height": ParamMeta(
        kind="float",
        ui_min=1.0,
        ui_max=300.0,
        display_name="Height",
        description="等高線fieldを生成する矩形領域の高さを指定します。",
        unit="mm",
        step=1.0,
        category="Layout",
    ),
    "seed": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=1_000_000,
        display_name="Terrain Seed",
        description="焦点の位置、強度、広がりのvariationを決めるseedです。",
        step=1.0,
        category="Terrain Structure",
    ),
    "focus_count": ParamMeta(
        kind="int",
        ui_min=1,
        ui_max=24,
        display_name="Focus Count",
        description="地形fieldを構成するGaussian焦点の数を指定します。",
        step=1.0,
        category="Terrain Structure",
        recommended_range=(3.0, 12.0),
    ),
    "focus_spread": ParamMeta(
        kind="float",
        ui_min=0.25,
        ui_max=2.5,
        display_name="Focus Spread",
        description="すべてのGaussian焦点の広がりへ掛ける倍率です。",
        step=0.05,
        format="%.2f",
        category="Terrain Structure",
        recommended_range=(0.65, 1.55),
    ),
    "level_count": ParamMeta(
        kind="int",
        ui_min=1,
        ui_max=40,
        display_name="Contour Levels",
        description="scalar fieldから抽出する等高線levelの数を指定します。",
        step=1.0,
        category="Terrain Structure",
        recommended_range=(8.0, 18.0),
    ),
    "field_warp": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=3.0,
        display_name="Field Warp",
        description="等高線fieldへ加える周期的な細部の強さを指定します。",
        step=0.05,
        format="%.2f",
        category="Terrain Detail",
        recommended_range=(0.25, 1.75),
    ),
    "warp_frequency": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=3.0,
        display_name="Warp Frequency",
        description="等高線fieldへ加える周期的な細部の周波数倍率です。",
        step=0.05,
        format="%.2f",
        category="Terrain Detail",
        recommended_range=(0.5, 1.75),
    ),
    "phase": ParamMeta(
        kind="float",
        ui_min=-360.0,
        ui_max=360.0,
        display_name="Warp Phase",
        description="周期的なfield warpの位相をdegree単位で指定します。",
        unit="deg",
        step=1.0,
        format="%.1f",
        category="Terrain Detail",
    ),
    "grid_pitch": ParamMeta(
        kind="float",
        ui_min=0.1,
        ui_max=10.0,
        display_name="Grid Pitch",
        description="等高線抽出に使うsampling gridの目標間隔を指定します。",
        unit="mm",
        step=0.05,
        format="%.2f",
        category="Sampling",
        advanced=True,
        recommended_range=(0.35, 2.0),
    ),
    "center": ParamMeta(
        kind="vec3",
        ui_min=-300.0,
        ui_max=300.0,
        display_name="Center",
        description="等高線fieldの中心となるXYZ座標を指定します。",
        unit="mm",
        category="Layout",
    ),
}


def _positive_finite(value: float, *, name: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"topographic_contours: {name} は正の有限値である必要がある")
    return normalized


def _nonnegative_finite(value: float, *, name: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        raise ValueError(f"topographic_contours: {name} は0以上の有限値である必要がある")
    return normalized


def _positive_int(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(f"topographic_contours: {name} はintである必要がある")
    normalized = int(value)
    if normalized < 1:
        raise ValueError(f"topographic_contours: {name} は1以上である必要がある")
    return normalized


def _seed_value(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError("topographic_contours: seed はintである必要がある")
    normalized = int(value)
    if normalized < 0:
        raise ValueError("topographic_contours: seed は0以上である必要がある")
    return normalized


def _contour_levels(count: int) -> tuple[float, ...]:
    if count == len(_CONTOUR_LEVEL_TEMPLATE):
        return _CONTOUR_LEVEL_TEMPLATE
    positions = np.linspace(0.0, len(_CONTOUR_LEVEL_TEMPLATE) - 1, count)
    sampled = np.interp(
        positions,
        np.arange(len(_CONTOUR_LEVEL_TEMPLATE)),
        _CONTOUR_LEVEL_TEMPLATE,
    )
    return tuple(float(value) for value in sampled)


def _focuses(seed: int, count: int) -> np.ndarray:
    """既定seedを基準形とする、prefix-stableな焦点列を返す。"""

    values = np.random.default_rng(seed).random((count, 5))
    reference = np.random.default_rng(_DEFAULT_SEED).random((count, 5))
    output = np.empty((count, 5), dtype=np.float64)

    anchored = min(count, int(_REFERENCE_FOCI.shape[0]))
    if anchored:
        base = _REFERENCE_FOCI[:anchored]
        delta = values[:anchored] - reference[:anchored]
        output[:anchored, 0:2] = np.clip(
            base[:, 0:2] + 0.10 * delta[:, 0:2],
            0.04,
            0.96,
        )
        output[:anchored, 2] = base[:, 2] * (1.0 + 0.30 * delta[:, 2])
        output[:anchored, 3:5] = base[:, 3:5] * (
            1.0 + 0.35 * delta[:, 3:5]
        )

    if count > anchored:
        extra = values[anchored:]
        output[anchored:, 0:2] = 0.08 + 0.84 * extra[:, 0:2]
        output[anchored:, 2] = 0.28 + 0.52 * extra[:, 2]
        output[anchored:, 3:5] = 0.045 + 0.115 * extra[:, 3:5]
    return output


def _grid_shape(width: float, height: float, pitch: float) -> tuple[int, int]:
    ratio_x = width / pitch
    ratio_y = height / pitch
    if not math.isfinite(ratio_x) or not math.isfinite(ratio_y):
        raise ResourceLimitError(
            "topographic_contours: sampling gridの大きさが有限範囲を超えている"
        )
    nx = max(2, int(math.ceil(ratio_x)) + 1)
    ny = max(2, int(math.ceil(ratio_y)) + 1)
    point_count = nx * ny
    if point_count > DEFAULT_MAX_GRID_POINTS:
        raise ResourceLimitError(
            "topographic_contours: sampling gridが上限を超えるため配列を確保しません: "
            f"points={point_count:,} > {DEFAULT_MAX_GRID_POINTS:,}; "
            "grid_pitchを大きくしてください"
        )
    return nx, ny


def _field(
    *,
    nx: int,
    ny: int,
    focuses: np.ndarray,
    focus_spread: float,
    field_warp: float,
    warp_frequency: float,
    phase: float,
) -> np.ndarray:
    u = np.linspace(0.0, 1.0, nx, dtype=np.float64)
    v = np.linspace(0.0, 1.0, ny, dtype=np.float64)
    values = np.zeros((ny, nx), dtype=np.float64)

    for cx, cy, strength, sigma_x, sigma_y in focuses:
        dx2 = ((u - cx) / (sigma_x * focus_spread)) ** 2
        dy2 = ((v - cy) / (sigma_y * focus_spread)) ** 2
        values += strength * np.exp(-0.5 * (dy2[:, None] + dx2[None, :]))

    if field_warp != 0.0 and warp_frequency != 0.0:
        frequency = warp_frequency
        phase_radians = math.radians(math.remainder(phase, 360.0))
        primary_x = np.sin(24.0 * frequency * u + phase_radians)
        primary_y = np.cos(20.8 * frequency * v - phase_radians)
        values += 0.026 * field_warp * primary_y[:, None] * primary_x[None, :]
        values += 0.018 * field_warp * np.sin(
            15.7 * frequency * v[:, None] + 9.5 * frequency * u[None, :]
        )
    return values


@primitive(meta=topographic_contours_meta)
def topographic_contours(
    *,
    width: float = 80.0,
    height: float = 100.0,
    seed: int = _DEFAULT_SEED,
    focus_count: int = 6,
    focus_spread: float = 1.0,
    level_count: int = 13,
    field_warp: float = 1.0,
    warp_frequency: float = 1.0,
    phase: float = 0.0,
    grid_pitch: float = 1.0,
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> GeomTuple:
    """複数焦点のscalar fieldから地形等高線を生成する。

    Parameters
    ----------
    width, height : float, optional
        等高線を生成する矩形領域の幅と高さ。
    seed : int, optional
        焦点layoutの決定的variationを制御する非負整数。
    focus_count : int, optional
        Gaussian焦点の数。
    focus_spread : float, optional
        全焦点の広がりへ掛ける倍率。
    level_count : int, optional
        抽出する等高線levelの数。
    field_warp : float, optional
        fieldへ加える周期的な細部の強さ。
    warp_frequency : float, optional
        周期的な細部の周波数倍率。
    phase : float, optional
        field warpの位相 [deg]。
    grid_pitch : float, optional
        sampling gridの目標間隔。
    center : tuple[float, float, float], optional
        出力矩形の中心 `(x, y, z)`。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        open contourとclosed contourを連続polylineとして詰めたGeometry。
    """

    width_f = _positive_finite(width, name="width")
    height_f = _positive_finite(height, name="height")
    seed_i = _seed_value(seed)
    focus_count_i = _positive_int(focus_count, name="focus_count")
    if focus_count_i > _MAX_FOCUS_COUNT:
        raise ResourceLimitError(
            "topographic_contours: focus_countが処理上限を超えています: "
            f"{focus_count_i:,} > {_MAX_FOCUS_COUNT:,}"
        )
    spread_f = _positive_finite(focus_spread, name="focus_spread")
    level_count_i = _positive_int(level_count, name="level_count")
    if level_count_i > _MAX_LEVEL_COUNT:
        raise ResourceLimitError(
            "topographic_contours: level_countが処理上限を超えています: "
            f"{level_count_i:,} > {_MAX_LEVEL_COUNT:,}"
        )
    warp_f = _nonnegative_finite(field_warp, name="field_warp")
    frequency_f = _nonnegative_finite(warp_frequency, name="warp_frequency")
    phase_f = float(phase)
    if not math.isfinite(phase_f):
        raise ValueError("topographic_contours: phase は有限値である必要がある")
    pitch_f = _positive_finite(grid_pitch, name="grid_pitch")

    cx, cy, cz = (float(component) for component in center)
    if not all(math.isfinite(value) for value in (cx, cy, cz)):
        raise ValueError("topographic_contours: center は有限値である必要がある")
    bounds = (
        cx - 0.5 * width_f,
        cx + 0.5 * width_f,
        cy - 0.5 * height_f,
        cy + 0.5 * height_f,
        cz,
    )
    if any(not math.isfinite(value) or abs(value) > _FLOAT32_MAX for value in bounds):
        raise ValueError(
            "topographic_contours: 出力boundsはfloat32の有限範囲内である必要がある"
        )

    nx, ny = _grid_shape(width_f, height_f, pitch_f)
    grid_points = nx * ny
    field_work = grid_points * focus_count_i
    if field_work > _MAX_FIELD_POINT_FOCI:
        raise ResourceLimitError(
            "topographic_contours: field評価量が上限を超えるため処理しません: "
            f"grid_points × focus_count={field_work:,} > "
            f"{_MAX_FIELD_POINT_FOCI:,}; grid_pitchまたはfocus_countを減らしてください"
        )
    cell_count = (nx - 1) * (ny - 1)
    marching_work = cell_count * level_count_i
    if marching_work > _MAX_MARCHING_CELL_LEVELS:
        raise ResourceLimitError(
            "topographic_contours: 等高線抽出量が上限を超えるため処理しません: "
            f"cells × level_count={marching_work:,} > "
            f"{_MAX_MARCHING_CELL_LEVELS:,}; grid_pitchまたはlevel_countを減らしてください"
        )
    maximum_segments = 2 * cell_count * level_count_i
    ensure_geometry_output(
        "topographic_contours",
        vertices=2 * maximum_segments,
        lines=maximum_segments,
        scratch_bytes=grid_points * 48 + cell_count * 16 + focus_count_i * 120,
        hint="grid_pitchまたはlevel_countを減らしてください",
    )

    values = _field(
        nx=nx,
        ny=ny,
        focuses=_focuses(seed_i, focus_count_i),
        focus_spread=spread_f,
        field_warp=warp_f,
        warp_frequency=frequency_f,
        phase=phase_f,
    )
    origin_x = cx - 0.5 * width_f
    origin_y = cy - 0.5 * height_f
    pitch_x = width_f / float(nx - 1)
    pitch_y = height_f / float(ny - 1)

    output: list[np.ndarray] = []
    for level in _contour_levels(level_count_i):
        paths = marching_squares_paths(
            values,
            origin_x=origin_x,
            origin_y=origin_y,
            pitch_x=pitch_x,
            pitch_y=pitch_y,
            level=level,
        )
        for path in paths:
            line = np.empty((int(path.shape[0]), 3), dtype=np.float32)
            line[:, 0:2] = path
            line[:, 2] = np.float32(cz)
            output.append(line)

    if not output:
        return empty_packed_geometry()
    vertex_count = sum(int(line.shape[0]) for line in output)
    ensure_geometry_output(
        "topographic_contours",
        vertices=vertex_count,
        lines=len(output),
    )
    return pack_polylines(output)


__all__ = ["topographic_contours", "topographic_contours_meta"]
