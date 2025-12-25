"""
どこで: `sketch/main.py`。
何を: API を用いた簡単なスケッチを定義し、run でプレビュー表示する。
なぜ: 動作確認用の最小エントリポイントとして利用するため。
"""

from grafix import E, G, L, cc, run

CANVAS_WIDTH = 300
CANVAS_HEIGHT = 300


def draw(t: float):
    ply1 = G.text(font="Cappadocia.otf", text="GRAFIX")
    eff1 = (
        E(name="pl2")
        .affine()
        .mirror()
        .fill(density=1000)
        .subdivide(subdivisions=5)
        .displace(t=t * 0.1)
    )
    return eff1(ply1)


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
