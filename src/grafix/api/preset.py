"""
Purpose:
    再利用するscene callableを、公開parameter schema付きpreset declarationとして登録する。
Use when:
    `@preset`のsignature、parameter公開範囲、activation、またはregistrationを変更する場合。
Constraints:
    - preset本体の内部operation観測をmuteし、GUIにはpresetの公開引数だけを出す。
    - declarationとinvokerが同じschema/identity snapshotを共有する。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from functools import wraps
from typing import Any, ParamSpec

from grafix.core.authoring_definitions import register_authoring_declaration
from grafix.core.geometry import Geometry
from grafix.core.parameters import caller_site_id, current_frame_params, current_param_store
from grafix.core.parameters.context import (
    current_param_recording_enabled,
    parameter_recording_muted,
)
from grafix.core.parameters.labels_ops import set_label
from grafix.core.parameters.identity import identity_string
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.meta_spec import meta_dict_from_user
from grafix.core.parameters.resolver import resolve_params
from grafix.core.parameters.validation import validate_parameter_value
from grafix.core.operation_schema import ParameterOpSchema
from grafix.core.preset_catalog import (
    PresetDeclaration,
    PresetIdentity,
    attach_preset_declaration,
    preset_op,
)
from grafix.core.scene import SceneItem

_PSpec = ParamSpec("_PSpec")
_PRESET_ACTIVATE_META = ParamMeta(
    kind="bool",
    description="このプリセットによるシーン要素の生成を有効にする。",
)

# --- 役割メモ ---
#
# preset declaration は callable と GUI/永続化向け仕様を同じ snapshot で保持する。
# canonical op は `ParameterKey(op=..., site_id=..., arg=...)` の op 側にも使われる。


def _defaults_from_signature(
    func: Callable[..., object],
    meta: dict[str, ParamMeta],
) -> dict[str, Any]:
    # meta に含めた引数は「公開パラメータ」扱いになるため、default を必須にする。
    # 目的: GUI の base 値/初期値が常に決まり、スケッチ側の意図が曖昧にならないようにする。
    #
    # default=None を禁止するのは「未指定/欠損」と区別が付きにくく、UI/永続化の扱いが難しいため。
    sig = inspect.signature(func)
    defaults: dict[str, Any] = {}
    for arg in meta.keys():
        param = sig.parameters.get(arg)
        if param is None:
            raise ValueError(f"@preset meta 引数がシグネチャに存在しません: {func.__name__}.{arg}")
        if param.default is inspect._empty:
            raise ValueError(f"@preset meta 引数は default 必須です: {func.__name__}.{arg}")
        if param.default is None:
            raise ValueError(
                f"@preset meta 引数 default に None は使えません: {func.__name__}.{arg}"
            )
        defaults[arg] = param.default
    return defaults


def _maybe_set_label(*, op: str, site_id: str, label: str) -> None:
    # label の設定先は 2 系統ある:
    # - ParamStore がある（メインプロセス）: store に直接 label を保存する
    # - store が無い（mp-draw worker 等）: frame_params 経由で「観測結果」として返す
    store = current_param_store()
    if store is not None:
        set_label(store, op=op, site_id=site_id, label=label)
        return
    frame_params = current_frame_params()
    if frame_params is not None:
        frame_params.set_label(op=op, site_id=site_id, label=label)


def preset(
    *,
    meta: Mapping[str, ParamMeta | Mapping[str, object]],
    ui_visible: Mapping[str, Callable[[Mapping[str, Any]], bool]] | None = None,
) -> Callable[[Callable[_PSpec, SceneItem]], Callable[_PSpec, SceneItem]]:
    """プリセット関数を Parameter GUI 向けにラップするデコレータ。

    Parameters
    ----------
    meta : Mapping[str, ParamMeta | Mapping[str, object]]
        GUI に公開する引数のメタ情報。ここに含めた引数だけが GUI/永続化の対象になる。

        dict spec の形式:
        - kind: str（必須）
        - ui_min/ui_max: object（任意）
        - choices: Sequence[str] | None（任意）
    ui_visible : Mapping[str, Callable[[Mapping[str, Any]], bool]] or None
        GUI 表示向けの “行の可視性” ルール。
        key は引数名（arg）、value は “その preset 呼び出し 1 回” の現在値辞書を受け取り、
        その引数行を表示するかどうかを返す predicate。

    Notes
    -----
    - preset 関数は `SceneItem`（Geometry / Layer / それらのネスト列）を返す component 専用。
    - 公開対象は `meta` に含まれる引数のみ。
    - 関数本体は自動で mute され、内部の `G.*` / `E.*` の観測（GUI/永続化）を行わない。
    - `activate` は予約引数として自動追加され、GUI/永続化の対象になる（meta に含めない）。
      `False` の場合は関数本体を実行せず、空の `Geometry` を返す。
    - label と parameter identity は ``P(name=..., key=...).foo(...)`` から渡す。
      元 preset 関数は wrapper 所有の予約名を受け付けない。通常の
      ``P.foo(...)`` では自動追加された ``activate`` だけを直接指定できる。
    """

    meta_norm = meta_dict_from_user(meta)
    reserved = {"name", "key", "instance_key", "shared", "activate"}
    # identity/name/activate は予約引数:
    # - name: GUI 上のグループ見出し名（label）を差し替える（GUI には出さない）
    # - key: 同一呼び出し箇所で複数回呼ぶときの衝突回避（GUI には出さない）
    # - activate: preset を “有効化” するための公開 bool（GUI/永続化の対象）
    if reserved & set(meta_norm.keys()):
        bad = ", ".join(sorted(reserved & set(meta_norm.keys())))
        raise ValueError(f"@preset meta に予約引数は含められません: {bad}")

    def decorator(func: Callable[_PSpec, SceneItem]) -> Callable[_PSpec, SceneItem]:
        preset_name = identity_string(func.__name__, name="preset name")
        parameter_op = preset_op(preset_name)

        # GUI 側で扱う op 名は `preset.<funcname>` に固定する。
        # 目的: preset を「1 種類の op」として分類/表示し、ParameterKey の op にも使う。
        sig = inspect.signature(func)
        reserved_in_signature = reserved & set(sig.parameters)
        if reserved_in_signature:
            bad = ", ".join(sorted(reserved_in_signature))
            raise ValueError(
                f"@preset の予約引数はシグネチャに含められません: "
                f"{func.__name__}({bad})"
            )
        # 公開パラメータは default 必須として、呼び出しごとの base 値が必ず決まるようにする。
        defaults = _defaults_from_signature(func, meta_norm)
        meta_keys = set(meta_norm.keys())
        # activate はデコレータ側で自動的に公開する（meta には書かせない）。
        meta_with_activate = {"activate": _PRESET_ACTIVATE_META, **meta_norm}
        # GUI の行順は「定義したシグネチャ順」を優先する（dict の順序に依存しない）。
        sig_order = [arg_name for arg_name in sig.parameters if arg_name in meta_keys]

        def _invoke_at_site(
            identity: PresetIdentity,
            site_id: str,
            args: tuple[Any, ...],
            kwargs: dict[str, Any],
        ) -> SceneItem:
            # activate を kwargs から取り出す。
            # `explicit` 判定が必要なので、pop 前に「明示指定されていたか」を保持する。
            activate_explicit = "activate" in kwargs
            activate_base = validate_parameter_value(
                kwargs.pop("activate", True),
                kind="bool",
                choices=None,
            )

            # - bind: 呼び出しをシグネチャに当てはめ、引数名で扱えるようにする
            # - explicit_keys: 「ユーザーが明示的に渡した引数名」を記録する（apply_defaults 前）
            #   目的: resolve_params(explicit_args=...) に渡し、初期 override ポリシーへ反映させるため
            bound = sig.bind(*args, **kwargs)
            explicit_keys = set(bound.arguments.keys())
            bound.apply_defaults()

            # group header 名は、指定が無ければ関数名を使う（GUI 未使用時は何もしない）。
            if current_param_recording_enabled():
                label = preset_name if identity.name is None else identity.name
                _maybe_set_label(op=parameter_op, site_id=site_id, label=label)

            # 公開引数だけ解決する:
            # - preset の “公開 UI” を meta に絞り、本体内部の G/E パラメータは GUI/永続化対象外にする。
            # - recording 無効時は resolve をスキップし、コードが渡した base 値をそのまま使う。
            public_params = {"activate": activate_base}
            public_params.update({k: bound.arguments[k] for k in meta_keys})
            resolved_params = public_params
            explicit_public = meta_keys & explicit_keys
            explicit_public_with_activate = set(explicit_public)
            if activate_explicit:
                explicit_public_with_activate.add("activate")
            if (
                current_param_recording_enabled()
                and current_frame_params() is not None
                and meta_with_activate
            ):
                # resolve_params は:
                # - base 値（スケッチ側）/ GUI 値 / CC 値 を統合して effective 値を返す
                # - 同時に frame_params に「観測結果」を記録し、フレーム終端で ParamStore にマージされる
                resolved_params = resolve_params(
                    op=parameter_op,
                    params=public_params,
                    meta=meta_with_activate,
                    site_id=site_id,
                    explicit_args=explicit_public_with_activate,
                )

            for k, v in resolved_params.items():
                if k == "activate":
                    continue
                bound.arguments[k] = v

            resolved_activate = validate_parameter_value(
                resolved_params["activate"],
                kind="bool",
                choices=None,
            )
            if not resolved_activate:
                # activate=False なら「何も描かない Geometry」を返して終了する。
                # （GUI 行としての preset 自体は記録/表示される）
                return Geometry.create(op="concat")

            # 本体は常に mute:
            # preset 内部で生成される Geometry（G.* / E.*）は公開 API の外に置き、
            # GUI/永続化は “preset の公開引数” に限定する。
            with parameter_recording_muted():
                return func(*bound.args, **bound.kwargs)

        direct_identity = PresetIdentity(
            name=None,
            key=None,
            instance_key=None,
            shared=False,
        )

        @wraps(func)
        def wrapper(*args: _PSpec.args, **kwargs: _PSpec.kwargs) -> SceneItem:
            site_id = caller_site_id(skip=1)
            return _invoke_at_site(
                direct_identity,
                site_id,
                tuple(args),
                dict(kwargs),
            )

        def invoker(
            identity: PresetIdentity,
            /,
            *args: Any,
            **kwargs: Any,
        ) -> SceneItem:
            if not isinstance(identity, PresetIdentity):
                raise TypeError("preset identity は PresetIdentity である必要があります")
            site_id = caller_site_id(
                skip=1,
                key=identity.key,
                instance_key=identity.instance_key,
                shared=identity.shared,
            )
            return _invoke_at_site(identity, site_id, args, kwargs)

        declaration = PresetDeclaration(
            name=preset_name,
            func=wrapper,
            invoker=invoker,
            schema=ParameterOpSchema(
                meta=meta_with_activate,
                defaults={"activate": True, **defaults},
                param_order=("activate", *sig_order),
                ui_visible={} if ui_visible is None else ui_visible,
            ),
        )
        register_authoring_declaration(declaration)
        attach_preset_declaration(wrapper, declaration)
        return wrapper

    return decorator


__all__ = ["preset"]
