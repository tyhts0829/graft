"""
Purpose:
    現在の全built-in primitiveを名前と代表形状が対応する一枚の静的showcaseとして示す。
Use when:
    primitive追加・変更後にregistry網羅性、代表出力、相対的な見え方を確認するとき。
Constraints:
    - PRIMITIVE_NAMESをbuilt-in manifestと重複なく完全一致させる。
    - sampleの返却順をPRIMITIVE_NAMESと一致させ、各primitiveを明示的に一度以上呼ぶ。
    - seedを含む入力を固定し、drawを時刻非依存かつ決定的に保つ。
    - sampleとlabelを対応するcell内へ収め、import時にruntimeを起動しない。
"""

from __future__ import annotations

from grafix import E, G, run

CANVAS_WIDTH = 360
CANVAS_HEIGHT = 530

_COLUMNS = 4
_CELL_WIDTH = CANVAS_WIDTH / _COLUMNS
_CELL_HEIGHT = 80.0
_GRID_TOP = 26.0
_SAMPLE_Y_OFFSET = 34.0
_LABEL_Y_OFFSET = 65.0

PRIMITIVE_NAMES = (
    "arc",
    "asemic",
    "bezier",
    "circle",
    "ellipse",
    "grid",
    "line",
    "lissajous",
    "laplace_field_grid",
    "lsystem",
    "polygon",
    "polyline",
    "polyhedron",
    "rect",
    "sphere",
    "spiral",
    "spline",
    "text",
    "torus",
    "wave",
    "topographic_contours",
    "delaunay",
)


def _cell_center(index: int) -> tuple[float, float, float]:
    """掲載順からsample領域の中心座標を返す。"""

    column = index % _COLUMNS
    row = index // _COLUMNS
    row_top = _GRID_TOP + row * _CELL_HEIGHT
    return (
        (column + 0.5) * _CELL_WIDTH,
        row_top + _SAMPLE_Y_OFFSET,
        0.0,
    )


def _primitive_samples():
    """掲載順に、全組み込みprimitiveの代表的なgeometryを返す。"""

    centers = tuple(_cell_center(index) for index in range(len(PRIMITIVE_NAMES)))

    arc = G.arc(
        radius=21.0,
        start=-35.0,
        sweep=250.0,
        segments=64,
        center=centers[0],
    )
    asemic = G.asemic(
        text="ABC",
        seed=7,
        n_nodes=18,
        candidates=8,
        stroke_min=2,
        stroke_max=4,
        walk_min_steps=2,
        walk_max_steps=3,
        stroke_style="bezier",
        bezier_samples=6,
        text_align="center",
        center=centers[1],
        scale=14.0,
    )
    cx, cy, cz = centers[2]
    bezier = G.bezier(
        p0=(cx - 25.0, cy - 14.0, cz),
        p1=(cx - 9.0, cy + 24.0, cz),
        p2=(cx + 9.0, cy - 24.0, cz),
        p3=(cx + 25.0, cy + 14.0, cz),
        segments=64,
    )
    circle = G.circle(radius=21.0, segments=72, center=centers[3])
    ellipse = G.ellipse(
        radius_x=25.0,
        radius_y=13.0,
        angle=24.0,
        segments=72,
        center=centers[4],
    )
    grid = G.grid(nx=5, ny=4, center=centers[5], scale=48.0)
    line = G.line(center=centers[6], length=52.0, angle=28.0)
    lissajous = G.lissajous(
        a=3,
        b=2,
        phase=35.0,
        samples=160,
        turns=1.0,
        center=centers[7],
        scale=44.0,
    )
    cx, cy, cz = centers[8]
    laplace_field_grid = G.laplace_field_grid(
        preset="cylinder_uniform",
        u_min=-3.0,
        u_max=3.0,
        v_min=-3.0,
        v_max=3.0,
        n_u=7,
        n_v=7,
        samples=48,
        center=(cx, cy, cz),
        scale=8.0,
        rotate=12.0,
        clip=True,
        clip_xmin=cx - 27.0,
        clip_xmax=cx + 27.0,
        clip_ymin=cy - 25.0,
        clip_ymax=cy + 25.0,
        boundary_samples=64,
    )
    cx, cy, cz = centers[9]
    lsystem = G.lsystem(
        kind="plant",
        iters=3,
        center=(cx, cy - 22.0, cz),
        heading=90.0,
        angle=24.0,
        step=2.6,
        jitter=0.0,
        seed=11,
    )
    polygon = G.polygon(
        n_sides=6,
        phase=30.0,
        center=centers[10],
        scale=44.0,
    )
    cx, cy, cz = centers[11]
    polyline = G.polyline(
        points=(
            (cx - 25.0, cy - 12.0, cz),
            (cx - 14.0, cy + 15.0, cz),
            (cx - 2.0, cy - 5.0, cz),
            (cx + 11.0, cy + 18.0, cz),
            (cx + 25.0, cy - 11.0, cz),
        )
    )
    polyhedron = E.rotate(
        auto_center=True,
        rotation=(58.0, 0.0, 28.0),
    )(
        G.polyhedron(
            kind="icosahedron",
            center=centers[12],
            scale=40.0,
        )
    )
    rect = G.rect(
        width=50.0,
        height=29.0,
        angle=18.0,
        center=centers[13],
    )
    sphere = E.rotate(
        auto_center=True,
        rotation=(62.0, 0.0, 24.0),
    )(
        G.sphere(
            subdivisions=0,
            style="rings",
            line_mode="both",
            center=centers[14],
            scale=42.0,
        )
    )
    spiral = G.spiral(
        inner_radius=1.5,
        outer_radius=23.0,
        turns=4.0,
        phase=15.0,
        samples=160,
        center=centers[15],
    )
    cx, cy, cz = centers[16]
    spline = G.spline(
        points=(
            (cx - 25.0, cy - 12.0, cz),
            (cx - 10.0, cy + 17.0, cz),
            (cx + 8.0, cy - 16.0, cz),
            (cx + 25.0, cy + 12.0, cz),
        ),
        closed=False,
        tension=0.1,
        segments_per_span=18,
    )
    cx, cy, cz = centers[17]
    text = G.text(
        text="Aa",
        text_align="center",
        quality=0.2,
        center=(cx, cy - 9.0, cz),
        scale=22.0,
    )
    torus = E.rotate(
        auto_center=True,
        rotation=(62.0, 0.0, 24.0),
    )(
        G.torus(
            major_radius=1.0,
            minor_radius=0.38,
            major_segments=12,
            minor_segments=7,
            center=centers[18],
            scale=17.0,
        )
    )
    wave = G.wave(
        kind="sine",
        length=54.0,
        amplitude=15.0,
        cycles=2.5,
        phase=15.0,
        samples=128,
        angle=0.0,
        center=centers[19],
    )
    topographic_contours = G.topographic_contours(
        width=54.0,
        height=46.0,
        seed=271,
        focus_count=5,
        focus_spread=1.0,
        level_count=9,
        field_warp=1.0,
        warp_frequency=1.0,
        phase=0.0,
        grid_pitch=2.0,
        center=centers[20],
    )
    delaunay = G.delaunay(
        width=54.0,
        height=46.0,
        site_count=18,
        seed=150,
        candidates=8,
        guard_band=2.0,
        center=centers[21],
    )

    return (
        ("arc", arc),
        ("asemic", asemic),
        ("bezier", bezier),
        ("circle", circle),
        ("ellipse", ellipse),
        ("grid", grid),
        ("line", line),
        ("lissajous", lissajous),
        ("laplace_field_grid", laplace_field_grid),
        ("lsystem", lsystem),
        ("polygon", polygon),
        ("polyline", polyline),
        ("polyhedron", polyhedron),
        ("rect", rect),
        ("sphere", sphere),
        ("spiral", spiral),
        ("spline", spline),
        ("text", text),
        ("torus", torus),
        ("wave", wave),
        ("topographic_contours", topographic_contours),
        ("delaunay", delaunay),
    )


def _label(name: str, index: int):
    """sample cell下部へprimitive名を配置する。"""

    x, _y, z = _cell_center(index)
    row = index // _COLUMNS
    row_top = _GRID_TOP + row * _CELL_HEIGHT
    return G.text(
        text=name,
        text_align="center",
        quality=0.1,
        center=(x, row_top + _LABEL_Y_OFFSET, z),
        scale=4,
    )


def draw(_t: float):
    """時刻に依存しないprimitive一覧を返す。"""

    geometry = G.text(
        text="GRAFIX PRIMITIVES",
        text_align="center",
        quality=0.1,
        center=(CANVAS_WIDTH / 2.0, 5.0, 0.0),
        scale=6.0,
    )
    for index, (name, sample) in enumerate(_primitive_samples()):
        geometry = geometry + sample + _label(name, index)
    return geometry


if __name__ == "__main__":
    run(
        draw,
        canvas_size=(CANVAS_WIDTH, CANVAS_HEIGHT),
        render_scale=2.0,
        parameter_gui=False,
        parameter_persistence=False,
    )
