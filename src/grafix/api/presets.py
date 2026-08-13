"""
Purpose:
    登録済みpresetをstable identity付きで呼び出す公開名前空間Pを提供する。
Use when:
    preset lookup、label/key identity、またはsession catalogへの束縛を変更する場合。
Constraints:
    - session内ではそのgenerationのcatalog、外側ではdefault authoring snapshotだけを参照する。
    - config-scoped presetをdrawの外側から暗黙loadしない。
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from grafix.core.parameters import validate_parameter_identity
from grafix.core.parameters.identity import identity_string
from grafix.core.preset_catalog import PresetIdentity, current_preset_catalog
from grafix.core.scene import SceneItem


class PresetNamespace:
    """preset を `P.<name>(...)` で呼び出す名前空間。

    束縛中は session catalog、束縛外では default authoring snapshot だけを参照する。
    """

    __slots__ = ("_identity",)

    def __init__(self, identity: PresetIdentity | None = None) -> None:
        self._identity = identity

    def __getattr__(self, name: str) -> Callable[..., SceneItem]:
        if name.startswith("_"):
            raise AttributeError(name)

        catalog = current_preset_catalog()
        if name not in catalog:
            raise AttributeError(f"未登録の preset: {name!r}")
        declaration = catalog[name]
        if self._identity is None:
            return declaration.func
        return partial(declaration.invoker, self._identity)

    def __call__(
        self,
        *,
        name: str | None = None,
        key: str | int | None = None,
        instance_key: str | int | None = None,
        shared: bool = False,
    ) -> "PresetNamespace":
        """label と parameter identity を保持する preset 名前空間を返す。

        ``key`` は semantic site、``instance_key`` は反復 instance を表す。
        ``shared=True`` は同じ semantic site を共有し、``instance_key`` との
        同時指定はこの呼び出しで拒否される。
        """

        validate_parameter_identity(
            key=key,
            instance_key=instance_key,
            shared=shared,
        )
        label = None if name is None else identity_string(name, name="preset label")
        return PresetNamespace(
            PresetIdentity(
                name=label,
                key=key,
                instance_key=instance_key,
                shared=shared,
            )
        )


P = PresetNamespace()
"""preset を `P.<name>(...)` で呼び出す公開名前空間。"""

__all__ = ["P", "PresetNamespace"]
