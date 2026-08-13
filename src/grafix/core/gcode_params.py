"""
Purpose:
    runtime config と G-code encoder が共有する machine placement・安全範囲・stroke optimization の immutable policy を定義する。
Use when:
    G-code 座標変換、紙の anchor、ベッド制約、stroke 順最適化の設定を変更・調査する場合。
Constraints:
    - ``paper_bottom_right_mm`` を紙の右下 anchor の正本とし、実 canvas size と独立した旧 origin/height policy を戻さない。
    - X は反転せず、``y_down`` は canvas Y と machine Y の向きだけを切り替える。
    - stroke 最適化設定は ``Layer.gcode_optimize=True`` のレイヤだけに適用し、Geometry/cache identity へ入れない。
See:
    architecture.md 「G-code の canvas/machine 座標 contract」
"""

from __future__ import annotations

from dataclasses import dataclass

from grafix.core.value_validation import exact_bool, exact_integer, finite_real


def _finite_pair(value: object, *, name: str) -> tuple[float, float]:
    """2要素 tuple を有限実数ペアとして返す。"""

    if type(value) is not tuple or len(value) != 2:
        raise TypeError(f"{name} は2要素の tuple である必要があります")
    return (
        finite_real(value[0], name=f"{name}[0]"),
        finite_real(value[1], name=f"{name}[1]"),
    )


def _optional_range(
    value: object,
    *,
    name: str,
) -> tuple[float, float] | None:
    """None または昇順の有限実数ペアを返す。"""

    if value is None:
        return None
    lower, upper = _finite_pair(value, name=name)
    if lower >= upper:
        raise ValueError(f"{name} は (min, max) の昇順である必要があります")
    return lower, upper


@dataclass(frozen=True, slots=True)
class GCodeParams:
    """G-code 生成と runtime config が共有する不変パラメータ。

    Parameters
    ----------
    travel_feed : float
        ペンアップ移動のフィードレート [mm/min]。正の有限実数。
    draw_feed : float
        ペンダウン描画のフィードレート [mm/min]。正の有限実数。
    z_up : float
        ペンアップ時の Z 高さ [mm]。
    z_down : float
        ペンダウン時の Z 高さ [mm]。
    y_down : bool
        True の場合、Y 反転を行う。
    paper_bottom_right_mm : tuple[float, float]
        紙（canvas）の右下角を置く machine XY 座標 [mm]。
    decimals : int
        数値出力の小数点以下の桁数。0 以上。
    paper_margin_mm : float
        紙の外周安全マージン [mm]。0 以上の有限実数。
    bed_x_range : tuple[float, float] or None
        ベッド X 範囲 [mm]。有限かつ昇順のペア。None で無効。
    bed_y_range : tuple[float, float] or None
        ベッド Y 範囲 [mm]。有限かつ昇順のペア。None で無効。
    bridge_draw_distance : float or None
        同一レイヤの最終ストローク列で、隣接ストローク間の距離がこの値 [mm]
        未満なら、ペンアップを省略して隙間を描画で繋ぐ。0 以上の有限実数。
        None で無効。``Layer.gcode_optimize=False`` のレイヤでは適用しない。
    optimize_travel : bool
        True の場合、同一レイヤ内の全クリップ後ストロークを並べ替え、
        ペンアップ移動距離を小さくする。False の場合は入力順を維持する。
        ``Layer.gcode_optimize=False`` のレイヤでは適用しない。
    allow_reverse : bool
        ``optimize_travel=True`` での並べ替え時に、ストロークの逆向き描画を
        許可する。``Layer.gcode_optimize=False`` のレイヤでは適用しない。

    Notes
    -----
    配置変換は ``canvas_size`` の右下 ``(width, height)`` を
    ``paper_bottom_right_mm`` へ写す。そのため、紙サイズが変わっても
    同じ machine 上の点へ右下角を合わせられる。

    ``L.layer(..., gcode_optimize=False)`` はレイヤ単位の粗いマスタースイッチであり、
    そのレイヤでは並べ替え、逆向き描画、短距離 bridge をすべて停止する。
    ``gcode_optimize=True`` のレイヤだけが本クラスの上記 3 設定に従う。
    """

    travel_feed: float = 3000.0
    draw_feed: float = 3000.0
    z_up: float = 3.0
    z_down: float = -1.0
    y_down: bool = True
    paper_bottom_right_mm: tuple[float, float] = (302.019, 0.0)
    decimals: int = 3
    paper_margin_mm: float = 2.0
    bed_x_range: tuple[float, float] | None = None
    bed_y_range: tuple[float, float] | None = None
    bridge_draw_distance: float | None = 0.5
    optimize_travel: bool = True
    allow_reverse: bool = True

    def __post_init__(self) -> None:
        """全 field を暗黙 coercion のない canonical 値へ検証する。"""

        object.__setattr__(
            self,
            "travel_feed",
            finite_real(
                self.travel_feed,
                name="travel_feed",
                minimum=0.0,
                minimum_inclusive=False,
            ),
        )
        object.__setattr__(
            self,
            "draw_feed",
            finite_real(
                self.draw_feed,
                name="draw_feed",
                minimum=0.0,
                minimum_inclusive=False,
            ),
        )
        object.__setattr__(self, "z_up", finite_real(self.z_up, name="z_up"))
        object.__setattr__(self, "z_down", finite_real(self.z_down, name="z_down"))
        object.__setattr__(self, "y_down", exact_bool(self.y_down, name="y_down"))
        object.__setattr__(
            self,
            "paper_bottom_right_mm",
            _finite_pair(
                self.paper_bottom_right_mm,
                name="paper_bottom_right_mm",
            ),
        )
        object.__setattr__(
            self,
            "decimals",
            exact_integer(self.decimals, name="decimals", minimum=0),
        )
        object.__setattr__(
            self,
            "paper_margin_mm",
            finite_real(
                self.paper_margin_mm,
                name="paper_margin_mm",
                minimum=0.0,
            ),
        )
        object.__setattr__(
            self,
            "bed_x_range",
            _optional_range(self.bed_x_range, name="bed_x_range"),
        )
        object.__setattr__(
            self,
            "bed_y_range",
            _optional_range(self.bed_y_range, name="bed_y_range"),
        )
        object.__setattr__(
            self,
            "bridge_draw_distance",
            (
                None
                if self.bridge_draw_distance is None
                else finite_real(
                    self.bridge_draw_distance,
                    name="bridge_draw_distance",
                    minimum=0.0,
                )
            ),
        )
        object.__setattr__(
            self,
            "optimize_travel",
            exact_bool(self.optimize_travel, name="optimize_travel"),
        )
        object.__setattr__(
            self,
            "allow_reverse",
            exact_bool(self.allow_reverse, name="allow_reverse"),
        )


__all__ = ["GCodeParams"]
