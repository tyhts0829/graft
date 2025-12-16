from __future__ import annotations

import numpy as np
from numba import njit


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
        # Already aligned with Z-axis
        z_offset = vertices[0, 2]
        result = vertices.copy()
        result[:, 2] -= z_offset
        return result, np.eye(3), z_offset

    rotation_axis = rotation_axis / rotation_axis_norm

    # Calculate rotation angle
    cos_theta = np.dot(normal, z_axis)
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

    R = np.eye(3) + np.sin(angle) * K + (1 - np.cos(angle)) * np.dot(K, K)

    # Apply rotation
    transformed_points = np.dot(vertices, R.T)

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
    return np.dot(result, rotation_matrix)
