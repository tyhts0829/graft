"""
Purpose:
    preview操作用timelineと固定fps recording timelineの`draw(t)`規則を定義する。
Use when:
    play/pause、seek、speed、bookmark、frame step、または録画時刻を変更する場合。
Constraints:
    - previewの実時間clockとrecordingの決定的frame clockを混同しない。
    - snapshotは一時点のtransport stateとしてimmutableに渡す。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time

from grafix.core.parameters.identity import identity_string
from grafix.core.value_validation import exact_bool, exact_integer, finite_real


TimeSource = Callable[[], float]


@dataclass(frozen=True, slots=True)
class TimeBookmark:
    """名前付きtimeline時刻。"""

    name: str
    t: float
    variation_name: str | None = None

    def __post_init__(self) -> None:
        name = identity_string(self.name, name="bookmark name")
        if name != name.strip():
            raise ValueError("bookmark name の前後に空白は使用できません")
        object.__setattr__(self, "t", finite_real(self.t, name="bookmark t"))
        if self.variation_name is not None:
            variation_name = identity_string(
                self.variation_name,
                name="variation_name",
            )
            if variation_name != variation_name.strip():
                raise ValueError("variation_name の前後に空白は使用できません")


@dataclass(frozen=True, slots=True, kw_only=True)
class TransportSnapshot:
    """プレビューの transport 状態。"""

    t: float
    is_playing: bool
    speed: float
    epoch: int
    loop_in: float | None = None
    loop_out: float | None = None
    bookmarks: tuple[TimeBookmark, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "t", finite_real(self.t, name="t"))
        exact_bool(self.is_playing, name="is_playing")
        object.__setattr__(
            self,
            "speed",
            finite_real(
                self.speed,
                name="speed",
                minimum=0.0,
                minimum_inclusive=False,
            ),
        )
        object.__setattr__(
            self,
            "epoch",
            exact_integer(self.epoch, name="epoch", minimum=0),
        )
        if (self.loop_in is None) != (self.loop_out is None):
            raise ValueError("loop_in と loop_out は同時に指定してください")
        if self.loop_in is not None and self.loop_out is not None:
            loop_in = finite_real(self.loop_in, name="loop_in")
            loop_out = finite_real(self.loop_out, name="loop_out")
            if loop_out <= loop_in:
                raise ValueError("loop_out は loop_in より大きい必要があります")
            object.__setattr__(self, "loop_in", loop_in)
            object.__setattr__(self, "loop_out", loop_out)
        if not isinstance(self.bookmarks, tuple) or any(
            not isinstance(bookmark, TimeBookmark)
            for bookmark in self.bookmarks
        ):
            raise TypeError("bookmarks は TimeBookmark の tuple である必要があります")


class TransportClock:
    """プレビュー用の操作可能な実時間クロック。

    `time_source` は単調増加時計を想定し、既定値は `perf_counter`。
    注入した時刻源が逆行した場合も `t` が意図せず戻らないよう、
    直前に観測した値で clamp する。

    Notes
    -----
    `seek` / `reset` / `step_frame` はユーザーが意図した timeline 操作のため、
    それらの呼び出しに限り `t` は戻ることがある。
    """

    def __init__(
        self,
        *,
        start_time: float | None = None,
        time_source: TimeSource = time.perf_counter,
        initial_t: float = 0.0,
        playing: bool = True,
        speed: float = 1.0,
    ) -> None:
        if not callable(time_source):
            raise TypeError("time_source は callable である必要があります")
        self._time_source = time_source
        source_start = finite_real(
            time_source() if start_time is None else start_time,
            name="start_time",
        )
        timeline_start = finite_real(initial_t, name="initial_t")
        playback_speed = self._validated_speed(speed)

        self._last_source_time = source_start
        self._anchor_source_time = source_start
        self._anchor_t = timeline_start
        self._is_playing = exact_bool(playing, name="playing")
        self._speed = playback_speed
        # 非同期 draw の結果が timeline の不連続をまたいで採用されないよう、
        # seek/reset/step ごとに単調増加させる generation。
        self._epoch = 0
        self._loop: tuple[float, float] | None = None
        self._bookmarks: dict[str, TimeBookmark] = {}

    @property
    def is_playing(self) -> bool:
        """再生中なら `True`。"""

        return self._is_playing

    @property
    def speed(self) -> float:
        """現在の再生倍率を返す。"""

        return self._speed

    @property
    def epoch(self) -> int:
        """現在の transport generation を返す。

        連続再生、pause/play、speed 変更では変化せず、timeline を不連続に
        移動する操作でだけ増加する。非同期 evaluator はこの値を task/result
        に結び付け、古い generation の結果を破棄できる。
        """

        return self._epoch

    @property
    def loop_range(self) -> tuple[float, float] | None:
        """有効なloop in/outを返す。"""

        return self._loop

    @property
    def bookmarks(self) -> tuple[TimeBookmark, ...]:
        """登録順のimmutable bookmark列を返す。"""

        return tuple(self._bookmarks.values())

    def t(self) -> float:
        """現在のフレーム時刻 `t`（秒）を返す。"""

        if not self._is_playing:
            return self._anchor_t
        source_time = self._read_source_time()
        timeline_t = self._timeline_at(source_time)
        loop = self._loop
        if loop is None or timeline_t < loop[1]:
            return timeline_t

        loop_in, loop_out = loop
        wrapped = loop_in + ((timeline_t - loop_in) % (loop_out - loop_in))
        self._anchor_t = wrapped
        self._anchor_source_time = source_time
        self.mark_discontinuity()
        return wrapped

    def snapshot(self) -> TransportSnapshot:
        """時刻と再生状態を同じ観測点の snapshot として返す。"""

        t = self.t()
        loop = self._loop
        return TransportSnapshot(
            t=t,
            is_playing=self.is_playing,
            speed=self.speed,
            epoch=self.epoch,
            loop_in=None if loop is None else loop[0],
            loop_out=None if loop is None else loop[1],
            bookmarks=self.bookmarks,
        )

    def set_loop(self, loop_in: float, loop_out: float) -> None:
        """再生loopの半開区間 ``[loop_in, loop_out)`` を設定する。"""

        start = finite_real(loop_in, name="loop_in")
        end = finite_real(loop_out, name="loop_out")
        if end <= start:
            raise ValueError("loop_out は loop_in より大きい必要がある")
        self._loop = (start, end)

    def clear_loop(self) -> None:
        """再生loopを解除する。"""

        self._loop = None

    def set_bookmark(
        self,
        name: str,
        *,
        t: float | None = None,
        variation_name: str | None = None,
    ) -> TimeBookmark:
        """時刻へbookmarkを保存し、任意でnamed variationと関連付ける。"""

        name_s = identity_string(name, name="bookmark name")
        bookmark_t = self.t() if t is None else t
        bookmark = TimeBookmark(
            name=name_s,
            t=bookmark_t,
            variation_name=variation_name,
        )
        self._bookmarks[name_s] = bookmark
        return bookmark

    def remove_bookmark(self, name: str) -> bool:
        """bookmarkを削除し、存在したかを返す。"""

        return self._bookmarks.pop(
            identity_string(name, name="bookmark name"),
            None,
        ) is not None

    def seek_bookmark(self, name: str) -> float:
        """bookmarkへseekし、移動後の時刻を返す。"""

        name_s = identity_string(name, name="bookmark name")
        try:
            target = self._bookmarks[name_s].t
        except KeyError as exc:
            raise KeyError(f"未登録の bookmark: {name_s!r}") from exc
        self.seek(target)
        return target

    def play(self) -> None:
        """現在の `t` から再生を開始する。"""

        if self._is_playing:
            return
        self._anchor_source_time = self._read_source_time()
        self._is_playing = True

    def pause(self) -> None:
        """現在の `t` で再生を一時停止する。"""

        if not self._is_playing:
            return
        now = self._read_source_time()
        self._anchor_t = self._timeline_at(now)
        self._anchor_source_time = now
        self._is_playing = False

    def toggle(self) -> bool:
        """再生/一時停止を切り替え、切り替え後の再生状態を返す。"""

        if self._is_playing:
            self.pause()
        else:
            self.play()
        return self.is_playing

    def reset(self) -> None:
        """再生/一時停止状態を保ったまま `t = 0` へ戻す。"""

        self.seek(0.0)

    def seek(self, t: float) -> None:
        """再生/一時停止状態を保ったまま `t` を移動する。"""

        self.synchronize(t)
        self.mark_discontinuity()

    def synchronize(self, t: float) -> None:
        """epoch を変えず、外部 fixed timeline の現在時刻へ同期する。

        録画中に `RecordingClock` の時刻を toolbar へ mirror するような、
        同一の不連続区間内での同期専用。ユーザーの seek/scrub には `seek()`
        を使い、録画の開始・終了時は別途 `mark_discontinuity()` を呼ぶ。
        """

        target = finite_real(t, name="t")
        self._anchor_t = target
        self._anchor_source_time = self._read_source_time()

    def step_frame(self, *, fps: float = 60.0, frames: int = 1) -> float:
        """一時停止し、`frames / fps` 秒だけ timeline を進める。

        負の `frames` を渡すとフレーム単位で戻せる。
        操作後の `t` を返す。
        """

        frame_rate = finite_real(
            fps,
            name="fps",
            minimum=0.0,
            minimum_inclusive=False,
        )
        frame_count = exact_integer(frames, name="frames")

        self.pause()
        self._anchor_t += frame_count / frame_rate
        self.mark_discontinuity()
        return self._anchor_t

    def mark_discontinuity(self) -> int:
        """録画境界など外部の不連続を記録し、新しい epoch を返す。

        `seek` / `reset` / `step_frame` は自動で呼ぶ。録画の開始・終了など、
        clock 自身が観測できない境界では呼び出し側が明示的に利用する。
        """

        self._epoch += 1
        return self._epoch

    def set_speed(self, speed: float) -> None:
        """`t` の連続性を保ったまま再生倍率を変える。"""

        playback_speed = self._validated_speed(speed)
        if playback_speed == self._speed:
            return

        if self._is_playing:
            now = self._read_source_time()
            self._anchor_t = self._timeline_at(now)
            self._anchor_source_time = now
        self._speed = playback_speed

    def _read_source_time(self) -> float:
        now = finite_real(self._time_source(), name="time_source()")
        now = max(now, self._last_source_time)
        self._last_source_time = now
        return now

    def _timeline_at(self, source_time: float) -> float:
        return (
            self._anchor_t
            + (source_time - self._anchor_source_time) * self._speed
        )

    @staticmethod
    def _validated_speed(speed: float) -> float:
        return finite_real(
            speed,
            name="speed",
            minimum=0.0,
            minimum_inclusive=False,
        )


class RecordingClock:
    """録画タイムラインのフレーム時計。

    Notes
    -----
    `t` は `t0 + frame_index/fps`。
    実時間と切り離し、録画データ側の fps を維持するために使う。
    """

    def __init__(self, *, t0: float, fps: float) -> None:
        self._t0 = finite_real(t0, name="t0")
        self._fps = finite_real(
            fps,
            name="fps",
            minimum=0.0,
            minimum_inclusive=False,
        )
        self._frame_index = 0

    @property
    def fps(self) -> float:
        """録画 fps を返す。"""

        return self._fps

    @property
    def frame_index(self) -> int:
        """現在のフレーム番号（0-based）を返す。"""

        return self._frame_index

    def t(self) -> float:
        """現在のフレーム時刻 `t`（秒）を返す。"""

        return self._t0 + self._frame_index / self._fps

    def tick(self) -> None:
        """フレームを 1 つ進める。"""

        self._frame_index += 1
