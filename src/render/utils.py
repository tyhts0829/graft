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
