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
    NX = 50
    NY = 50
    WHITE_SPACE = 10
    # 画面にNX×NY個の円を描く
    circles = []
    for ix in range(NX):
        for iy in range(NY):
            cx = (CANVAS_WIDTH - WHITE_SPACE * 2) / (NX - 1) * ix + WHITE_SPACE
            cy = (CANVAS_HEIGHT - WHITE_SPACE * 2) / (NY - 1) * iy + WHITE_SPACE
            r = 10 + 5 * math.sin(t + 20 * 0.5)
            circles.append(G.circle(r=r, cx=cx, cy=cy))
    return L(circles, color=(0.2, 0.6, 0.8), thickness=0.001)


if __name__ == "__main__":
    run(
        draw,
        background_color=(1.0, 1.0, 1.0, 1.0),
        line_thickness=0.0015,
        line_color=(0.0, 0.0, 0.0),
        render_scale=4.0,
        canvas_size=(CANVAS_WIDTH, CANVAS_HEIGHT),
    )
