"""
どこで: `sketch/main.py`。
何を: API を用いた簡単なスケッチを定義し、run でプレビュー表示する。
なぜ: 動作確認用の最小エントリポイントとして利用するため。
"""

from grafix.api import E, G, L, run

CANVAS_WIDTH = 300
CANVAS_HEIGHT = 300


def draw(t: float):
    ply1 = G.polyhedron()
    eff1 = (
        E(name="eff_ply1")
        .affine()
        .fill()
        .subdivide()
        .displace()
        .rotate(rotation=(t * 5, t * 5, t * 5))
    )

    ply2 = G.text()
    eff2 = E.affine().fill()

    return L(eff1(ply1)), L(eff2(ply2))


if __name__ == "__main__":
    run(
        draw,
        background_color=(1.0, 1.0, 1.0),
        line_thickness=0.001,
        line_color=(0.0, 0.0, 0.0),
        render_scale=3.0,
        canvas_size=(CANVAS_WIDTH, CANVAS_HEIGHT),
        parameter_gui=True,
    )
