"""
どこで: リポジトリ直下 `main.py`。
何を: API を用いた簡単なスケッチを定義し、run でプレビュー表示する。
なぜ: 動作確認用の最小エントリポイントとして利用するため。
"""

import sys

sys.path.append("src")

from api import E, G, run
from core.geometry import Geometry


def draw(t: float):
    NX = 10
    NY = 10
    # 画面にNX×NY個の円を描く
    circles = []
    for ix in range(NX):
        for iy in range(NY):
            cx = 300.0 * (ix + 0.5) / NX
            cy = 300.0 * (iy + 0.5) / NY
            r = 20.0 + 10.0 * t
            c = G.circle(r=r, cx=cx, cy=cy, segments=80)
            circles.append(c)
    return circles


if __name__ == "__main__":
    run(
        draw,
        background_color=(1.0, 1.0, 1.0, 1.0),
        line_thickness=0.0015,
        line_color=(0.0, 0.0, 0.0, 1.0),
        render_scale=2.0,
        canvas_size=(300, 300),
    )
