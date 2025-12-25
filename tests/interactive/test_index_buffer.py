"""interactive.gl.index_buffer の `build_line_indices` をテスト。"""

from __future__ import annotations

import numpy as np

from grafix.interactive.gl.index_buffer import build_line_indices
from grafix.interactive.gl.index_buffer import build_line_indices_and_stats
from grafix.interactive.gl.line_mesh import LineMesh


def test_build_line_indices_empty() -> None:
    offsets = np.array([0], dtype=np.int32)
    indices = build_line_indices(offsets)
    assert indices.dtype == np.uint32
    assert indices.size == 0


def test_build_line_indices_single_polyline() -> None:
    # 3 vertices => 3 indices
    offsets = np.array([0, 3], dtype=np.int32)
    indices = build_line_indices(offsets)
    assert indices.tolist() == [0, 1, 2]


def test_build_line_indices_multiple_polylines_with_restart() -> None:
    offsets = np.array([0, 3, 5], dtype=np.int32)
    indices = build_line_indices(offsets)
    assert indices.tolist() == [
        0,
        1,
        2,
        LineMesh.PRIMITIVE_RESTART_INDEX,
        3,
        4,
    ]


def test_build_line_indices_skips_short_polylines() -> None:
    # [0, 1) は 1 頂点なのでスキップし、[1, 4) のみ出力される
    offsets = np.array([0, 1, 4], dtype=np.int32)
    indices = build_line_indices(offsets)
    assert indices.tolist() == [1, 2, 3]


def test_build_line_indices_is_cached_by_offsets_content() -> None:
    offsets1 = np.array([0, 3, 5], dtype=np.int32)
    offsets2 = np.array([0, 3, 5], dtype=np.int32)
    indices1 = build_line_indices(offsets1)
    indices2 = build_line_indices(offsets2)
    assert indices1 is indices2


def test_build_line_indices_and_stats_single_polyline() -> None:
    offsets = np.array([0, 3], dtype=np.int32)
    indices, stats = build_line_indices_and_stats(offsets)
    assert indices.tolist() == [0, 1, 2]
    assert stats.draw_vertices == 3
    assert stats.draw_lines == 1


def test_build_line_indices_and_stats_skips_short_polylines() -> None:
    # [0, 1) は 1 頂点なのでスキップし、[1, 4) のみ描画対象としてカウントされる
    offsets = np.array([0, 1, 4], dtype=np.int32)
    indices, stats = build_line_indices_and_stats(offsets)
    assert indices.tolist() == [1, 2, 3]
    assert stats.draw_vertices == 3
    assert stats.draw_lines == 1


def test_build_line_indices_and_stats_multiple_polylines_with_restart() -> None:
    offsets = np.array([0, 3, 5], dtype=np.int32)
    indices, stats = build_line_indices_and_stats(offsets)
    assert indices.tolist() == [
        0,
        1,
        2,
        LineMesh.PRIMITIVE_RESTART_INDEX,
        3,
        4,
    ]
    assert stats.draw_vertices == 5
    assert stats.draw_lines == 2
