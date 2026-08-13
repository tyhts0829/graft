"""
Purpose:
    A5用紙のG-code変換・plotter配置を実出力で校正するための基準Sketch。
Use when:
    A5のcanvas-to-machine変換、右下anchor、Y反転、scaleや原点ずれを確認するとき。
Constraints:
    - canvas寸法を148×210 mmに保ち、基準markを紙端から5 mm内側へ維持する。
    - center dot、円、corner/edge mark、labelの位置関係を校正基準として維持する。
    - X非反転・Y反転の判別に必要な非同一軸の情報を対称化して失わせない。
    - import時にpreviewを起動せず、runはmain guard内に限定する。
"""

from grafix import E, G, run

# A5
CANVAS_WIDTH = 148
CANVAS_HEIGHT = 210


def draw(t):
    center_dot = G(name="center_dot").polygon(
        activate=True,
        n_sides=32,
        phase=0.0,
        center=(74.0, 105.0, 0.0),
        scale=2.0,
    )

    circle = G(name="circle").polygon(
        activate=True,
        n_sides=128,
        phase=0.0,
        center=(74.0, 105.0, 0.0),
        scale=69.0,
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
            offset=(128.0, 0.0, 0.0),
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
            offset=(0.0, 190.0, 0.0),
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
        delta=(64.0, 95.0, 0.0),
    )

    cross_center = cross_center_eff(cross)

    edge_line = G(name="edge_line").line(
        activate=True,
        center=(10.0, 104.0, 0.0),
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
            offset=(128.0, 0.0, 0.0),
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
            delta=(63.0, -93.0, 0.0),
        )
        .repeat(
            activate=True,
            count=1,
            cumulative_scale=False,
            cumulative_offset=False,
            cumulative_rotate=False,
            offset=(0.0, 189.0, 0.0),
            rotation_step=(0.0, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            curve=1.0,
            auto_center=True,
            pivot=(0.0, 0.0, 0.0),
        )
    )

    edge_line_h = edge_line_h_eff(edge_line)
    edge_line = edge_line_v + edge_line_h

    text = G(name="alignment_A5").text(text="alignment A5")
    return center_dot, cross_center, circle, cross_corner, edge_line, text


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
