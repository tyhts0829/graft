"""operation catalog の 1 entry を CLI で表示する。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping

from grafix.api.effects import E
from grafix.api.operation_info import OperationInfo
from grafix.api.primitives import G
from grafix.core.parameters.meta import ParamMeta


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m grafix describe")
    parser.add_argument("kind", choices=("primitive", "effect"), help="operation 種別")
    parser.add_argument("name", help="operation 名")
    return parser.parse_args(argv)


def _format_meta(meta: ParamMeta) -> str:
    parts = [f"kind={meta.kind!r}"]
    if meta.description is not None:
        parts.append(f"description={meta.description!r}")
    if meta.ui_min is not None:
        parts.append(f"ui_min={meta.ui_min!r}")
    if meta.ui_max is not None:
        parts.append(f"ui_max={meta.ui_max!r}")
    if meta.choices is not None:
        parts.append(f"choices={tuple(meta.choices)!r}")
    return ", ".join(parts)


def _print_mapping(title: str, values: Mapping[str, object]) -> None:
    print(f"{title}:")
    if not values:
        print("  -")
        return
    for name, value in values.items():
        print(f"  {name}: {value!r}")


def _print_entry(entry: OperationInfo) -> None:
    print(f"name: {entry.name}")
    print(f"kind: {entry.kind}")
    print(f"n_inputs: {entry.n_inputs}")
    print(f"description: {entry.description}")
    print(f"source: {entry.source or '-'}")
    print(f"provenance: {entry.provenance or '-'}")
    print(f"accepted_args: {', '.join(entry.accepted_args) or '-'}")
    print(f"required_args: {', '.join(entry.required_args) or '-'}")
    _print_mapping("defaults", entry.defaults)
    print("meta:")
    if not entry.meta:
        print("  -")
    else:
        for name, meta in entry.meta.items():
            print(f"  {name}: {_format_meta(meta)}")
    print("doc:")
    if not entry.doc:
        print("  -")
    else:
        for line in entry.doc.splitlines():
            print(f"  {line}")


def main(argv: list[str] | None = None) -> int:
    """CLI 引数を解釈し、current catalog の operation 情報を表示する。"""

    if argv is None:
        argv = sys.argv[1:]
    args = _parse_args(argv)

    try:
        entry: OperationInfo
        if args.kind == "primitive":
            entry = G.describe(args.name)
        else:
            entry = E.describe(args.name)
    except KeyError as exc:
        print(str(exc.args[0]), file=sys.stderr)
        return 2

    _print_entry(entry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
