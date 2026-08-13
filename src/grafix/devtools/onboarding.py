"""
Purpose:
    最小 Grafix project の初期化と、package に同梱した example の一覧・安全なコピーを提供する。
Use when:
    ``grafix init``/``grafix examples``、project template、packaged example の discovery/copy policy を変更・調査する場合。
Constraints:
    - project template と example は既存 file を上書きせず、新規 file だけを排他的に作成する。
    - example の正本は package resource に留め、一覧 description は各 source の module docstring 先頭行から取得する。
    - 初期化結果は created/existing を区別し、既存 project を暗黙に migration しない。
Side effects:
    directory と template/example file を作成し、package resource を読み込む。
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal

FileCreationStatus = Literal["created", "exists"]


@dataclass(frozen=True, slots=True)
class FileCreation:
    """project 初期化で扱った 1 ファイルの結果。"""

    path: Path
    status: FileCreationStatus


@dataclass(frozen=True, slots=True)
class ProjectInitResult:
    """project 初期化結果。"""

    root: Path
    files: tuple[FileCreation, ...]

    @property
    def created(self) -> tuple[Path, ...]:
        """新規作成したファイルを返す。"""

        return tuple(item.path for item in self.files if item.status == "created")

    @property
    def existing(self) -> tuple[Path, ...]:
        """既存のため変更しなかったファイルを返す。"""

        return tuple(item.path for item in self.files if item.status == "exists")


@dataclass(frozen=True, slots=True)
class BundledExample:
    """コピー可能な同梱 example の情報。"""

    name: str
    filename: str
    description: str


_SKETCH_MAIN = '''"""Grafix の最小スケッチ。"""

from grafix import G, SQUARE, run


def draw(t: float):
    """時刻 ``t`` の scene を返す。"""

    _ = t
    return G.circle(radius=90.0, center=(150.0, 150.0, 0.0))


if __name__ == "__main__":
    run(draw, canvas_size=SQUARE)
'''

_CONFIG = """version: 1

paths:
  output_dir: "../data/output"
  sketch_dir: "../sketch"
  preset_module_dirs:
    - "../sketch/presets"
"""


def _project_manifest(project_name: str) -> str:
    return f'''[project]
name = "{project_name}"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["grafix"]

[tool.mypy]
mypy_path = "typings"
'''


def _normalize_project_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-")
    return normalized or "grafix-project"


def _create_text_file(path: Path, content: str) -> FileCreation:
    """UTF-8 file を排他的に作り、既存 file は変更しない。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    except FileExistsError:
        return FileCreation(path=path, status="exists")
    return FileCreation(path=path, status="created")


def init_project(
    root: str | Path,
    *,
    project_name: str | None = None,
) -> ProjectInitResult:
    """最小 Grafix project を既存 file 非上書きで作成する。

    Parameters
    ----------
    root : str or Path
        project root として作成または利用する directory。
    project_name : str or None, optional
        ``pyproject.toml`` に書く名前。省略時は root directory 名から作る。

    Returns
    -------
    ProjectInitResult
        file ごとの作成・既存状態を含む結果。
    """

    root_path = Path(root).expanduser().resolve(strict=False)
    name = _normalize_project_name(project_name or root_path.name)
    (root_path / "sketch/presets").mkdir(parents=True, exist_ok=True)
    templates = (
        (Path("pyproject.toml"), _project_manifest(name)),
        (Path(".grafix/config.yaml"), _CONFIG),
        (Path("sketch/__init__.py"), ""),
        (Path("sketch/main.py"), _SKETCH_MAIN),
    )
    files = tuple(
        _create_text_file(root_path / relative_path, content)
        for relative_path, content in templates
    )
    return ProjectInitResult(root=root_path, files=files)


def _example_description(text: str) -> str:
    try:
        doc = ast.get_docstring(ast.parse(text), clean=True)
    except SyntaxError:
        return ""
    if not doc:
        return ""
    return doc.splitlines()[0].strip()


def _example_root() -> Traversable:
    return resources.files("grafix").joinpath("resource", "examples")


def list_examples() -> tuple[BundledExample, ...]:
    """利用可能な同梱 example を名前順で返す。

    Returns
    -------
    tuple[BundledExample, ...]
        package resource として配布される Python example の一覧。
    """

    examples: list[BundledExample] = []
    for item in _example_root().iterdir():
        if not item.is_file() or not item.name.endswith(".py") or item.name.startswith("_"):
            continue
        text = item.read_text(encoding="utf-8")
        examples.append(
            BundledExample(
                name=Path(item.name).stem,
                filename=item.name,
                description=_example_description(text),
            )
        )
    return tuple(sorted(examples, key=lambda item: item.name))


def copy_example(name: str, destination: str | Path) -> Path:
    """同梱 example を既存 file 非上書きでコピーする。

    Parameters
    ----------
    name : str
        :func:`list_examples` が返す名前。``.py`` suffix は省略できる。
    destination : str or Path
        コピー先 file。

    Returns
    -------
    Path
        作成した file の絶対 path。

    Raises
    ------
    KeyError
        指定名の example が存在しない場合。
    FileExistsError
        コピー先が既に存在する場合。既存内容は変更しない。
    """

    normalized_name = Path(str(name)).stem
    examples = {example.name: example for example in list_examples()}
    try:
        example = examples[normalized_name]
    except KeyError:
        raise KeyError(f"未知の example: {name!r}") from None

    source = _example_root().joinpath(example.filename)
    destination_path = Path(destination).expanduser().resolve(strict=False)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with destination_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(source.read_text(encoding="utf-8"))
    return destination_path


def main_init(argv: list[str] | None = None) -> int:
    """``grafix init`` の CLI を実行する。

    Parameters
    ----------
    argv : list[str] or None, optional
        CLI 引数。None の場合は ``sys.argv`` を使う。

    Returns
    -------
    int
        process exit code。
    """

    parser = argparse.ArgumentParser(prog="python -m grafix init")
    parser.add_argument("path", nargs="?", default=".", help="作成する project directory")
    parser.add_argument("--name", help="pyproject.toml の project 名")
    args = parser.parse_args(argv)

    result = init_project(args.path, project_name=args.name)
    for item in result.files:
        relative = item.path.relative_to(result.root)
        label = "created" if item.status == "created" else "exists"
        print(f"[{label}] {relative}")  # noqa: T201
    return 0


def main_examples(argv: list[str] | None = None) -> int:
    """``grafix examples`` の CLI を実行する。

    Parameters
    ----------
    argv : list[str] or None, optional
        CLI 引数。引数なしは ``list`` と同じ。

    Returns
    -------
    int
        process exit code。
    """

    parser = argparse.ArgumentParser(prog="python -m grafix examples")
    subparsers = parser.add_subparsers(dest="action")
    subparsers.add_parser("list", help="同梱 example を一覧表示する")
    copy_parser = subparsers.add_parser("copy", help="同梱 example をコピーする")
    copy_parser.add_argument("name", help="example 名")
    copy_parser.add_argument("--output", "-o", type=Path, help="コピー先 file")
    args = parser.parse_args(argv)

    if args.action in {None, "list"}:
        for example in list_examples():
            suffix = f" - {example.description}" if example.description else ""
            print(f"{example.name}{suffix}")  # noqa: T201
        return 0

    if args.action == "copy":
        examples = {example.name: example for example in list_examples()}
        normalized_name = Path(str(args.name)).stem
        selected = examples.get(normalized_name)
        if selected is None:
            print(f"未知の example: {args.name!r}", file=sys.stderr)  # noqa: T201
            return 2
        destination = (
            Path("sketch/examples") / selected.filename
            if args.output is None
            else Path(args.output)
        )
        try:
            copied = copy_example(selected.name, destination)
        except FileExistsError:
            print(f"既存 file は上書きしません: {destination}", file=sys.stderr)  # noqa: T201
            return 1
        print(f"Copied {selected.name}: {copied}")  # noqa: T201
        return 0

    raise AssertionError(f"unknown examples action: {args.action!r}")


__all__ = [
    "BundledExample",
    "FileCreation",
    "FileCreationStatus",
    "ProjectInitResult",
    "copy_example",
    "init_project",
    "list_examples",
    "main_examples",
    "main_init",
]
