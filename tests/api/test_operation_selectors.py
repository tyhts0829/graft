"""G/E selector が実 operation の Geometry recipe へ lower される契約を検証する。"""

from __future__ import annotations

import numpy as np
import pytest

from grafix import E, G
from grafix.api._operation_selector import (
    PRIMITIVE_SELECTOR_OP,
    freeze_params_by_target,
    resolve_effect_selection,
    resolve_primitive_selection,
)
from grafix.api.effects import _make_effect_selector_step
from grafix.core.operation_authoring import effect, primitive
from grafix.core.operation_catalog import (
    OperationCatalogBuilder,
    bind_operation_catalog,
    current_operation_catalog,
)
from grafix.core.operation_selector import (
    ensure_primitive_selector_spec,
    selector_spec,
)
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple


@primitive
def selector_test_custom_primitive(*, extent: float) -> GeomTuple:
    """selector の required 引数転送を検証する custom primitive。"""

    _ = extent
    raise AssertionError("Geometry recipe の構築時に primitive を評価してはならない")


@effect
def selector_test_custom_effect(g: GeomTuple, *, amount: float) -> GeomTuple:
    """selector の required 引数転送を検証する custom effect。"""

    _ = amount
    return g


_selector_test_live_calls = 0


@primitive(cache_policy="none", version="1")
def selector_test_live_primitive() -> GeomTuple:
    """selector が target の uncached 契約を保つことを検証する。"""

    global _selector_test_live_calls
    _selector_test_live_calls += 1
    coords = np.full(
        (2, 3),
        float(_selector_test_live_calls),
        dtype=np.float32,
    )
    offsets = np.asarray([0, 2], dtype=np.int32)
    return coords, offsets


def test_g_select_lowers_to_target_primitive_with_target_arguments() -> None:
    geometry = G.select(
        target="circle",
        params_by_target={
            "circle": {
                "radius": 3.5,
                "segments": 24,
                "center": (1.0, 2.0, 3.0),
            }
        },
        key="selected-shape",
    )

    assert geometry.op == "circle"
    assert geometry.inputs == ()
    assert dict(geometry.args) == {
        "activate": True,
        "center": (1.0, 2.0, 3.0),
        "radius": 3.5,
        "segments": 24,
    }


def test_g_select_uses_target_defaults_and_preserves_geometry_identity() -> None:
    first = G.select(target="circle", key="first")
    same = G.select(target="circle", key="same")
    changed_arg = G.select(
        target="circle",
        params_by_target={"circle": {"radius": 2.0}},
        key="changed-arg",
    )
    changed_target = G.select(target="line", key="changed-target")

    assert first.op == same.op == "circle"
    assert first.id == same.id
    assert first.id != changed_arg.id
    assert first.id != changed_target.id
    assert dict(first.args) == {
        "activate": True,
        "center": (0.0, 0.0, 0.0),
        "radius": 0.5,
        "segments": 96,
    }


def test_e_select_lowers_to_unary_target_effect() -> None:
    source = G.line(length=2.0, key="source")

    geometry = E.select(
        target="rotate",
        n_inputs=1,
        params_by_target={"rotate": {"rotation": (0.0, 0.0, 30.0)}},
        key="selected-effect",
    )(source)

    assert geometry.op == "rotate"
    assert geometry.inputs == (source,)
    assert dict(geometry.args) == {
        "activate": True,
        "auto_center": True,
        "pivot": (0.0, 0.0, 0.0),
        "rotation": (0.0, 0.0, 30.0),
    }


def test_e_select_can_be_followed_by_a_normal_unary_effect() -> None:
    source = G.line(key="source")

    geometry = E.select(
        target="rotate",
        params_by_target={"rotate": {"rotation": (10.0, 20.0, 30.0)}},
        key="selected-effect",
    ).translate(delta=(4.0, 5.0, 6.0))(source)

    assert geometry.op == "translate"
    assert dict(geometry.args)["delta"] == (4.0, 5.0, 6.0)
    selected = geometry.inputs[0]
    assert selected.op == "rotate"
    assert dict(selected.args)["rotation"] == (10.0, 20.0, 30.0)
    assert selected.inputs == (source,)


def test_unary_e_select_can_be_added_inside_an_effect_chain() -> None:
    source = G.line(key="source")

    geometry = E.rotate(
        rotation=(0.0, 0.0, 15.0),
        key="fixed-effect",
    ).select(
        target="translate",
        params_by_target={"translate": {"delta": (1.0, 2.0, 3.0)}},
        key="selected-effect",
    )(source)

    assert geometry.op == "translate"
    assert dict(geometry.args)["delta"] == (1.0, 2.0, 3.0)
    assert geometry.inputs[0].op == "rotate"
    assert geometry.inputs[0].inputs == (source,)


def test_e_select_lowers_binary_target_with_both_inputs() -> None:
    first = G.circle(center=(-1.0, 0.0, 0.0), key="first")
    second = G.circle(center=(1.0, 0.0, 0.0), key="second")

    geometry = E.select(
        target="boolean",
        n_inputs=2,
        params_by_target={"boolean": {"mode": "difference"}},
        key="selected-binary-effect",
    )(first, second)

    assert geometry.op == "boolean"
    assert geometry.inputs == (first, second)
    assert dict(geometry.args) == {
        "activate": True,
        "mode": "difference",
    }


def test_e_select_rejects_target_with_different_arity() -> None:
    with pytest.raises(ValueError) as exc_info:
        E.select(target="rotate", n_inputs=2)

    message = str(exc_info.value)
    assert "rotate" in message
    assert "2" in message or "n_inputs" in message

    with pytest.raises(TypeError, match="チェーンの先頭"):
        E.rotate().select(target="boolean", n_inputs=2)


@pytest.mark.parametrize("n_inputs", [True, 1.5, "1", None])
def test_e_select_rejects_non_integer_arity(n_inputs: object) -> None:
    with pytest.raises(TypeError, match="n_inputs.*int"):
        E.select(target="rotate", n_inputs=n_inputs)  # type: ignore[arg-type]


@pytest.mark.parametrize("n_inputs", [0, -1])
def test_e_select_rejects_non_positive_arity(n_inputs: int) -> None:
    with pytest.raises(ValueError, match="n_inputs.*1 以上"):
        E.select(target="rotate", n_inputs=n_inputs)

    with pytest.raises(ValueError, match="n_inputs.*1 以上"):
        E.rotate().select(n_inputs=n_inputs)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid", [0, 1, "", None, object()])
def test_selector_resolution_rejects_non_bool_target_explicit(
    invalid: object,
) -> None:
    primitive_params = freeze_params_by_target(None, kind="primitive")
    effect_params = freeze_params_by_target(None, kind="effect", n_inputs=1)
    with pytest.raises(TypeError, match="target_explicit.*bool"):
        resolve_primitive_selection(
            target="circle",
            target_explicit=invalid,  # type: ignore[arg-type]
            params_by_target=primitive_params,
            site_id="strict-primitive-selector",
        )

    with pytest.raises(TypeError, match="target_explicit.*bool"):
        resolve_effect_selection(
            target="rotate",
            target_explicit=invalid,  # type: ignore[arg-type]
            n_inputs=1,
            params_by_target=effect_params,
            site_id="strict-effect-selector",
        )


def test_resolved_selection_params_are_read_only() -> None:
    frozen_params = freeze_params_by_target(None, kind="primitive")
    selected = resolve_primitive_selection(
        target="circle",
        target_explicit=True,
        params_by_target=frozen_params,
        site_id="immutable-primitive-selector",
    )

    with pytest.raises(TypeError):
        selected.params["radius"] = 2.0  # type: ignore[index]


def test_frozen_selector_params_bind_one_catalog_and_selector() -> None:
    catalog = current_operation_catalog()
    frozen = freeze_params_by_target(
        {"circle": {"radius": 2.0}},
        kind="primitive",
        catalog=catalog,
    )

    selected = resolve_primitive_selection(
        target="circle",
        target_explicit=True,
        params_by_target=frozen,
        site_id="bound-primitive-selector",
    )

    assert frozen.selector is ensure_primitive_selector_spec()
    assert frozen.catalog is catalog
    assert selected.target == "circle"
    assert selected.params["radius"] == 2.0


def test_effect_resolution_rejects_frozen_selector_with_other_arity() -> None:
    frozen = freeze_params_by_target(None, kind="effect", n_inputs=1)

    with pytest.raises(LookupError, match="n_inputs"):
        resolve_effect_selection(
            target="boolean",
            target_explicit=True,
            n_inputs=2,
            params_by_target=frozen,
            site_id="wrong-arity-selector",
        )


def test_freeze_rejects_selector_from_stale_catalog_generation() -> None:
    original = current_operation_catalog()
    stale_selector = selector_spec(original, kind="primitive", n_inputs=0)
    reduced_builder = OperationCatalogBuilder()
    for entry in original.entries():
        if entry.kind != "primitive" or entry.name != "arc":
            reduced_builder.register(entry.declaration)

    with pytest.raises(LookupError, match="catalog"):
        freeze_params_by_target(
            None,
            kind="primitive",
            catalog=reduced_builder.freeze(),
            selector=stale_selector,
        )


@pytest.mark.parametrize("invalid", [0, 1, "", None, object()])
def test_effect_selector_step_rejects_non_bool_target_explicit(
    invalid: object,
) -> None:
    with pytest.raises(TypeError, match="target_explicit.*bool"):
        _make_effect_selector_step(
            target="rotate",
            target_explicit=invalid,  # type: ignore[arg-type]
            n_inputs=1,
            params_by_target=None,
            site_id="strict-effect-selector-step",
        )


def test_e_select_reports_target_and_arity_when_no_candidates_exist() -> None:
    with pytest.raises(ValueError) as exc_info:
        E.select(target="rotate", n_inputs=99)

    message = str(exc_info.value)
    assert "rotate" in message
    assert "n_inputs=99" in message
    assert "利用可能な候補" in message


def test_e_select_keeps_catalog_snapshot_if_current_catalog_changes_before_apply() -> None:
    @effect(n_inputs=3)
    def selector_test_delayed_removed_effect(
        first: GeomTuple,
        _second: GeomTuple,
        _third: GeomTuple,
    ) -> GeomTuple:
        return first

    original = current_operation_catalog()
    with bind_operation_catalog(original):
        builder = E.select(
            target="selector_test_delayed_removed_effect",
            n_inputs=3,
        )
    reduced_builder = OperationCatalogBuilder()
    for entry in original.entries():
        if entry.name != "selector_test_delayed_removed_effect":
            reduced_builder.register(entry.declaration)
    with bind_operation_catalog(reduced_builder.freeze()):
        inputs = tuple(G.line(key=f"delayed-removed-{index}") for index in range(3))
        geometry = builder(*inputs)
    assert geometry.op == "selector_test_delayed_removed_effect"
    assert geometry.inputs == inputs


def test_e_select_rejects_wrong_number_of_geometry_inputs() -> None:
    source = G.line(key="source")
    other = G.line(key="other")
    unary = E.select(target="rotate", n_inputs=1)
    binary = E.select(target="boolean", n_inputs=2)

    with pytest.raises(TypeError, match=r"1 個"):
        unary(source, other)
    with pytest.raises(TypeError, match=r"2 個"):
        binary(source)


@pytest.mark.parametrize(
    ("namespace", "target", "kind"),
    (
        (G, "selector_test_missing_primitive", "primitive"),
        (E, "selector_test_missing_effect", "effect"),
    ),
)
def test_select_rejects_unknown_target(
    namespace: object,
    target: str,
    kind: str,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        namespace.select(target=target)  # type: ignore[attr-defined]

    message = str(exc_info.value)
    assert target in message
    assert kind in message
    assert "利用可能な候補" in message


@pytest.mark.parametrize("namespace", (G, E))
@pytest.mark.parametrize("invalid", (1, object()))
def test_select_rejects_implicitly_stringifiable_target(
    namespace: object,
    invalid: object,
) -> None:
    with pytest.raises(TypeError, match="空でない文字列"):
        namespace.select(target=invalid)  # type: ignore[attr-defined]


@pytest.mark.parametrize("namespace", (G, E))
def test_select_rejects_empty_target(namespace: object) -> None:
    with pytest.raises(ValueError, match="空でない文字列"):
        namespace.select(target="")  # type: ignore[attr-defined]


@pytest.mark.parametrize("namespace", (G, E))
@pytest.mark.parametrize("invalid", (1, object(), ""))
def test_select_rejects_noncanonical_params_by_target_key(
    namespace: object,
    invalid: object,
) -> None:
    with pytest.raises((TypeError, ValueError), match="空でない文字列"):
        namespace.select(  # type: ignore[attr-defined]
            params_by_target={invalid: {}},
        )


def test_select_validates_unknown_target_kwargs() -> None:
    with pytest.raises(TypeError, match=r"raduis.*radius"):
        G.select(
            target="circle",
            params_by_target={"circle": {"raduis": 2.0}},
        )

    with pytest.raises(TypeError, match=r"rotaton.*rotation"):
        E.select(
            target="rotate",
            params_by_target={"rotate": {"rotaton": (0.0, 0.0, 10.0)}},
        )


def test_select_validates_unselected_params_by_target_entries() -> None:
    with pytest.raises(ValueError) as exc_info:
        G.select(
            target="circle",
            params_by_target={"selector_test_missing_primitive": {}},
        )
    assert "selector_test_missing_primitive" in str(exc_info.value)

    with pytest.raises(TypeError, match=r"lenght.*length"):
        G.select(
            target="circle",
            params_by_target={"line": {"lenght": 2.0}},
        )


def test_select_supports_registered_custom_operations_and_required_args() -> None:
    primitive_geometry = G.select(
        target="selector_test_custom_primitive",
        params_by_target={
            "selector_test_custom_primitive": {
                "extent": 7.5,
            }
        },
    )
    source = G.line(key="source")
    effect_geometry = E.select(
        target="selector_test_custom_effect",
        params_by_target={
            "selector_test_custom_effect": {
                "amount": 2.5,
            }
        },
    )(source)

    assert primitive_geometry.op == "selector_test_custom_primitive"
    assert dict(primitive_geometry.args) == {"extent": 7.5}
    assert effect_geometry.op == "selector_test_custom_effect"
    assert effect_geometry.inputs == (source,)
    assert dict(effect_geometry.args) == {"amount": 2.5}


def test_select_reports_missing_gui_visible_required_argument() -> None:
    with pytest.raises(TypeError, match=r"required|必要.*extent|extent"):
        G.select(target="selector_test_custom_primitive")

    source = G.line(key="source")
    with pytest.raises(TypeError, match=r"required|必要.*amount|amount"):
        E.select(target="selector_test_custom_effect")(source)


def test_private_selector_specs_are_not_exposed_in_public_catalogs() -> None:
    G.select(target="circle")
    E.select(target="rotate", n_inputs=1)
    E.select(target="boolean", n_inputs=2)

    primitive_catalog_names = {entry.name for entry in G.catalog()}
    effect_catalog_names = {entry.name for entry in E.catalog()}
    operation_catalog = current_operation_catalog()
    assert all(not entry.name.startswith("_") for entry in operation_catalog.entries())
    assert "select" not in primitive_catalog_names
    assert "select" not in effect_catalog_names


def test_e_select_copies_params_by_target_before_builder_application() -> None:
    rotation = (0.0, 0.0, 45.0)
    params_by_target: dict[str, dict[str, object]] = {
        "rotate": {
            "rotation": rotation,
        }
    }
    original = {
        "rotate": {
            "rotation": (0.0, 0.0, 45.0),
        }
    }
    selected = E.select(
        target="rotate",
        params_by_target=params_by_target,
    )
    selected_hash = hash(selected)

    assert params_by_target == original
    params_by_target["rotate"]["rotation"] = (90.0, 90.0, 90.0)
    params_by_target.clear()

    geometry = selected(G.line(key="source"))

    assert hash(selected) == selected_hash
    assert dict(geometry.args)["rotation"] == (0.0, 0.0, 45.0)


def test_select_preserves_target_identity_without_fake_evaluator_entry() -> None:
    live = G.select(target="selector_test_live_primitive")
    selected_circle = G.select(target="circle")

    catalog = current_operation_catalog()
    assert live.op == "selector_test_live_primitive"
    assert catalog.resolve("primitive", live.op).cache_policy == "none"
    assert selected_circle.op == "circle"
    assert ("primitive", PRIMITIVE_SELECTOR_OP) not in catalog


def test_selector_spec_refreshes_only_after_public_catalog_change() -> None:
    before = ensure_primitive_selector_spec()
    assert ensure_primitive_selector_spec() is before

    @primitive(meta={"size": ParamMeta(kind="float")})
    def selector_test_late_primitive(
        *,
        size: float = 1.0,
    ) -> GeomTuple:
        _ = size
        raise AssertionError("Geometry recipe の構築時に評価してはならない")

    selected = G.select(
        target="selector_test_late_primitive",
        params_by_target={"selector_test_late_primitive": {"size": 2.0}},
    )
    after = ensure_primitive_selector_spec()

    assert selected.op == "selector_test_late_primitive"
    assert after.fingerprint != before.fingerprint
    assert "selector_test_late_primitive" in (
        after.schema.meta["target"].choices or ()
    )

    G.select(target="selector_test_late_primitive")
    assert ensure_primitive_selector_spec() is after


def test_select_rejects_private_target_and_invalid_target_choice() -> None:
    with pytest.raises(ValueError, match=PRIMITIVE_SELECTOR_OP):
        G.select(target=PRIMITIVE_SELECTOR_OP)

    with pytest.raises(ValueError, match=r"anchor"):
        G.select(
            target="circle",
            params_by_target={"line": {"anchor": "not-an-anchor"}},
        )
