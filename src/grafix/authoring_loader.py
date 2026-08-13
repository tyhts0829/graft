"""
Purpose:
    config指定のauthoring sourceを捕捉し、session用のimmutable definitionsへ変換する。
Use when:
    preset module探索、worker recipe、またはconfig-scoped catalog構築を変更する場合。
Constraints:
    - 全candidateをpreflightしてから実行し、失敗時はdefault definitionsを変更しない。
    - source発見とbytes captureをcore valueから分離し、採用snapshotだけをcallerへ返す。
Side effects:
    authoring sourceを読み、一時import transaction内で実行する。
"""

from __future__ import annotations

import hashlib
import importlib
from collections.abc import Callable
from pathlib import Path

from grafix._snapshot_import import (
    SnapshotImportModule,
    SnapshotImportPlan,
    SnapshotModuleSource,
    snapshot_import_context,
)
from grafix._source_import_policy import validate_source_import_policy
from grafix.core.authoring_definitions import (
    AuthoringDefinitionsSnapshot,
    RegistrationTarget,
    default_authoring_definitions,
    registration_scope,
)
from grafix.core.authoring_recipe import (
    AuthoringDefinitionsRecipe,
    AuthoringModuleSource,
    AuthoringSourceRoot,
)
from grafix.core.builtins import builtin_operation_catalog
from grafix.core.operation_catalog import compose_operation_catalogs
from grafix.core.runtime_config import RuntimeConfig

_CANDIDATE_PACKAGE_PREFIX = "_grafix_config_authoring_"
_CANONICAL_CANDIDATE_PACKAGE = "_grafix_config_authoring"


def default_session_authoring_definitions() -> AuthoringDefinitionsSnapshot:
    """builtin と通常 module-scope declaration を一度だけ snapshot する。"""

    authored = default_authoring_definitions.snapshot()
    return AuthoringDefinitionsSnapshot(
        operations=compose_operation_catalogs(
            builtin_operation_catalog(),
            authored.operations,
        ),
        presets=authored.presets,
        recipe=AuthoringDefinitionsRecipe(),
    )


def authoring_definitions_for_draw(
    draw: Callable[..., object],
    *,
    config: RuntimeConfig,
    definitions: AuthoringDefinitionsSnapshot | None = None,
) -> AuthoringDefinitionsSnapshot:
    """明示値、draw generation、config candidate の順で一 snapshot を選ぶ。"""

    if not callable(draw):
        raise TypeError("draw は callable である必要があります")
    if type(config) is not RuntimeConfig:
        raise TypeError("config は exact RuntimeConfig である必要があります")
    if definitions is not None:
        if type(definitions) is not AuthoringDefinitionsSnapshot:
            raise TypeError(
                "definitions は exact AuthoringDefinitionsSnapshot または None です"
            )
        return definitions
    candidate = getattr(draw, "__grafix_authoring_definitions__", None)
    if candidate is None:
        return load_config_authoring_definitions(config)
    if type(candidate) is not AuthoringDefinitionsSnapshot:
        raise TypeError(
            "draw.__grafix_authoring_definitions__ は "
            "exact AuthoringDefinitionsSnapshot です"
        )
    return candidate


def _module_name(package_name: str, relative_path: Path) -> str:
    module_path = (
        relative_path.parent
        if relative_path.name == "__init__.py"
        else relative_path.with_suffix("")
    )
    parts = module_path.parts
    if any(not part.isidentifier() for part in parts):
        raise ValueError(
            f"authoring module path は Python identifier で構成する必要があります: {relative_path}"
        )
    return ".".join((package_name, *parts))


def _candidate_sources(root: Path) -> tuple[AuthoringModuleSource, ...]:
    """root 配下の Python source を bytes snapshot として安定順で返す。"""

    if not root.is_dir():
        return ()
    return tuple(
        AuthoringModuleSource(
            relative_path=path.relative_to(root),
            content=path.read_bytes(),
        )
        for path in sorted(root.rglob("*.py"), key=lambda item: item.relative_to(root).parts)
    )


def _preflight_authoring_recipe(recipe: AuthoringDefinitionsRecipe) -> None:
    """全 source の path/import contract を candidate 実行前に検証する。"""

    for root in recipe.roots:
        for source in root.modules:
            source_path = root.path / source.relative_path
            if source.relative_path == Path("__init__.py"):
                raise ValueError(
                    "authoring source root は synthetic namespace のため "
                    f"root __init__.py を使用できません: {source_path}"
                )
            _module_name(_CANONICAL_CANDIDATE_PACKAGE, source.relative_path)
            validate_source_import_policy(source.content, path=source_path)


def capture_authoring_definitions_recipe(
    config: RuntimeConfig,
) -> AuthoringDefinitionsRecipe:
    """config directory の module bytes を worker-safe recipe へ一度だけ固定する。"""

    if not isinstance(config, RuntimeConfig):
        raise TypeError("config は RuntimeConfig である必要があります")
    roots = tuple(Path(path).resolve(strict=False) for path in config.preset_module_dirs)
    recipe = AuthoringDefinitionsRecipe(
        roots=tuple(
            AuthoringSourceRoot(path=root, modules=_candidate_sources(root))
            for root in roots
        )
    )
    _preflight_authoring_recipe(recipe)
    return recipe


def _candidate_fingerprint(recipe: AuthoringDefinitionsRecipe) -> str:
    """absolute path に依存しない candidate source fingerprint を返す。"""

    digest = hashlib.sha256()
    for root_index, root in enumerate(recipe.roots):
        sources = root.modules
        digest.update(root_index.to_bytes(8, "big"))
        digest.update(len(sources).to_bytes(8, "big"))
        for source in sources:
            relative = source.relative_path.as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(len(source.content).to_bytes(8, "big"))
            digest.update(source.content)
    return digest.hexdigest()


def _candidate_import_plan(
    recipe: AuthoringDefinitionsRecipe,
) -> SnapshotImportPlan:
    fingerprint = _candidate_fingerprint(recipe)
    package_names = tuple(
        f"{_CANDIDATE_PACKAGE_PREFIX}{fingerprint}_{index}"
        for index in range(len(recipe.roots))
    )
    modules: list[SnapshotImportModule] = []
    for package_name, root in zip(package_names, recipe.roots, strict=True):
        for source in root.modules:
            module_name = _module_name(package_name, source.relative_path)
            if module_name == package_name:
                # root package は path 非依存の synthetic namespace として扱う。
                continue
            modules.append(
                SnapshotImportModule(
                    name=module_name,
                    source=SnapshotModuleSource(
                        path=root.path / source.relative_path,
                        content=source.content,
                        is_package=source.is_package,
                        canonical_name=(
                            _CANONICAL_CANDIDATE_PACKAGE
                            + module_name.removeprefix(package_name)
                        ),
                    ),
                )
            )
    return SnapshotImportPlan(
        package_names=package_names,
        modules=tuple(modules),
        canonical_package_name=_CANONICAL_CANDIDATE_PACKAGE,
        display_name="grafix-candidate",
    )


def load_authoring_definitions_recipe(
    recipe: AuthoringDefinitionsRecipe,
    *,
    seed: AuthoringDefinitionsSnapshot | None = None,
) -> AuthoringDefinitionsSnapshot:
    """確定済み recipe を隔離実行し、成功時だけ snapshot を返す。

    module namespace は import 中だけ ``sys.modules`` に置く。
    返す catalog と callable は module global を直接保持するため、
    評価時に process-global module registry を参照しない。
    """

    if type(recipe) is not AuthoringDefinitionsRecipe:
        raise TypeError("recipe は exact AuthoringDefinitionsRecipe です")
    _preflight_authoring_recipe(recipe)
    base = default_session_authoring_definitions() if seed is None else seed
    if type(base) is not AuthoringDefinitionsSnapshot:
        raise TypeError("seed は exact AuthoringDefinitionsSnapshot である必要があります")
    if base.recipe is not None:
        _preflight_authoring_recipe(base.recipe)

    target = RegistrationTarget(
        operations=base.operations,
        presets=base.presets,
    )
    combined_recipe = (
        None
        if base.recipe is None
        else AuthoringDefinitionsRecipe(roots=(*base.recipe.roots, *recipe.roots))
    )
    if not any(root.modules for root in recipe.roots):
        return target.snapshot(recipe=combined_recipe)

    plan = _candidate_import_plan(recipe)
    with snapshot_import_context(plan, retain_modules=False):
        with registration_scope(target):
            for module in plan.modules:
                importlib.import_module(module.name)

    return target.snapshot(recipe=combined_recipe)


def load_config_authoring_definitions(
    config: RuntimeConfig,
    *,
    seed: AuthoringDefinitionsSnapshot | None = None,
) -> AuthoringDefinitionsSnapshot:
    """config directories を一度 capture し、その exact recipe を実行する。"""

    recipe = capture_authoring_definitions_recipe(config)
    return load_authoring_definitions_recipe(recipe, seed=seed)


__all__ = [
    "authoring_definitions_for_draw",
    "capture_authoring_definitions_recipe",
    "default_session_authoring_definitions",
    "load_authoring_definitions_recipe",
    "load_config_authoring_definitions",
]
