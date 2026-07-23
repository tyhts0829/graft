"""snapshot source が安全に相対 import できる lexical scope を検証する。"""

from __future__ import annotations

import ast
from pathlib import Path


class SourceImportPolicyError(ImportError):
    """deferred relative import の source location と scope を表す。"""

    def __init__(self, *, path: Path, lineno: int, scope: str) -> None:
        message = (
            f"{path}:{lineno}: {scope} 内の relative import は使用できません。"
            "relative import は module lexical scope に置いてください"
        )
        super().__init__(message)
        self.filename = str(path)
        self.lineno = lineno
        self.scope = scope


class _RelativeImportScopeVisitor(ast.NodeVisitor):
    """module scope を離れた ``ImportFrom`` だけを拒否する。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._scopes: list[str] = []

    def _visit_lexical_scope(self, node: ast.AST, label: str) -> None:
        self._scopes.append(label)
        try:
            self.generic_visit(node)
        finally:
            self._scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_lexical_scope(node, f"function {node.name!r}")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_lexical_scope(node, f"async function {node.name!r}")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_lexical_scope(node, f"class {node.name!r}")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level > 0 and self._scopes:
            raise SourceImportPolicyError(
                path=self._path,
                lineno=node.lineno,
                scope=" > ".join(self._scopes),
            )


def validate_source_import_policy(
    content: bytes,
    *,
    path: Path,
) -> ast.Module:
    """source を parse し、relative import が module scope にだけあると保証する。

    Parameters
    ----------
    content:
        検証する確定済み source bytes。
    path:
        syntax error と policy error に表示する論理 source path。

    Returns
    -------
    ast.Module
        検証済み source の構文木。

    Raises
    ------
    SourceImportPolicyError
        function、async function、class の内側に relative import がある場合。
    SyntaxError
        source を parse できない場合。
    """

    if type(content) is not bytes:
        raise TypeError("content は exact bytes です")
    if not isinstance(path, Path):
        raise TypeError("path は Path です")
    tree = ast.parse(content, filename=str(path))
    _RelativeImportScopeVisitor(path).visit(tree)
    return tree


__all__ = ["SourceImportPolicyError", "validate_source_import_policy"]
