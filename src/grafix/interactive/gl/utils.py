from __future__ import annotations

# どこで: `src/grafix/interactive/gl/utils.py`。
# 何を: 描画で使う小さなユーティリティ（投影行列生成）を提供する。
# なぜ: renderer 初期化等で共有し、座標系の定義を一箇所に集約するため。

import numpy as np


def build_projection(canvas_width: float, canvas_height: float) -> "np.ndarray":
    """キャンバス mm を基準とする正射影行列（ModernGL 用の転置済み）を返す。"""
    proj = np.array(
        [
            [2 / canvas_width, 0, 0, -1],
            [0, -2 / canvas_height, 0, 1],
            [0, 0, -1, 0],
            [0, 0, 0, 1],
        ],
        dtype="f4",
    ).T
    return proj
