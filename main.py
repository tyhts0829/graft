"""
どこで: リポジトリ直下 `main.py`。
何を: API を用いた簡単なスケッチを定義し、run でプレビュー表示する。
なぜ: 動作確認用の最小エントリポイントとして利用するため。
"""

import sys

sys.path.append("src")

import math

from api import E, G, L, run

CANVAS_WIDTH = 300
CANVAS_HEIGHT = 300


def draw(t: float):
    eff1 = E(name="triple_scale").scale().rotate()
    ply1 = G.grid(nx=100)
    eff2 = E.scale().rotate()
    ply2 = G.polygon()

    return L(eff1(ply1)), L(eff2(ply2), thickness=0.002, color=(1.0, 0.0, 0.0))


if __name__ == "__main__":
    run(
        draw,
        background_color=(1.0, 1.0, 1.0),
        line_thickness=0.001,
        line_color=(0.0, 0.0, 0.0),
        render_scale=4.0,
        canvas_size=(CANVAS_WIDTH, CANVAS_HEIGHT),
        parameter_gui=True,
    )
