"""
Purpose:
    A4用紙のG-code変換・plotter配置を実出力で校正するための基準Sketch。
Use when:
    A4のcanvas-to-machine変換、右下anchor、Y反転、scaleや原点ずれを確認するとき。
Constraints:
    - canvas寸法を210×297 mmに保ち、基準markを紙端から5 mm内側へ維持する。
    - 既存G-codeとの差分検証に使うため、基準geometryの点数・順序・位置を不用意に変えない。
    - X非反転・Y反転の判別に必要なcorner、center、edge markを対称化して意味を失わせない。
    - import時にpreviewを起動せず、runはmain guard内に限定する。
"""

from grafix import E, G, run

# A4
CANVAS_WIDTH = 210
CANVAS_HEIGHT = 297


def draw(t):
    circle = G(name="circle").polygon(
        activate=True,
        n_sides=128,
        phase=0.0,
        center=(105.0, 148.0, 0.0),
        scale=100.0,
    )

    l1 = G(name="line1").line(
        activate=True,
        center=(10.0, 10.0, 0.0),
        length=10.0,
        angle=0.0,
    )

    l1_eff = E(name="line1_eff").repeat(
        activate=True,
        count=1,
        cumulative_scale=False,
        cumulative_offset=False,
        cumulative_rotate=False,
        offset=(0.0, 0.0, 0.0),
        rotation_step=(0.0, 0.0, 90.0),
        scale=(1.0, 1.0, 1.0),
        curve=1.0,
        auto_center=True,
        pivot=(0.0, 0.0, 0.0),
    )

    cross = l1_eff(l1)
    cross_corner_eff = (
        E(name="cross_corner_eff")
        .repeat(
            activate=True,
            count=1,
            cumulative_scale=False,
            cumulative_offset=False,
            cumulative_rotate=False,
            offset=(190.0, 0.0, 0.0),
            rotation_step=(0.0, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            curve=1.0,
            auto_center=True,
            pivot=(0.0, 0.0, 0.0),
        )
        .repeat(
            activate=True,
            count=1,
            cumulative_scale=False,
            cumulative_offset=False,
            cumulative_rotate=False,
            offset=(0.0, 277.0, 0.0),
            rotation_step=(0.0, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            curve=1.0,
            auto_center=True,
            pivot=(0.0, 0.0, 0.0),
        )
    )

    cross_corner = cross_corner_eff(cross)
    cross_center_eff = E(name="cross_center_eff").affine(
        activate=True,
        auto_center=True,
        pivot=(0.0, 0.0, 0.0),
        rotation=(0.0, 0.0, 0.0),
        scale=(1.0, 1.0, 1.0),
        delta=(95.0, 140.0, 0.0),
    )

    cross_center = cross_center_eff(cross)

    edge_line = G(name="edge_line").line(
        activate=True,
        center=(10.0, 147.0, 0.0),
        length=82.818,
        angle=0.0,
    )

    edge_line_v_eff = (
        E(name="edge_line_v_eff")
        .affine(
            activate=True,
            auto_center=True,
            pivot=(0.0, 0.0, 0.0),
            rotation=(0.0, 0.0, 90.0),
            scale=(1.0, 1.0, 1.0),
            delta=(0.0, 0.0, 0.0),
        )
        .repeat(
            activate=True,
            count=1,
            cumulative_scale=False,
            cumulative_offset=False,
            cumulative_rotate=False,
            offset=(190.0, 0.0, 0.0),
            rotation_step=(0.0, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            curve=1.0,
            auto_center=True,
            pivot=(0.0, 0.0, 0.0),
        )
    )

    edge_line_v = edge_line_v_eff(edge_line)
    edge_line_h_eff = (
        E(name="edge_line_h_eff")
        .affine(
            activate=True,
            auto_center=True,
            pivot=(0.0, 0.0, 0.0),
            rotation=(0.0, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            delta=(94.0, -136.0, 0.0),
        )
        .repeat(
            activate=True,
            count=1,
            cumulative_scale=False,
            cumulative_offset=False,
            cumulative_rotate=False,
            offset=(0.0, 276.0, 0.0),
            rotation_step=(0.0, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            curve=1.0,
            auto_center=True,
            pivot=(0.0, 0.0, 0.0),
        )
    )

    edge_line_h = edge_line_h_eff(edge_line)
    edge_line = edge_line_v + edge_line_h
    return circle, cross_corner, cross_center, edge_line


if __name__ == "__main__":
    run(
        draw,
        background_color=(1.0, 1.0, 1.0),
        line_thickness=0.001,
        line_color=(0.0, 0.0, 0.0),
        render_scale=3.5,
        canvas_size=(CANVAS_WIDTH, CANVAS_HEIGHT),
        parameter_gui=True,
        # midi_port_name="Grid",
        # midi_mode="14bit",
    )
