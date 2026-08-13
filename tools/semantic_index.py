#!/usr/bin/env python3
"""
Purpose:
    Python moduleのsemantic headerから、コード本文を含まないrepository indexを生成する。
Use when:
    agent向けの候補ファイル一覧を作る場合や、header形式の不整合を検査する場合。
Constraints:
    - sourceをimportまたは実行せず、ASTのmodule docstringだけを読む。
    - manifestはpath順とし、4つのrouting field以外を含めない。
Side effects:
    `--output`を指定した場合だけJSONファイルを書き込む。
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_ROOTS = (
    _REPOSITORY_ROOT / "src",
    _REPOSITORY_ROOT / "sketch",
    _REPOSITORY_ROOT / "tests",
    _REPOSITORY_ROOT / "tools",
    _REPOSITORY_ROOT / ".agents" / "skills",
)
_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".cache",
        ".git",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "__pycache__",
        "build",
        "dist",
        "generated",
        "output",
        "third_party",
        "vendor",
        "vendored",
    }
)
_EXCLUDED_DIRECTORY_SUFFIXES = (".egg-info",)
_EXCLUDED_REPOSITORY_PREFIXES = (Path("sketch/agent_loop"),)
_REQUIRED_HEADINGS = ("Purpose", "Use when", "Constraints")
_OPTIONAL_HEADINGS = ("Side effects", "See")
_HEADINGS = _REQUIRED_HEADINGS + _OPTIONAL_HEADINGS
_MIN_HEADER_LINES = 5
_MAX_HEADER_LINES = 15


class SemanticIndexError(ValueError):
    """Semantic headerまたはsource treeをindex化できない場合のerror。"""


@dataclass(frozen=True, slots=True)
class SemanticHeader:
    """抽出後のrouting metadata。"""

    purpose: str
    use_when: str
    constraints: tuple[str, ...]

    def manifest_item(self, *, path: str) -> dict[str, object]:
        """JSON manifest用のfield限定recordを返す。"""

        return {
            "path": path,
            "purpose": self.purpose,
            "use_when": self.use_when,
            "constraints": list(self.constraints),
        }


def _module_docstring_node(tree: ast.Module) -> ast.Expr | None:
    if not tree.body:
        return None
    first = tree.body[0]
    if not (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return None
    return first


def _split_sections(docstring: str, *, path: Path) -> dict[str, list[str]] | None:
    lines = docstring.splitlines()
    nonempty = [line.strip() for line in lines if line.strip()]
    present_headings = {line[:-1] for line in nonempty if line.endswith(":")} & set(_HEADINGS)
    if not present_headings:
        return None
    if "Purpose" not in present_headings:
        if present_headings <= set(_OPTIONAL_HEADINGS):
            return None
        joined = ", ".join(f"{heading}:" for heading in sorted(present_headings))
        raise SemanticIndexError(f"{path}: Purpose:を欠くsemantic headerです: {joined}")
    if not nonempty or nonempty[0] != "Purpose:":
        raise SemanticIndexError(f"{path}: semantic headerはPurpose:から開始してください")

    sections: dict[str, list[str]] = {}
    current: str | None = None
    previous_order = -1
    for raw_line in lines:
        stripped = raw_line.strip()
        heading = stripped[:-1] if stripped.endswith(":") else None
        if raw_line == stripped and stripped.endswith(":") and heading not in _HEADINGS:
            raise SemanticIndexError(f"{path}: 未知のsemantic header headingです: {stripped}")
        if heading in _HEADINGS:
            order = _HEADINGS.index(heading)
            if order <= previous_order:
                raise SemanticIndexError(
                    f"{path}: semantic headerのheading順または重複が不正です: {heading}:"
                )
            sections[heading] = []
            current = heading
            previous_order = order
            continue
        if current is None:
            if stripped:
                raise SemanticIndexError(f"{path}: Purpose:より前に本文があります")
            continue
        if stripped:
            sections[current].append(stripped)

    missing = [heading for heading in _REQUIRED_HEADINGS if heading not in sections]
    if missing:
        joined = ", ".join(f"{heading}:" for heading in missing)
        raise SemanticIndexError(f"{path}: semantic headerに必須headingがありません: {joined}")
    empty = [heading for heading, values in sections.items() if not values]
    if empty:
        joined = ", ".join(f"{heading}:" for heading in empty)
        raise SemanticIndexError(f"{path}: semantic headerのsectionが空です: {joined}")
    return sections


def _paragraph(lines: Sequence[str]) -> str:
    return " ".join(line.removeprefix("- ").strip() for line in lines)


def _constraints(lines: Sequence[str], *, path: Path) -> tuple[str, ...]:
    if not any(line.startswith("- ") for line in lines):
        return (_paragraph(lines),)

    items: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("- "):
            if current:
                items.append(_paragraph(current))
            current = [line]
            continue
        if not current:
            raise SemanticIndexError(f"{path}: Constraints:の本文はbulletで統一してください")
        current.append(line)
    if current:
        items.append(_paragraph(current))
    return tuple(items)


def parse_semantic_header(source: str, *, path: Path) -> SemanticHeader | None:
    """Python sourceのmodule docstringからsemantic headerを抽出する。"""

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        location = "" if exc.lineno is None else f":{exc.lineno}"
        raise SemanticIndexError(f"{path}{location}: Python構文を解析できません") from exc

    node = _module_docstring_node(tree)
    if node is None:
        return None
    docstring = ast.get_docstring(tree, clean=True)
    if docstring is None:
        return None
    sections = _split_sections(docstring, path=path)
    if sections is None:
        return None

    end_lineno = node.end_lineno
    if end_lineno is None:
        raise SemanticIndexError(f"{path}: module docstringの終端行を取得できません")
    line_count = end_lineno - node.lineno + 1
    if not _MIN_HEADER_LINES <= line_count <= _MAX_HEADER_LINES:
        raise SemanticIndexError(
            f"{path}: semantic headerは{_MIN_HEADER_LINES}〜{_MAX_HEADER_LINES}行にしてください"
            f"（{line_count}行）"
        )

    return SemanticHeader(
        purpose=_paragraph(sections["Purpose"]),
        use_when=_paragraph(sections["Use when"]),
        constraints=_constraints(sections["Constraints"], path=path),
    )


def _repository_relative(path: Path, *, repository_root: Path) -> Path | None:
    try:
        return path.relative_to(repository_root)
    except ValueError:
        return None


def _is_excluded(path: Path, *, repository_root: Path) -> bool:
    if set(path.parts) & _EXCLUDED_DIRECTORY_NAMES or any(
        part.endswith(_EXCLUDED_DIRECTORY_SUFFIXES) for part in path.parts
    ):
        return True
    relative = _repository_relative(path, repository_root=repository_root)
    if relative is None:
        return False
    return any(
        relative == prefix or prefix in relative.parents for prefix in _EXCLUDED_REPOSITORY_PREFIXES
    )


def iter_python_files(
    roots: Iterable[Path],
    *,
    repository_root: Path,
) -> tuple[Path, ...]:
    """重複を除いたPython sourceを決定的なpath順で返す。"""

    files: set[Path] = set()
    for root in roots:
        resolved = root.expanduser().resolve(strict=False)
        if not resolved.exists():
            raise SemanticIndexError(f"{root}: pathが存在しません")
        candidates = (resolved,) if resolved.is_file() else resolved.rglob("*.py")
        for candidate in candidates:
            if candidate.suffix == ".py" and not _is_excluded(
                candidate,
                repository_root=repository_root,
            ):
                files.add(candidate)
    return tuple(sorted(files, key=lambda path: path.as_posix()))


def build_manifest(
    roots: Iterable[Path],
    *,
    repository_root: Path = _REPOSITORY_ROOT,
) -> list[dict[str, object]]:
    """指定tree内のsemantic headersをpath順manifestへ変換する。"""

    root = repository_root.resolve(strict=False)
    manifest: list[dict[str, object]] = []
    for path in iter_python_files(roots, repository_root=root):
        header = parse_semantic_header(path.read_text(encoding="utf-8"), path=path)
        if header is None:
            continue
        relative = _repository_relative(path, repository_root=root)
        manifest_path = path.as_posix() if relative is None else relative.as_posix()
        manifest.append(header.manifest_item(path=manifest_path))
    manifest.sort(key=lambda item: str(item["path"]))
    return manifest


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Python moduleのsemantic headersからrepository manifestを生成する。",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="走査するfile/directory。省略時はrepositoryの主要Python sourceを走査する。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="JSON出力先。省略時はstdoutへ出力する。",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Semantic index CLIを実行する。"""

    args = _parse_args(argv)
    roots = tuple(args.paths) if args.paths else _DEFAULT_ROOTS
    try:
        manifest = build_manifest(roots)
    except (OSError, UnicodeError, SemanticIndexError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    try:
        if args.output is None:
            sys.stdout.write(payload)
        else:
            args.output.write_text(payload, encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
