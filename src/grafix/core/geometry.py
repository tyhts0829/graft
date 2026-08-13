"""
Purpose:
    配列ではなく、operation・入力・canonical args・exact version を固定した immutable Geometry recipe DAG を表す。
Use when:
    authoring が作る node、GeometryId、DAG serialization、catalog mismatch を変更・調査する場合。
Constraints:
    - GeometryId は exact operation ref、入力 ID、canonical args から推移的に決め、object/process/path identity に依存させない。
    - node 作成後の同名 operation 置換で参照先を変えず、fingerprint 不一致を fallback で隠さない。
    - style と G-code policy を recipe や geometry cache identity に混ぜない。
See:
    architecture.md §4
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import blake2b
from math import isfinite
from struct import Struct
from types import NotImplementedType
from typing import Any, Iterable, Literal, Mapping, Sequence, cast

from grafix.core.operation_declaration import CachePolicy, EvaluationOpRef

GeometryId = str
_GeometryRecord = tuple[
    GeometryId,
    str,
    EvaluationOpRef | None,
    CachePolicy,
    tuple[GeometryId, ...],
    tuple[tuple[str, Any], ...],
]

_GEOMETRY_SIGNATURE_SCHEMA_VERSION = 3
_UINT64 = Struct(">Q")
_FLOAT64 = Struct(">d")
_PACK_UINT64 = _UINT64.pack
_PACK_FLOAT64 = _FLOAT64.pack
_REPR_PERSON = b"grafix.geom.v3r"
_TAG_FLOAT = ord("f")
_TAG_INT = ord("i")
_TAG_NONE = ord("n")
_TAG_STR = ord("s")
_TAG_TUPLE = ord("t")


def _append_frame(buffer: bytearray, payload: bytes) -> None:
    """可変長 payload を長さ付きで buffer へ追加する。"""

    buffer.extend(_PACK_UINT64(len(payload)))
    buffer.extend(payload)


def _append_signature_value(buffer: bytearray, value: Any) -> None:
    """正規化済み値の canonical sort 用表現を buffer へ追加する。

    実行関数へ渡す値そのものは変更せず、mapping key の型と境界を保つ
    byte 表現だけを構築する。
    """

    if value is None:
        buffer.append(_TAG_NONE)
        return

    value_type = type(value)
    if value_type is bool:
        buffer.extend(b"b\x01" if value else b"b\x00")
        return
    if value_type is int:
        buffer.append(_TAG_INT)
        _append_frame(buffer, str(value).encode("ascii"))
        return
    if value_type is float:
        if not isfinite(value):
            raise ValueError("非有限の float は Geometry 引数に使用できない")
        normalized = 0.0 if value == 0.0 else value
        buffer.append(_TAG_FLOAT)
        buffer.extend(_PACK_FLOAT64(normalized))
        return
    if value_type is str:
        buffer.append(_TAG_STR)
        _append_frame(buffer, value.encode("utf-8"))
        return
    if value_type is tuple:
        buffer.append(_TAG_TUPLE)
        buffer.extend(_PACK_UINT64(len(value)))
        for item in value:
            _append_signature_value(buffer, item)
        return
    raise TypeError(f"署名に使用できない値型: {type(value)!r}")


def _encode_signature_value(value: Any) -> bytes:
    """正規化済み値を mapping key の canonical sort 用 bytes に変換する。"""

    buffer = bytearray()
    _append_signature_value(buffer, value)
    return bytes(buffer)


def _normalize_value(value: Any) -> Any:
    """引数値を evaluator が受け取る不変な値へ正規化する。

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
    value_type = type(value)
    if value is None:
        return None
    if value_type is bool or value_type is int or value_type is str:
        return value
    if value_type is float:
        if not isfinite(value):
            raise ValueError("非有限の float は Geometry 引数に使用できない")
        return 0.0 if value == 0.0 else value
    if value_type is tuple or value_type is list:
        return tuple(map(_normalize_value, value))

    if isinstance(value, Enum):
        raise TypeError("Enum は Geometry 引数に使用できない")
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        normalized = float(value)
        if not isfinite(normalized):
            raise ValueError("非有限の float は Geometry 引数に使用できない")
        return 0.0 if normalized == 0.0 else normalized
    if isinstance(value, str):
        return str(value)
    if isinstance(value, (list, tuple)):
        return tuple(map(_normalize_value, value))
    if isinstance(value, Mapping):
        items = [(_normalize_value(k), _normalize_value(v)) for k, v in value.items()]
        items.sort(key=lambda item: _encode_signature_value(item[0]))
        return tuple(items)
    raise TypeError(f"正規化できない引数型: {type(value)!r}")


def normalize_args(params: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    """パラメータ辞書を evaluator 用の不変な引数タプルに変換する。

    Parameters
    ----------
    params : Mapping[str, Any]
        元の引数辞書。

    Returns
    -------
    tuple[tuple[str, Any], ...]
        キーでソートされた (名前, 正規化値) のタプル列。
    """
    if not isinstance(params, Mapping):
        raise TypeError("Geometry 引数は mapping である必要がある")

    names = tuple(params.keys())
    if any(type(name) is not str for name in names):
        raise TypeError("Geometry 引数名は exact str である必要がある")

    items: list[tuple[str, Any]] = []
    for name in sorted(names):
        raw_value = params[name]
        normalized = _normalize_value(raw_value)
        items.append((name, normalized))
    return tuple(items)


def _same_canonical_value(left: Any, right: Any) -> bool:
    """tuple tree の値と型が再帰的に一致するか返す。"""

    if type(left) is not type(right):
        return False
    if type(left) is tuple:
        return len(left) == len(right) and all(
            _same_canonical_value(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    if type(left) is float:
        return left.hex() == right.hex()
    return bool(left == right)


def compute_geometry_id(
    op: str,
    operation: EvaluationOpRef | None,
    inputs: Sequence["Geometry"],
    args: tuple[tuple[str, Any], ...],
) -> GeometryId:
    """GeometryId（内容署名）を計算する。

    Parameters
    ----------
    op : str
        演算子名。
    operation : EvaluationOpRef or None
        primitive/effect node が固定した exact evaluation version。内部 ``concat`` は None。
    inputs : Sequence[Geometry]
        子ノード列。
    args : tuple[tuple[str, Any], ...]
        正規化済み引数タプル。
    Returns
    -------
    GeometryId
        内容署名に基づく ID。
    """
    operation_signature = (
        None
        if operation is None
        else (
            operation.kind,
            operation.name,
            operation.fingerprint.digest,
        )
    )
    signature = (
        _GEOMETRY_SIGNATURE_SCHEMA_VERSION,
        op,
        operation_signature,
        tuple(g.id for g in inputs),
        args,
    )
    # 正規化後の閉じた built-in 型集合では repr が型と境界を保持する。
    # Python 実装の tuple serializer にまとめて任せ、再帰的な bytearray
    # 追記を典型パスから外す。
    payload = repr(signature).encode("utf-8")
    return blake2b(
        payload,
        digest_size=16,
        person=_REPR_PERSON,
    ).hexdigest()


@dataclass(frozen=True, slots=True, eq=False, repr=False, init=False)
class Geometry:
    """幾何レシピを表す不変 Geometry ノード。

    Parameters
    ----------
    op : str
        演算子名。primitive/effect/combine を区別せず保存する。
    operation : EvaluationOpRef or None
        node 作成時に catalog から解決した operation kind/name/evaluation fingerprint。
        内部 ``concat`` は None。
    inputs : tuple[Geometry, ...]
        子ノード列。primitive の場合は空タプル。
    args : tuple[tuple[str, Any], ...]
        正規化済み引数の (名前, 値) タプル列。
    cache_policy : {"content", "none"}
        declaration から固定した評価 cache policy。

    Notes
    -----
    外部入力は :meth:`create` で正規化し、core 内で検証済みの引数だけを
    :meth:`_from_canonical_args` へ渡す。どちらも ``id`` は ``op``、exact
    ``operation`` reference、``inputs``、``args`` の正規化済み内容から必ず計算する。
    """

    id: GeometryId
    op: str
    operation: EvaluationOpRef | None
    inputs: tuple["Geometry", ...]
    args: tuple[tuple[str, Any], ...]
    cache_policy: CachePolicy
    cacheable: bool
    fully_bound: bool
    operation_refs: tuple[EvaluationOpRef, ...]

    def __eq__(self, other: object) -> bool | NotImplementedType:
        """内容署名だけを比較し、深い recipe でも再帰しない。"""

        if not isinstance(other, Geometry):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        """内容署名から非再帰で hash を返す。"""

        return hash(self.id)

    def __repr__(self) -> str:
        """入力の内容署名だけを示す非再帰表現を返す。"""

        input_ids = tuple(item.id for item in self.inputs)
        return (
            f"Geometry(id={self.id!r}, op={self.op!r}, "
            f"operation={self.operation!r}, input_ids={input_ids!r}, "
            f"args={self.args!r})"
        )

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        """DAG を平坦な node record にして深さ非依存で pickle 化する。"""

        records: list[_GeometryRecord] = []
        visited: set[GeometryId] = set()
        stack: list[tuple[Geometry, bool]] = [(self, False)]
        while stack:
            geometry, expanded = stack.pop()
            if expanded:
                records.append(
                    (
                        geometry.id,
                        geometry.op,
                        geometry.operation,
                        geometry.cache_policy,
                        tuple(item.id for item in geometry.inputs),
                        geometry.args,
                    )
                )
                continue
            if geometry.id in visited:
                continue
            visited.add(geometry.id)
            stack.append((geometry, True))
            for item in reversed(geometry.inputs):
                if item.id not in visited:
                    stack.append((item, False))
        return _restore_geometry_dag, (tuple(records), self.id)

    @classmethod
    def create(
        cls,
        op: str,
        *,
        inputs: Sequence["Geometry"] | None = None,
        params: Mapping[str, Any] | None = None,
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
        Returns
        -------
        Geometry
            生成された Geometry ノード。
        """
        inputs_tuple = () if inputs is None else tuple(inputs)
        normalized_args = normalize_args({} if params is None else params)
        operation: EvaluationOpRef | None = None
        cache_policy: CachePolicy = "content"
        if op != "concat":
            # 低水準 create でも、その時点で利用可能な immutable entry があれば
            # exact ref を固定する。未知 op は構造テスト/serialization 用の unbound
            # recipe として作れるが、RealizeSession は評価前に明示的に拒否する。
            from grafix.core.operation_catalog import current_operation_catalog

            kind: Literal["primitive", "effect"] = (
                "effect" if inputs_tuple else "primitive"
            )
            try:
                entry = current_operation_catalog().resolve(kind, op)
            except KeyError:
                pass
            else:
                operation = entry.ref
                cache_policy = entry.cache_policy
        return cls._from_canonical_args(
            op=op,
            operation=operation,
            inputs=inputs_tuple,
            args=normalized_args,
            cache_policy=cache_policy,
        )

    @classmethod
    def _from_canonical_args(
        cls,
        *,
        op: str,
        operation: EvaluationOpRef | None,
        inputs: tuple["Geometry", ...],
        args: tuple[tuple[str, Any], ...],
        cache_policy: CachePolicy,
    ) -> "Geometry":
        """core が検証・正規化済みの recipe から Geometry を一度で生成する。"""

        if type(op) is not str:
            raise TypeError("Geometry op は exact str である必要がある")
        if not op:
            raise ValueError("Geometry op は空にできない")
        if operation is not None and type(operation) is not EvaluationOpRef:
            raise TypeError("Geometry operation は exact EvaluationOpRef または None です")
        if op == "concat":
            if operation is not None:
                raise ValueError("concat Geometry は operation ref を持ちません")
        elif operation is not None:
            expected_kind = "effect" if inputs else "primitive"
            if operation.kind != expected_kind or operation.name != op:
                raise ValueError("Geometry op/input と operation ref が一致しません")
        if type(inputs) is not tuple or any(
            type(item) is not Geometry for item in inputs
        ):
            raise TypeError("Geometry inputs は Geometry の列である必要がある")
        if type(args) is not tuple or any(
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            for item in args
        ):
            raise TypeError("Geometry args は canonical (name, value) tuple が必要です")
        names = tuple(name for name, _value in args)
        if names != tuple(sorted(names)) or len(set(names)) != len(names):
            raise ValueError("Geometry args は名前順かつ重複なしである必要があります")
        if cache_policy not in {"content", "none"}:
            raise ValueError("Geometry cache_policy は 'content' または 'none' です")
        cache_policy = cast(CachePolicy, cache_policy)
        if operation is None and cache_policy != "content":
            raise ValueError("unbound/concat Geometry の cache_policy は 'content' です")
        geometry_id = compute_geometry_id(
            op=op,
            operation=operation,
            inputs=inputs,
            args=args,
        )
        operation_refs = {
            ref
            for item in inputs
            for ref in item.operation_refs
        }
        if operation is not None:
            operation_refs.add(operation)
        sorted_refs = tuple(
            sorted(
                operation_refs,
                key=lambda ref: (ref.kind, ref.name, ref.fingerprint.digest),
            )
        )
        result = object.__new__(cls)
        object.__setattr__(result, "id", geometry_id)
        object.__setattr__(result, "op", op)
        object.__setattr__(result, "operation", operation)
        object.__setattr__(result, "inputs", inputs)
        object.__setattr__(result, "args", args)
        object.__setattr__(result, "cache_policy", cache_policy)
        object.__setattr__(
            result,
            "cacheable",
            cache_policy == "content" and all(item.cacheable for item in inputs),
        )
        object.__setattr__(
            result,
            "fully_bound",
            (op == "concat" or operation is not None)
            and all(item.fully_bound for item in inputs),
        )
        object.__setattr__(result, "operation_refs", sorted_refs)
        return result

    @staticmethod
    def _concat(*geometries: "Geometry") -> "Geometry":
        """少数の Geometry を二分 concat recipe としてまとめる。"""

        if len(geometries) == 1:
            return geometries[0]
        return Geometry._from_canonical_args(
            op="concat",
            operation=None,
            inputs=geometries,
            args=(),
            cache_policy="content",
        )

    @staticmethod
    def _flatten_concat_inputs(
        geometries: Sequence["Geometry"],
    ) -> tuple["Geometry", ...]:
        """共有 concat を境界として、非共有の内部 concat だけを平坦化する。"""

        def is_internal_concat(geometry: Geometry) -> bool:
            return geometry.op == "concat" and not geometry.args

        reference_counts: dict[GeometryId, int] = {}
        for geometry in geometries:
            if is_internal_concat(geometry):
                reference_counts[geometry.id] = (
                    reference_counts.get(geometry.id, 0) + 1
                )

        # 同じ concat が直下に複数回あれば、その部分木は最初から評価境界である。
        # shared doubling の各段で同じ suffix を再走査しないよう、既知の境界は
        # 参照解析の対象にも入れない。
        stack = [
            geometry
            for geometry in geometries
            if (
                is_internal_concat(geometry)
                and reference_counts[geometry.id] == 1
            )
        ]

        visited: set[GeometryId] = set()
        while stack:
            geometry = stack.pop()
            if (
                geometry.id in visited
                or reference_counts.get(geometry.id, 0) > 1
            ):
                continue
            visited.add(geometry.id)
            for item in geometry.inputs:
                if not is_internal_concat(item):
                    continue
                reference_counts[item.id] = reference_counts.get(item.id, 0) + 1
                if item.id not in visited:
                    stack.append(item)

        flattened: list[Geometry] = []
        stack = list(reversed(geometries))
        while stack:
            geometry = stack.pop()
            if (
                is_internal_concat(geometry)
                and reference_counts[geometry.id] == 1
            ):
                stack.extend(reversed(geometry.inputs))
            else:
                flattened.append(geometry)
        return tuple(flattened)

    @classmethod
    def concat(cls, geometries: Iterable["Geometry"]) -> "Geometry":
        """Geometry 列を一度の走査で concat recipe にまとめる。

        Parameters
        ----------
        geometries : Iterable[Geometry]
            出力順に連結する Geometry 列。共有されていない引数なしの内部
            concat は反復的に平坦化する。

        Returns
        -------
        Geometry
            空列では空の concat、1 要素では元の Geometry、それ以外では平坦な
            concat recipe。複数箇所から参照される concat は、DAG を指数的に
            展開しないため評価境界として残す。

        Raises
        ------
        TypeError
            Geometry 以外の要素が含まれる場合。
        """

        roots: list[Geometry] = []
        for geometry in geometries:
            if not isinstance(geometry, Geometry):
                raise TypeError("concat の要素は Geometry である必要がある")
            roots.append(geometry)

        if not roots:
            return cls.create(op="concat")
        if len(roots) == 1:
            return roots[0]

        inputs = cls._flatten_concat_inputs(roots)
        if len(inputs) == 1:
            return inputs[0]
        return cls.create(op="concat", inputs=inputs)

    def __add__(self, other: object) -> "Geometry | NotImplementedType":
        """`g1 + g2` を `concat` として表現する。"""
        if not isinstance(other, Geometry):
            return NotImplemented
        return Geometry._concat(self, other)

    def __radd__(self, other: object) -> "Geometry | NotImplementedType":
        """`sum([...])` のために `0 + Geometry` を許可する。"""
        if other == 0:
            return self
        if not isinstance(other, Geometry):
            return NotImplemented
        return Geometry._concat(other, self)


def _restore_geometry_dag(
    records: tuple[_GeometryRecord, ...],
    root_id: GeometryId,
) -> Geometry:
    """pickle record を検証し、内容から ID を再計算して DAG を復元する。"""

    if type(records) is not tuple:
        raise TypeError("Geometry pickle records は tuple である必要がある")
    if type(root_id) is not str or not root_id:
        raise ValueError("Geometry pickle root_id が不正です")

    geometries: dict[GeometryId, Geometry] = {}
    for record_index, record in enumerate(records):
        if type(record) is not tuple or len(record) != 6:
            raise ValueError(
                f"Geometry pickle record[{record_index}] は6要素tupleである必要がある"
            )
        geometry_id, op, operation, cache_policy, input_ids, args = record
        if type(geometry_id) is not str or not geometry_id:
            raise ValueError(f"Geometry pickle record[{record_index}] の id が不正です")
        if geometry_id in geometries:
            raise ValueError(f"Geometry pickle に重複 id があります: {geometry_id!r}")
        if type(op) is not str or not op:
            raise ValueError(f"Geometry pickle record[{record_index}] の op が不正です")
        if operation is not None and type(operation) is not EvaluationOpRef:
            raise ValueError(
                f"Geometry pickle record[{record_index}] の operation が不正です"
            )
        if cache_policy not in {"content", "none"}:
            raise ValueError(
                f"Geometry pickle record[{record_index}] の cache_policy が不正です"
            )
        if type(input_ids) is not tuple or any(
            type(input_id) is not str or not input_id for input_id in input_ids
        ):
            raise ValueError(
                f"Geometry pickle record[{record_index}] の input_ids が不正です"
            )
        missing_inputs = tuple(
            input_id for input_id in input_ids if input_id not in geometries
        )
        if missing_inputs:
            raise ValueError(
                f"Geometry pickle record[{record_index}] が未知の input id を参照しています: "
                f"{missing_inputs!r}"
            )
        if type(args) is not tuple:
            raise ValueError(f"Geometry pickle record[{record_index}] の args が不正です")

        params: dict[str, Any] = {}
        for arg_index, item in enumerate(args):
            if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str:
                raise ValueError(
                    f"Geometry pickle record[{record_index}] の args[{arg_index}] が不正です"
                )
            name, value = item
            if name in params:
                raise ValueError(
                    f"Geometry pickle record[{record_index}] に重複 arg があります: {name!r}"
                )
            params[name] = value

        rebuilt = Geometry._from_canonical_args(
            op=op,
            operation=operation,
            inputs=tuple(geometries[input_id] for input_id in input_ids),
            args=normalize_args(params),
            cache_policy=cast(CachePolicy, cache_policy),
        )
        if not _same_canonical_value(rebuilt.args, args):
            raise ValueError(
                f"Geometry pickle record[{record_index}] の args は canonical ではありません"
            )
        if rebuilt.id != geometry_id:
            raise ValueError(
                f"Geometry pickle record[{record_index}] の id が内容と一致しません: "
                f"expected={rebuilt.id!r}, got={geometry_id!r}"
            )
        geometries[geometry_id] = rebuilt

    try:
        return geometries[root_id]
    except KeyError:
        raise ValueError(f"Geometry pickle root id が見つかりません: {root_id!r}") from None
