"""
どこで: リポジトリ直下 `main.py`。
何を: API を用いた簡単なスケッチを定義し、run でプレビュー表示する。
なぜ: 動作確認用の最小エントリポイントとして利用するため。
"""

import sys

sys.path.append("src")

from api import E, G, run
from core.geometry import Geometry


def draw(t: float) -> Geometry:
    c = G.circle(r=10.0 + t, cx=150, cy=150, segments=160)
    return c


if __name__ == "__main__":
    run(
        draw,
        background_color=(1.0, 1.0, 1.0, 1.0),
        line_thickness=0.0015,
        line_color=(0.0, 0.0, 0.0, 1.0),
        render_scale=2.0,
        canvas_size=(300, 300),
    )
