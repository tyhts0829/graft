"""realize_cache / inflight の動作を確認するための簡易スクリプト。

src 配下の実装は変更せず、tools.cache_check.visualize_cache のトレーサを用いて
どの Geometry が計算され、どの呼び出しがキャッシュヒットだったかを出力する。
"""

from __future__ import annotations

import sys
from pathlib import Path

from tools.cache_check import (
    FrameRealizeLog,
    RealizeEventType,
    export_geometry_dag_dot,
    frame_logging,
    install_realize_tracer,
    save_geometry_dag_png,
    save_geometry_dag_png_multiframe,
)


def main() -> None:
    """G/E/realize を用いてキャッシュ挙動を確認し、ログと DOT/PNG を出力する。"""
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parents[1]

    # src レイアウトのパッケージ（grafix）を import できるようにする。
    sys.path.append(str(project_root / "src"))

    # トレーサを先にインストールしてから realize を import する。
    install_realize_tracer()

    from grafix.api import E, G  # type: ignore[import]
    from grafix.core.geometry import Geometry  # type: ignore[import]
    from grafix.core.realize import realize  # type: ignore[import]

    # circle2 つと concat/scale からなる少し大きめの DAG を構築。
    base1 = G.circle(r=1.0)
    base2 = G.circle(r=2.0, cx=1.0)
    concat = Geometry.create(op="concat", inputs=(base1, base2), params={})

    # フレーム 1, 2: 同じスケール係数。
    scaled_v1 = E.scale(scale=(1.5, 1.5, 1.5))(concat)
    # フレーム 3: スケール係数を変えて別 GeometryId にする。
    scaled_v2 = E.scale(scale=(0.75, 0.75, 0.75))(concat)

    # 3 フレーム分のログを別々に収集する。
    frame_logs: list[FrameRealizeLog] = []
    frame_roots: list[Geometry] = []

    # frame 1: すべて初回計算。
    log1 = FrameRealizeLog()
    with frame_logging(log1):
        _ = realize(scaled_v1)
    frame_logs.append(log1)
    frame_roots.append(scaled_v1)

    # frame 2: 同じ Geometry をもう一度 realize（ほぼキャッシュヒットになる想定）。
    log2 = FrameRealizeLog()
    with frame_logging(log2):
        _ = realize(scaled_v1)
    frame_logs.append(log2)
    frame_roots.append(scaled_v1)

    # frame 3: スケール係数を変えた Geometry を realize。
    # concat と circle はキャッシュヒットしつつ、scale 部分だけ再計算される挙動を期待する。
    log3 = FrameRealizeLog()
    with frame_logging(log3):
        _ = realize(scaled_v2)
    frame_logs.append(log3)
    frame_roots.append(scaled_v2)

    # ここでは 3 フレーム分をまとめたログとして 1 つに連結したビューも作る。
    merged_log = FrameRealizeLog()
    for log in frame_logs:
        merged_log.events.extend(log.events)

    # イベント一覧を標準出力に出す。
    print("=== Realize events (merged) ===")  # noqa: T201
    for ev in merged_log.events:
        duration = (
            f"{ev.duration_sec:.6f}s" if ev.duration_sec is not None else "-"
        )
        print(  # noqa: T201
            f"{ev.event_type.name:12} "
            f"id={ev.geometry_id[:8]} "
            f"op={ev.op or '-':8} "
            f"depth={ev.depth or 0} "
            f"duration={duration}",
        )

    # 種別ごとの件数を集計する。
    counts: dict[RealizeEventType, int] = {
        RealizeEventType.COMPUTE: 0,
        RealizeEventType.CACHE_HIT: 0,
        RealizeEventType.INFLIGHT_WAIT: 0,
    }
    for ev in merged_log.events:
        counts[ev.event_type] = counts.get(ev.event_type, 0) + 1

    print("\n=== Summary ===")  # noqa: T201
    print(f"COMPUTE      : {counts[RealizeEventType.COMPUTE]}")  # noqa: T201
    print(f"CACHE_HIT    : {counts[RealizeEventType.CACHE_HIT]}")  # noqa: T201
    print(f"INFLIGHT_WAIT: {counts[RealizeEventType.INFLIGHT_WAIT]}")  # noqa: T201

    # Geometry DAG を DOT として保存する（スクリプトディレクトリ配下）。
    dot_path = script_dir / "cache_check_dag.dot"
    dot_text = export_geometry_dag_dot(root_geometry=frame_roots, frame_log=merged_log)
    dot_path.write_text(dot_text, encoding="utf-8")
    print(f"\nDOT を {dot_path} に出力した。")  # noqa: T201

    png_path = script_dir / "cache_check_dag.png"
    try:
        save_geometry_dag_png(
            path=str(png_path),
            root_geometry=frame_roots,
            frame_log=merged_log,
        )
    except RuntimeError as exc:
        print(f"PNG 出力はスキップ: {exc}")  # noqa: T201
    else:
        print(f"PNG を {png_path} に出力した。")  # noqa: T201

    # フレームごとにクラスタを分けた PNG も出力する。
    multi_png_path = script_dir / "cache_check_dag_multiframe.png"
    try:
        save_geometry_dag_png_multiframe(
            path=str(multi_png_path),
            root_geometry=frame_roots,
            frame_logs=frame_logs,
            frame_labels=["frame 1", "frame 2", "frame 3"],
        )
    except RuntimeError as exc:
        print(f"マルチフレーム PNG 出力はスキップ: {exc}")  # noqa: T201
    else:
        print(f"マルチフレーム PNG を {multi_png_path} に出力した。")  # noqa: T201


if __name__ == "__main__":
    main()
