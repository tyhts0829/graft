# src/core/geometry.py
# Graft コアの Geometry ノード定義。
# 幾何レシピ DAG の中核モデルと署名生成を実装する。

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import blake2b
from math import isfinite
from typing import Any, Mapping, Sequence, Tuple

GeometryId = str

DEFAULT_SCHEMA_VERSION = 1
DEFAULT_QUANT_STEP = 1e-3


def _canonicalize_value(value: Any) -> Any:
    """引数値を内容署名用に正規化する。

    Parameters
    ----------
    value : Any
        元の値。

    Returns
    -------
    Any
        正規化済み値。

    Raises
    ------
    TypeError
        サポートされない型が渡された場合。
    ValueError
        float の値が NaN/inf の場合。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        v = float(value)
        if not isfinite(v):
            raise ValueError("非有限の float は Geometry 引数に使用できない")
        if v == 0.0:
            v = 0.0
        if isinstance(value, int):
            return int(v)
        return v
    if isinstance(value, str):
        return value
    if isinstance(value, Enum):
        return f"{value.__class__.__name__}.{value.name}"
    if isinstance(value, (list, tuple)):
        return tuple(_canonicalize_value(v) for v in value)
    if isinstance(value, dict):
        return tuple(
            (str(k), _canonicalize_value(v))
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        )
    raise TypeError(f"正規化できない引数型: {type(value)!r}")


def canonicalize_args(params: Mapping[str, Any]) -> Tuple[Tuple[str, Any], ...]:
    """パラメータ辞書を Geometry 用の正規化済み引数タプルに変換する。

    Parameters
    ----------
    params : Mapping[str, Any]
        元の引数辞書。

    Returns
    -------
    tuple[tuple[str, Any], ...]
        キーでソートされた (名前, 正規化値) のタプル列。
    """
    items: list[tuple[str, Any]] = []
    for name in sorted(params.keys()):
        raw_value = params[name]
        canonical = _canonicalize_value(raw_value)
        items.append((str(name), canonical))
    return tuple(items)


def _update_hash_with_value(hasher: blake2b, value: Any) -> None:
    """正規化済み値をハッシュに追加する。

    Parameters
    ----------
    hasher : blake2b
        ハッシュオブジェクト。
    value : Any
        正規化済み値。
    """
    if value is None:
        hasher.update(b"n")
        return
    if isinstance(value, bool):
        hasher.update(b"b1" if value else b"b0")
        return
    if isinstance(value, (int, float)):
        hasher.update(b"f")
        hasher.update(f"{value:.17g}".encode("ascii"))
        return
    if isinstance(value, str):
        hasher.update(b"s")
        hasher.update(value.encode("utf-8"))
        return
    if isinstance(value, tuple):
        hasher.update(b"t[")
        for item in value:
            _update_hash_with_value(hasher, item)
            hasher.update(b",")
        hasher.update(b"]")
        return
    raise TypeError(f"署名に使用できない値型: {type(value)!r}")


def compute_geometry_id(
    op: str,
    inputs: Sequence["Geometry"],
    args: Tuple[Tuple[str, Any], ...],
    *,
    schema_version: int = DEFAULT_SCHEMA_VERSION,
) -> GeometryId:
    """GeometryId（内容署名）を計算する。

    Parameters
    ----------
    op : str
        演算子名。
    inputs : Sequence[Geometry]
        子ノード列。
    args : tuple[tuple[str, Any], ...]
        正規化済み引数タプル。
    schema_version : int, optional
        署名スキーマのバージョン。

    Returns
    -------
    GeometryId
        内容署名に基づく ID。
    """
    h = blake2b(digest_size=16)
    h.update(f"v{schema_version}".encode("ascii"))
    h.update(b"|op:")
    h.update(op.encode("utf-8"))

    h.update(b"|inputs:")
    for g in inputs:
        h.update(b"#")
        h.update(g.id.encode("ascii"))

    h.update(b"|args:")
    for name, value in args:
        h.update(b"k:")
        h.update(name.encode("utf-8"))
        h.update(b"=")
        _update_hash_with_value(h, value)

    return h.hexdigest()


@dataclass(frozen=True, slots=True)
class Geometry:
    """幾何レシピを表す不変 Geometry ノード。

    Parameters
    ----------
    id : GeometryId
        内容署名に基づく GeometryId。
    op : str
        演算子名。primitive/effect/combine を区別せず保存する。
    inputs : tuple[Geometry, ...]
        子ノード列。primitive の場合は空タプル。
    args : tuple[tuple[str, Any], ...]
        正規化済み引数の (名前, 値) タプル列。

    Notes
    -----
    インスタンスは不変とし、内容が同じであれば同じ id になる設計とする。
    """

    id: GeometryId
    op: str
    inputs: tuple["Geometry", ...]
    args: Tuple[Tuple[str, Any], ...]

    @classmethod
    def create(
        cls,
        op: str,
        *,
        inputs: Sequence["Geometry"] | None = None,
        params: Mapping[str, Any] | None = None,
        schema_version: int = DEFAULT_SCHEMA_VERSION,
    ) -> "Geometry":
        """演算子名とパラメータから Geometry ノードを生成する。

        Parameters
        ----------
        op : str
            演算子名。
        inputs : Sequence[Geometry] or None, optional
            子ノード列。省略時は空とみなす。
        params : Mapping[str, Any] or None, optional
            元の引数辞書。None の場合は空辞書とみなす。
        schema_version : int, optional
            署名スキーマのバージョン。

        Returns
        -------
        Geometry
            生成された Geometry ノード。
        """
        if inputs is None:
            inputs_seq: Sequence["Geometry"] = ()
        else:
            inputs_seq = inputs
        if params is None:
            params = {}

        normalized_args = canonicalize_args(
            params,
        )
        inputs_tuple = tuple(inputs_seq)
        geometry_id = compute_geometry_id(
            op=op,
            inputs=inputs_tuple,
            args=normalized_args,
            schema_version=schema_version,
        )
        return cls(
            id=geometry_id,
            op=op,
            inputs=inputs_tuple,
            args=normalized_args,
        )
