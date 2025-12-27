"""
どこで: `sketch/main.py`。
何を: API を用いた簡単なスケッチを定義し、run でプレビュー表示する。
なぜ: 動作確認用の最小エントリポイントとして利用するため。
"""

from grafix import E, G, run

CANVAS_WIDTH = 300
CANVAS_HEIGHT = 300


def draw(t: float):
    g = G.polygon()
    e = E.partition().drop().fill(angle=(10, 20, 30, 40, 50))
    return e(g)


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
