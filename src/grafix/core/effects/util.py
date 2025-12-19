from __future__ import annotations

import numpy as np
from numba import njit  # type: ignore[import-untyped]


@njit(cache=True)
def _dot3(a: np.ndarray, b: np.ndarray) -> float:
    return float(a[0] * b[0] + a[1] * b[1] + a[2] * b[2])


@njit(cache=True)
def _matmul3(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    out = np.empty((3, 3), dtype=np.float64)
    for i in range(3):
        for j in range(3):
            out[i, j] = a[i, 0] * b[0, j] + a[i, 1] * b[1, j] + a[i, 2] * b[2, j]
    return out


@njit(cache=True)
def _apply_row_mat(points: np.ndarray, mat: np.ndarray) -> np.ndarray:
    out = np.empty_like(points)
    for i in range(points.shape[0]):
        x = float(points[i, 0])
        y = float(points[i, 1])
        z = float(points[i, 2])
        out[i, 0] = x * mat[0, 0] + y * mat[1, 0] + z * mat[2, 0]
        out[i, 1] = x * mat[0, 1] + y * mat[1, 1] + z * mat[2, 1]
        out[i, 2] = x * mat[0, 2] + y * mat[1, 2] + z * mat[2, 2]
    return out


@njit(cache=True)
def transform_to_xy_plane(vertices: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """頂点をXY平面（z=0）に変換する。

    頂点の法線ベクトルがZ軸に沿うように回転させ、
    その後z座標を0に平行移動する。

    引数:
        vertices: (N, 3) 3D点の配列。

    返り値:
        以下のタプル:
            - transformed_points: (N, 3) XY平面上の配列
            - rotation_matrix: (3, 3) 使用された回転行列
            - z_offset: z方向の平行移動量
    """
    if vertices.shape[0] < 3:
        return vertices.astype(np.float64).copy(), np.eye(3), 0.0

    # Ensure float64 type for calculations
    vertices = vertices.astype(np.float64)

    # Calculate polygon normal vector
    v1 = vertices[1] - vertices[0]
    v2 = vertices[2] - vertices[0]
    normal = np.cross(v1, v2)
    norm = np.sqrt(normal[0] ** 2 + normal[1] ** 2 + normal[2] ** 2)

    if norm == 0:
        return vertices.copy(), np.eye(3), 0.0

    normal = normal / norm  # normalize

    # Calculate rotation axis (cross product with Z-axis)
    z_axis = np.array([0.0, 0.0, 1.0])
    rotation_axis = np.cross(normal, z_axis)

    rotation_axis_norm = np.sqrt(
        rotation_axis[0] ** 2 + rotation_axis[1] ** 2 + rotation_axis[2] ** 2
    )
    if rotation_axis_norm == 0:
        # Z 軸と平行（または反平行）なら、姿勢はそのまま（法線方向の符号は入力順に依存）。
        R0 = np.eye(3)
    else:
        rotation_axis = rotation_axis / rotation_axis_norm

        # Calculate rotation angle
        cos_theta = _dot3(normal, z_axis)
        # Manual clip for njit compatibility
        if cos_theta < -1.0:
            cos_theta = -1.0
        elif cos_theta > 1.0:
            cos_theta = 1.0
        angle = np.arccos(cos_theta)

        # Create rotation matrix using Rodrigues' formula
        # Create K matrix manually for njit compatibility
        K = np.zeros((3, 3))
        K[0, 1] = -rotation_axis[2]
        K[0, 2] = rotation_axis[1]
        K[1, 0] = rotation_axis[2]
        K[1, 2] = -rotation_axis[0]
        K[2, 0] = -rotation_axis[1]
        K[2, 1] = rotation_axis[0]

        R0 = np.eye(3) + np.sin(angle) * K + (1 - np.cos(angle)) * _matmul3(K, K)

    # --- 追加: 面内の回転自由度を固定する ---
    #
    # 法線を Z に揃えるだけだと、法線まわりの回転（面内回転）が未定義になり、
    # 物体が自分の法線まわりに回転したときにハッチが「中で回って見える」。
    # そこで XY 上で「最初に見つかった非ゼロの辺」を +X に揃える回転を足す。
    # （旧実装の挙動に近づける）
    aligned0 = _apply_row_mat(vertices, R0.T)
    phi = 0.0
    found = False
    for i in range(vertices.shape[0] - 1):
        dx = float(aligned0[i + 1, 0] - aligned0[i, 0])
        dy = float(aligned0[i + 1, 1] - aligned0[i, 1])
        if dx * dx + dy * dy > 1e-12:
            phi = np.arctan2(dy, dx)
            found = True
            break

    if found:
        c = float(np.cos(phi))
        s = float(np.sin(phi))
        # 回転角は -phi（辺の向きを +X へ）
        Rz = np.eye(3)
        Rz[0, 0] = c
        Rz[0, 1] = s
        Rz[1, 0] = -s
        Rz[1, 1] = c
        R = _matmul3(Rz, R0)
    else:
        R = R0

    # Apply rotation
    transformed_points = _apply_row_mat(vertices, R.T)

    # Get z-coordinate and align to z=0
    z_offset = transformed_points[0, 2]
    transformed_points[:, 2] -= z_offset

    return transformed_points, R, z_offset


@njit(cache=True)
def transform_back(
    vertices: np.ndarray, rotation_matrix: np.ndarray, z_offset: float
) -> np.ndarray:
    """頂点を元の向きに戻す（`transform_to_xy_plane` の逆変換）。"""
    # Ensure consistent float64 type for calculations
    vertices = vertices.astype(np.float64)
    rotation_matrix = rotation_matrix.astype(np.float64)

    # Restore z-coordinate
    result = vertices.copy()
    result[:, 2] += z_offset

    # Apply inverse rotation
    return _apply_row_mat(result, rotation_matrix)
