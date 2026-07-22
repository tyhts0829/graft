"""Font asset identity と session-owned outline resource を提供する。"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Generic, TypeVar

import numpy as np

from grafix.core.evaluation_config import EvaluationConfig
from grafix.core.font_resolver import resolve_font_path
from grafix.core.value_validation import exact_integer

_DEFAULT_MAX_ASSETS = 8
_DEFAULT_MAX_ASSET_BYTES = 64 * 1024 * 1024
_DEFAULT_MAX_FONTS = 8
_DEFAULT_MAX_GLYPH_COMMANDS = 4096
_DEFAULT_MAX_GLYPH_POLYLINES = 256
_DEFAULT_MAX_GLYPH_POLYLINE_BYTES = 32 * 1024 * 1024
_DIGEST_LENGTH = 64

_KeyT = TypeVar("_KeyT", bound=Hashable)
_ValueT = TypeVar("_ValueT")


@dataclass(frozen=True, slots=True, order=True)
class FontFileStat:
    """Font asset の変更検知に使う file stat。"""

    size: int
    modified_ns: int
    changed_ns: int
    device: int
    inode: int

    @classmethod
    def from_os_stat(cls, value: os.stat_result) -> FontFileStat:
        """``os.stat_result`` の identity 関連 field を固定する。"""

        return cls(
            size=int(value.st_size),
            modified_ns=int(value.st_mtime_ns),
            changed_ns=int(value.st_ctime_ns),
            device=int(value.st_dev),
            inode=int(value.st_ino),
        )

    def canonical_value(self) -> tuple[int, int, int, int, int]:
        """external dependency digest 用の immutable tuple を返す。"""

        return (
            self.size,
            self.modified_ns,
            self.changed_ns,
            self.device,
            self.inode,
        )


@dataclass(frozen=True, slots=True, order=True)
class FontAssetFingerprint:
    """一つの font face と、その解決時点の file 内容を表す identity。"""

    canonical_path: str
    face_index: int
    stat: FontFileStat
    content_digest: str

    def __post_init__(self) -> None:
        if type(self.canonical_path) is not str or not self.canonical_path:
            raise TypeError("canonical_path は空でない str です")
        if type(self.face_index) is not int or self.face_index < 0:
            raise ValueError("face_index は 0 以上の int です")
        if type(self.stat) is not FontFileStat:
            raise TypeError("stat は exact FontFileStat です")
        digest = self.content_digest
        if (
            type(digest) is not str
            or len(digest) != _DIGEST_LENGTH
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("content_digest は SHA-256 lowercase hex 文字列です")

    def canonical_value(self) -> tuple[object, ...]:
        """external dependency digest 用の型付き immutable tuple を返す。"""

        return (
            "grafix.font-asset.v1",
            self.canonical_path,
            self.face_index,
            self.stat.canonical_value(),
            self.content_digest,
        )


@dataclass(frozen=True, slots=True, eq=False)
class ResolvedFontLease:
    """fingerprint と同じ file bytes を evaluator へ渡す immutable lease。"""

    fingerprint: FontAssetFingerprint
    data: bytes = field(repr=False)
    renderer: TextRenderer = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.fingerprint) is not FontAssetFingerprint:
            raise TypeError("fingerprint は exact FontAssetFingerprint です")
        if type(self.data) is not bytes:
            raise TypeError("data は exact bytes です")
        if type(self.renderer) is not TextRenderer:
            raise TypeError("renderer は exact TextRenderer です")
        if len(self.data) != self.fingerprint.stat.size:
            raise ValueError("lease bytes と fingerprint の file size が一致しません")
        if hashlib.sha256(self.data).hexdigest() != self.fingerprint.content_digest:
            raise ValueError("lease bytes と fingerprint の content digest が一致しません")

    @property
    def path(self) -> Path:
        """診断表示用の canonical path を返す。"""

        return Path(self.fingerprint.canonical_path)


class _BoundedLru(Generic[_KeyT, _ValueT]):
    """entry/byte 上限と eviction callback を持つ小さな LRU。"""

    __slots__ = (
        "_byte_size",
        "_entries",
        "_on_evict",
        "_size_of",
        "maxbytes",
        "maxsize",
    )

    def __init__(
        self,
        *,
        maxsize: int,
        maxbytes: int | None = None,
        size_of: Callable[[_ValueT], int] | None = None,
        on_evict: Callable[[_ValueT], None] | None = None,
    ) -> None:
        self.maxsize = exact_integer(maxsize, name="maxsize", minimum=1)
        self.maxbytes = (
            None if maxbytes is None else exact_integer(maxbytes, name="maxbytes", minimum=1)
        )
        self._size_of = size_of
        self._on_evict = on_evict
        self._entries: OrderedDict[_KeyT, _ValueT] = OrderedDict()
        self._byte_size = 0

    def get(self, key: _KeyT) -> _ValueT | None:
        value = self._entries.get(key)
        if value is not None:
            self._entries.move_to_end(key)
        return value

    def set(self, key: _KeyT, value: _ValueT) -> None:
        evicted: list[_ValueT] = []
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._byte_size -= self._value_size(previous)
            evicted.append(previous)
        self._entries[key] = value
        self._byte_size += self._value_size(value)
        while len(self._entries) > self.maxsize or (
            self.maxbytes is not None and self._byte_size > self.maxbytes
        ):
            _old_key, old_value = self._entries.popitem(last=False)
            self._byte_size -= self._value_size(old_value)
            evicted.append(old_value)
        self._evict_all(evicted)

    def clear(self) -> None:
        values = tuple(self._entries.values())
        self._entries.clear()
        self._byte_size = 0
        self._evict_all(values)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def byte_size(self) -> int:
        return self._byte_size

    def _value_size(self, value: _ValueT) -> int:
        return 0 if self._size_of is None else max(0, int(self._size_of(value)))

    def _evict(self, value: _ValueT) -> None:
        if self._on_evict is not None:
            self._on_evict(value)

    def _evict_all(self, values: Iterable[_ValueT]) -> None:
        first_error: BaseException | None = None
        for value in values:
            try:
                self._evict(value)
            except BaseException as error:  # cleanup 後に最初の失敗を保持する
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error


@dataclass(slots=True)
class _OpenFont:
    """TTFont と、その入力 stream の close ownership を束ねる。"""

    font: Any
    stream: BytesIO

    def close(self) -> None:
        try:
            self.font.close()
        finally:
            self.stream.close()


@dataclass(frozen=True, slots=True)
class _TextRendererStats:
    fonts: int
    glyph_commands: int
    glyph_polylines: int
    glyph_polyline_bytes: int


def _glyph_polyline_bytes(value: tuple[np.ndarray, ...]) -> int:
    return sum(int(polyline.nbytes) for polyline in value)


def _glyph_commands_to_polylines_font_units(
    glyph_commands: Iterable[tuple[str, tuple]],
) -> tuple[np.ndarray, ...]:
    """RecordingPen command を配置前の read-only font-unit 輪郭へ変換する。"""

    polylines: list[np.ndarray] = []
    current: list[list[float]] = []

    def flush(*, close: bool) -> None:
        nonlocal current
        if not current:
            return
        if close and len(current) > 1 and current[0] != current[-1]:
            current.append(current[0].copy())
        array = np.asarray(current, dtype=np.float32)
        array[:, 1] *= -1.0
        array.setflags(write=False)
        polylines.append(array)
        current = []

    for command, arguments in glyph_commands:
        if command == "moveTo":
            flush(close=False)
            x, y = arguments[0]
            current.append([float(x), float(y)])
        elif command == "lineTo":
            x, y = arguments[0]
            current.append([float(x), float(y)])
        elif command == "closePath":
            flush(close=True)
    flush(close=False)
    return tuple(polylines)


class TextRenderer:
    """一 session の TTFont・glyph outline を bounded に再利用する。"""

    __slots__ = ("_closed", "_fonts", "_glyph_commands", "_glyph_polylines", "_lock")

    def __init__(
        self,
        *,
        max_fonts: int = _DEFAULT_MAX_FONTS,
        max_glyph_commands: int = _DEFAULT_MAX_GLYPH_COMMANDS,
        max_glyph_polylines: int = _DEFAULT_MAX_GLYPH_POLYLINES,
        max_glyph_polyline_bytes: int = _DEFAULT_MAX_GLYPH_POLYLINE_BYTES,
    ) -> None:
        self._lock = threading.RLock()
        self._closed = False
        self._fonts = _BoundedLru[FontAssetFingerprint, _OpenFont](
            maxsize=max_fonts,
            on_evict=lambda opened: opened.close(),
        )
        self._glyph_commands = _BoundedLru[tuple[FontAssetFingerprint, str, float], tuple](
            maxsize=max_glyph_commands
        )
        self._glyph_polylines = _BoundedLru[
            tuple[FontAssetFingerprint, str, float], tuple[np.ndarray, ...]
        ](
            maxsize=max_glyph_polylines,
            maxbytes=max_glyph_polyline_bytes,
            size_of=_glyph_polyline_bytes,
        )

    def __enter__(self) -> TextRenderer:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("close 済みの TextRenderer は使用できません")

    def get_font(self, lease: ResolvedFontLease) -> Any:
        """lease bytes から TTFont を取得し、path は再 open しない。"""

        if type(lease) is not ResolvedFontLease:
            raise TypeError("lease は exact ResolvedFontLease です")
        with self._lock:
            self._ensure_open()
            cached = self._fonts.get(lease.fingerprint)
            if cached is not None:
                return cached.font

            from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

            stream = BytesIO(lease.data)
            kwargs: dict[str, int] = {}
            if lease.path.suffix.lower() == ".ttc":
                kwargs["fontNumber"] = lease.fingerprint.face_index
            try:
                font = TTFont(stream, lazy=False, **kwargs)
            except BaseException:
                stream.close()
                raise
            opened = _OpenFont(font=font, stream=stream)
            self._fonts.set(lease.fingerprint, opened)
            return font

    def get_glyph_commands(
        self,
        *,
        char: str,
        lease: ResolvedFontLease,
        flat_seg_len_units: float,
        tt_font: Any | None = None,
        cmap: Any | None = None,
    ) -> tuple:
        """平坦化済みの RecordingPen 互換 command を返す。"""

        if type(char) is not str or len(char) != 1:
            raise ValueError("char は1文字の str です")
        if type(lease) is not ResolvedFontLease:
            raise TypeError("lease は exact ResolvedFontLease です")
        segment_length = round(float(flat_seg_len_units), 6)
        key = (lease.fingerprint, char, segment_length)
        with self._lock:
            self._ensure_open()
            cached = self._glyph_commands.get(key)
            if cached is not None:
                return cached

            from fontTools.pens.recordingPen import (  # type: ignore[import-untyped]
                DecomposingRecordingPen,
            )

            from grafix.core.primitives._text_flatten import flatten_recording

            font = self.get_font(lease) if tt_font is None else tt_font
            cmap_value = font.getBestCmap() if cmap is None else cmap
            if cmap_value is None:
                self._glyph_commands.set(key, ())
                return ()

            glyph_name = cmap_value.get(ord(char))
            if glyph_name is None:
                if char.isascii() and char.isprintable():
                    glyph_name = char
                else:
                    self._glyph_commands.set(key, ())
                    return ()

            glyph_set = font.getGlyphSet()
            glyph = glyph_set.get(glyph_name)
            if glyph is None:
                self._glyph_commands.set(key, ())
                return ()

            recording = DecomposingRecordingPen(glyph_set, reverseFlipped=True)
            try:
                glyph.draw(recording)
            except recording.MissingComponentError:  # type: ignore[attr-defined]
                self._glyph_commands.set(key, ())
                return ()

            result = flatten_recording(
                recording,
                approximate_segment_length=int(round(float(flat_seg_len_units))),
            )
            self._glyph_commands.set(key, result)
            return result

    def get_glyph_polylines(
        self,
        *,
        char: str,
        lease: ResolvedFontLease,
        flat_seg_len_units: float,
        tt_font: Any,
        cmap: Any,
    ) -> tuple[np.ndarray, ...]:
        """配置前の glyph 輪郭を read-only font-unit 座標で返す。"""

        segment_length = round(float(flat_seg_len_units), 6)
        key = (lease.fingerprint, char, segment_length)
        with self._lock:
            self._ensure_open()
            cached = self._glyph_polylines.get(key)
            if cached is not None:
                return cached
            commands = self.get_glyph_commands(
                char=char,
                lease=lease,
                flat_seg_len_units=flat_seg_len_units,
                tt_font=tt_font,
                cmap=cmap,
            )
            polylines = _glyph_commands_to_polylines_font_units(commands)
            self._glyph_polylines.set(key, polylines)
            return polylines

    def stats(self) -> _TextRendererStats:
        """現在の resource 数を返す。"""

        with self._lock:
            return _TextRendererStats(
                fonts=len(self._fonts),
                glyph_commands=len(self._glyph_commands),
                glyph_polylines=len(self._glyph_polylines),
                glyph_polyline_bytes=self._glyph_polylines.byte_size,
            )

    def clear(self) -> None:
        """全 TTFont を close し、glyph cache を空にする。"""

        with self._lock:
            if self._closed:
                return
            self._glyph_polylines.clear()
            self._glyph_commands.clear()
            self._fonts.clear()

    def close(self) -> None:
        """全 resource を解放し、以後の利用を禁止する。"""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._glyph_polylines.clear()
            self._glyph_commands.clear()
            self._fonts.clear()


@dataclass(frozen=True, slots=True)
class FontResourceStats:
    """FontResources の bounded state snapshot。"""

    assets: int
    asset_bytes: int
    fonts: int
    glyph_commands: int
    glyph_polylines: int
    glyph_polyline_bytes: int


class FontResources:
    """一 evaluation owner の font lease と outline cache を所有する。"""

    __slots__ = ("_assets", "_closed", "_lock", "_renderer")

    def __init__(
        self,
        *,
        max_assets: int = _DEFAULT_MAX_ASSETS,
        max_asset_bytes: int = _DEFAULT_MAX_ASSET_BYTES,
        max_fonts: int = _DEFAULT_MAX_FONTS,
        max_glyph_commands: int = _DEFAULT_MAX_GLYPH_COMMANDS,
        max_glyph_polylines: int = _DEFAULT_MAX_GLYPH_POLYLINES,
        max_glyph_polyline_bytes: int = _DEFAULT_MAX_GLYPH_POLYLINE_BYTES,
    ) -> None:
        self._lock = threading.RLock()
        self._closed = False
        self._assets = _BoundedLru[tuple[str, int, FontFileStat], ResolvedFontLease](
            maxsize=max_assets,
            maxbytes=max_asset_bytes,
            size_of=lambda lease: len(lease.data),
        )
        self._renderer = TextRenderer(
            max_fonts=max_fonts,
            max_glyph_commands=max_glyph_commands,
            max_glyph_polylines=max_glyph_polylines,
            max_glyph_polyline_bytes=max_glyph_polyline_bytes,
        )

    def __enter__(self) -> FontResources:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("close 済みの FontResources は使用できません")

    @property
    def renderer(self) -> TextRenderer:
        """この owner に属する TextRenderer instance を返す。"""

        with self._lock:
            self._ensure_open()
            return self._renderer

    def resolve(
        self,
        font: str,
        face_index: int,
        *,
        config: EvaluationConfig,
    ) -> ResolvedFontLease:
        """現在の探索優先順を再評価し、内容一致時だけ lease を再利用する。"""

        if type(config) is not EvaluationConfig:
            raise TypeError("config は exact EvaluationConfig です")
        index = exact_integer(face_index, name="face_index", minimum=0)
        with self._lock:
            self._ensure_open()
        path = resolve_font_path(font, config=config)
        canonical_path = path.as_posix()
        stat = FontFileStat.from_os_stat(path.stat())
        key = (canonical_path, index, stat)
        with self._lock:
            self._ensure_open()
            cached = self._assets.get(key)
            if cached is not None:
                return cached

        with path.open("rb") as stream:
            data = stream.read()
            opened_stat = FontFileStat.from_os_stat(os.fstat(stream.fileno()))
        fingerprint = FontAssetFingerprint(
            canonical_path=canonical_path,
            face_index=index,
            stat=opened_stat,
            content_digest=hashlib.sha256(data).hexdigest(),
        )
        lease = ResolvedFontLease(
            fingerprint=fingerprint,
            data=data,
            renderer=self._renderer,
        )
        opened_key = (canonical_path, index, opened_stat)
        with self._lock:
            self._ensure_open()
            cached = self._assets.get(opened_key)
            if cached is not None:
                return cached
            self._assets.set(opened_key, lease)
        return lease

    def stats(self) -> FontResourceStats:
        """asset/font/glyph resource 数と byte 数を返す。"""

        with self._lock:
            renderer_stats = self._renderer.stats()
            return FontResourceStats(
                assets=len(self._assets),
                asset_bytes=self._assets.byte_size,
                fonts=renderer_stats.fonts,
                glyph_commands=renderer_stats.glyph_commands,
                glyph_polylines=renderer_stats.glyph_polylines,
                glyph_polyline_bytes=renderer_stats.glyph_polyline_bytes,
            )

    def clear(self) -> None:
        """TTFont/glyph/lease を解放する。close 後は何もしない。"""

        with self._lock:
            if self._closed:
                return
            try:
                self._renderer.clear()
            finally:
                self._assets.clear()

    def close(self) -> None:
        """所有 resource を一度だけ解放し、以後の利用を禁止する。"""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._renderer.close()
            finally:
                self._assets.clear()


__all__ = [
    "FontAssetFingerprint",
    "FontFileStat",
    "FontResourceStats",
    "FontResources",
    "ResolvedFontLease",
    "TextRenderer",
]
