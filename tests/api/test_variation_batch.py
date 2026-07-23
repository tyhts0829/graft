from __future__ import annotations

from fractions import Fraction
import json
import os
import random
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from grafix.export import variation_batch as variation_export_module
from grafix import (
    ExportFormat,
    ExportResult,
    RenderSession,
    VariationBatchResult,
    VariationRenderResult,
    render_variation_batch,
)
from grafix.core.geometry import Geometry
from grafix.core.parameters.codec import encode_param_store
from grafix.core.parameters.collapsed_header import primitive_collapsed_header_key
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.parameters.variations import create_variation

_META = ParamMeta(kind="float", ui_min=0.0, ui_max=100.0)
_AMOUNT = ParameterKey(op="batch", site_id="main", arg="amount")
_LATER = ParameterKey(op="batch", site_id="main", arg="later")
_DISCOVERED = ParameterKey(op="batch", site_id="main", arg="discovered")


class _StringSubclass(str):
    pass


class _TupleSubclass(tuple):
    pass


def _add(store: ParamStore, key: ParameterKey, value: float) -> None:
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=value,
                meta=_META,
                effective=value,
                source="code",
                explicit=False,
            )
        ],
    )


def _set(store: ParamStore, key: ParameterKey, value: float) -> None:
    ok, error = update_state_from_ui(store, key, value, meta=_META)
    assert ok is True and error is None


def _value(store: ParamStore, key: ParameterKey) -> float:
    state = store.get_state(key)
    assert state is not None
    return float(state.ui_value)


class _Session:
    def __init__(
        self,
        store: ParamStore,
        *,
        fail_value: float | None = None,
        mutate_store_during_render: bool = False,
    ) -> None:
        self.param_store = store
        self.fail_value = fail_value
        self.mutate_store_during_render = mutate_store_during_render
        self.observed: list[tuple[float, float, float]] = []
        self.discovered_was_present: list[bool] = []
        self.provenance_seeds: list[int | None | str] = []

    def render(
        self,
        t: float,
        *,
        provenance_seed: int | None | str = "session",
    ) -> SimpleNamespace:
        amount = _value(self.param_store, _AMOUNT)
        later = _value(self.param_store, _LATER)
        self.observed.append((float(t), amount, later))
        self.provenance_seeds.append(provenance_seed)
        self.discovered_was_present.append(
            self.param_store.get_state(_DISCOVERED) is not None
        )
        if self.mutate_store_during_render:
            _add(self.param_store, _DISCOVERED, amount * 100.0)
            self.param_store._set_meta(
                _AMOUNT,
                replace(_META, ui_min=-100.0),
            )
            self.param_store._favorite_keys_ref().add(_DISCOVERED)
            self.param_store._locked_keys_ref().add(_DISCOVERED)
            self.param_store._collapsed_headers_ref().add(
                primitive_collapsed_header_key(("batch", "main"))
            )
            self.param_store._runtime_ref().observed_groups.add(("batch", "mutated"))
            # Render 中の予期しない collection 変更も batch 外へ漏らさない。
            self.param_store._variations_ref().pop("A & first", None)
        if self.fail_value is not None and amount == self.fail_value:
            raise RuntimeError(f"cannot render amount={amount:g}")
        return SimpleNamespace(t=float(t))


class _CaptureService:
    def __init__(self, *, fail_t: float | None = None) -> None:
        self.calls: list[tuple[object, Path, bool, tuple[int, int] | None]] = []
        self.fail_t = fail_t

    def export(
        self,
        frame: object,
        path: str | Path,
        *,
        overwrite: bool = False,
        output_size: tuple[int, int] | None = None,
    ) -> ExportResult:
        output = Path(path)
        self.calls.append((frame, output, bool(overwrite), output_size))
        if self.fail_t is not None and float(frame.t) == self.fail_t:
            raise OSError("capture backend failed")
        output.write_bytes(b"thumbnail")
        manifest = output.with_name(f"{output.name}.capture.json")
        manifest.write_text("{}")
        return ExportResult(
            path=output,
            format=ExportFormat.from_path(output),
            manifest_path=manifest,
        )


@pytest.mark.parametrize("overwrite", ("false", 0, 1))
def test_batch_rejects_non_boolean_overwrite(
    tmp_path: Path,
    overwrite: Any,
) -> None:
    store = _variation_store()

    with pytest.raises(TypeError, match="overwrite"):
        render_variation_batch(
            _Session(store),
            tmp_path,
            overwrite=overwrite,
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("default_t", [True, "1.0"])
def test_batch_rejects_non_numeric_default_time(
    tmp_path: Path,
    default_t: object,
) -> None:
    with pytest.raises(TypeError, match="default_t"):
        render_variation_batch(
            _Session(_variation_store()),
            tmp_path,
            default_t=default_t,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("default_t", [float("inf"), float("nan")])
def test_batch_rejects_non_finite_default_time(
    tmp_path: Path,
    default_t: float,
) -> None:
    with pytest.raises(ValueError, match="default_t"):
        render_variation_batch(
            _Session(_variation_store()),
            tmp_path,
            default_t=default_t,
        )


def test_batch_normalizes_a_valid_real_default_time(tmp_path: Path) -> None:
    result = render_variation_batch(
        _Session(_variation_store()),
        tmp_path,
        variation_names=("C 日本語",),
        default_t=Fraction(3, 2),
        thumbnail_format=ExportFormat.SVG,
        capture_service=_CaptureService(),
    )

    assert result.items[0].t == 1.5
    assert type(result.items[0].t) is float


@pytest.mark.parametrize(
    "names",
    [
        "A & first",
        ["A & first"],
        iter(("A & first",)),
        _TupleSubclass(("A & first",)),
        (1,),
        (_StringSubclass("A & first"),),
    ],
)
def test_batch_rejects_non_sequence_or_non_string_variation_names(
    tmp_path: Path,
    names: object,
) -> None:
    with pytest.raises(TypeError, match="variation_names"):
        render_variation_batch(
            _Session(_variation_store()),
            tmp_path,
            variation_names=names,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("batch_name", (1, _StringSubclass("variations")))
def test_batch_rejects_non_string_batch_name(
    tmp_path: Path,
    batch_name: Any,
) -> None:
    with pytest.raises(TypeError, match="batch_name"):
        render_variation_batch(
            _Session(_variation_store()),
            tmp_path,
            batch_name=batch_name,
        )


@pytest.mark.parametrize("output_root", (1, object(), _StringSubclass("output")))
def test_batch_rejects_implicit_output_root_path_conversion(
    tmp_path: Path,
    output_root: Any,
) -> None:
    with pytest.raises(TypeError, match="output_root"):
        render_variation_batch(
            _Session(_variation_store()),
            output_root,
            variation_names=("A & first",),
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "thumbnail_size",
    (
        [320, 320],
        _TupleSubclass((320, 320)),
        (True, 320),
        (320.0, 320),
        (0, 320),
    ),
)
def test_batch_requires_an_exact_positive_thumbnail_size_tuple(
    tmp_path: Path,
    thumbnail_size: Any,
) -> None:
    with pytest.raises((TypeError, ValueError), match="thumbnail_size"):
        render_variation_batch(
            _Session(_variation_store()),
            tmp_path,
            variation_names=("A & first",),
            thumbnail_size=thumbnail_size,
        )

    assert list(tmp_path.iterdir()) == []


def test_batch_preserves_exact_variation_and_batch_name_whitespace(
    tmp_path: Path,
) -> None:
    store = ParamStore()
    _add(store, _AMOUNT, 1.0)
    _add(store, _LATER, 2.0)
    create_variation(store, "  exact name  ", created_at=1.0)

    result = render_variation_batch(
        _Session(store),
        tmp_path,
        variation_names=("  exact name  ",),
        batch_name="  exact batch  ",
        thumbnail_format=ExportFormat.SVG,
        capture_service=_CaptureService(),
    )

    assert result.output_directory.name == "  exact batch  "
    assert result.items[0].variation_name == "  exact name  "


def _variation_store() -> ParamStore:
    store = ParamStore()
    _add(store, _AMOUNT, 2.0)
    # この時点では _LATER が無いため、B の snapshot には含まれない。
    create_variation(store, "B / missing", seed=22, t=2.0, created_at=1.0)

    _add(store, _LATER, 99.0)
    _set(store, _AMOUNT, 1.0)
    _set(store, _LATER, 10.0)
    create_variation(store, "A & first", seed=11, t=1.0, created_at=2.0)

    _set(store, _AMOUNT, 3.0)
    _set(store, _LATER, 30.0)
    create_variation(store, "C 日本語", seed=None, t=None, created_at=3.0)

    # batch 呼び出し前の original state。
    _set(store, _AMOUNT, 9.0)
    _set(store, _LATER, 99.0)
    return store


def test_batch_merges_each_variation_and_restores_original_store(tmp_path: Path) -> None:
    store = _variation_store()
    session = _Session(store)
    capture = _CaptureService()

    result = render_variation_batch(
        session,
        tmp_path,
        variation_names=("A & first", "B / missing", "C 日本語"),
        default_t=7.5,
        thumbnail_format=ExportFormat.SVG,
        capture_service=capture,
    )

    # B が持たない後発 key は A の値 10 を引き継がず、original 99 に戻る。
    assert session.observed == [
        (1.0, 1.0, 10.0),
        (2.0, 2.0, 99.0),
        (7.5, 3.0, 30.0),
    ]
    assert session.provenance_seeds == [11, 22, None]
    assert (_value(store, _AMOUNT), _value(store, _LATER)) == (9.0, 99.0)
    assert result.success_count == 3
    assert result.failure_count == 0
    assert result.ok is True
    assert all(call[2] is False for call in capture.calls)
    assert all(call[3] is None for call in capture.calls)
    assert "A_first_seed-11.svg" in result.items[0].thumbnail_path.name
    assert "C_日本語_seed-none.svg" in result.items[2].thumbnail_path.name

    sheet = result.contact_sheet_path.read_text()
    assert "A &amp; first" in sheet
    assert "B / missing" in sheet
    assert "C 日本語" in sheet
    assert "seed 11" in sheet
    assert "seed —" in sheet

    summary = json.loads(result.summary_path.read_text())
    assert summary["schema"] == "grafix.variation-batch.v1"
    assert summary["success_count"] == 3
    assert [item["variation_name"] for item in summary["items"]] == [
        "A & first",
        "B / missing",
        "C 日本語",
    ]


def test_batch_rejects_string_thumbnail_format(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="ExportFormat"):
        render_variation_batch(
            _Session(_variation_store()),
            tmp_path,
            thumbnail_format="svg",  # type: ignore[arg-type]
        )


def test_batch_exactly_isolates_render_mutations_and_restores_store(
    tmp_path: Path,
) -> None:
    store = _variation_store()
    session = _Session(store, mutate_store_during_render=True)
    before_revision = store.revision
    before_favorite_revision = store.favorite_revision
    before_adjustments = store.capture_adjustment_snapshot()
    before_explicit = deepcopy(store._explicit_by_key)
    before_favorites = set(store._favorite_keys_ref())
    before_locks = set(store._locked_keys_ref())
    before_collapsed = set(store._collapsed_headers_ref())
    before_runtime = deepcopy(store._runtime_ref())
    before_persisted = encode_param_store(
        store,
        preserve_explicit_overrides=True,
    )

    result = render_variation_batch(
        session,
        tmp_path,
        variation_names=("A & first", "B / missing", "C 日本語"),
        capture_service=_CaptureService(),
    )

    assert result.success_count == 3
    assert session.discovered_was_present == [False, False, False]
    assert store.revision == before_revision
    assert store.capture_adjustment_snapshot() == before_adjustments
    assert store._explicit_by_key == before_explicit
    assert store._favorite_keys_ref() == before_favorites
    assert store._locked_keys_ref() == before_locks
    assert store._collapsed_headers_ref() == before_collapsed
    assert store._runtime_ref() == before_runtime
    assert (
        encode_param_store(store, preserve_explicit_overrides=True)
        == before_persisted
    )
    assert store.get_state(_DISCOVERED) is None

    # exact restore 後に作る mutation view は復元後の store を更新する。
    restored_revision = store.revision
    store._favorite_keys_ref().add(_AMOUNT)
    assert _AMOUNT in store._favorite_keys_ref()
    assert store.favorite_revision == before_favorite_revision + 1
    assert store.revision == restored_revision + 1


def test_png_thumbnail_uses_requested_output_size(tmp_path: Path) -> None:
    store = _variation_store()
    capture = _CaptureService()

    render_variation_batch(
        _Session(store),
        tmp_path,
        variation_names=("A & first",),
        thumbnail_size=(240, 180),
        capture_service=capture,
    )

    assert capture.calls[0][3] == (240, 180)


def test_partial_failure_is_reported_and_does_not_stop_later_variations(
    tmp_path: Path,
) -> None:
    store = _variation_store()
    session = _Session(store, fail_value=2.0)

    result = render_variation_batch(
        session,
        tmp_path,
        variation_names=("A & first", "B / missing", "unknown", "C 日本語"),
        capture_service=_CaptureService(),
    )

    assert [item.status for item in result.items] == [
        "success",
        "failed",
        "failed",
        "success",
    ]
    assert result.items[1].error_type == "RuntimeError"
    assert "amount=2" in (result.items[1].error_message or "")
    assert result.items[2].error_type == "KeyError"
    assert result.success_count == 2
    assert result.failure_count == 2
    assert result.ok is False
    assert session.observed[-1][1:] == (3.0, 30.0)
    assert (_value(store, _AMOUNT), _value(store, _LATER)) == (9.0, 99.0)

    summary = json.loads(result.summary_path.read_text())
    assert summary["failure_count"] == 2
    assert "cannot render" in summary["items"][1]["error_message"]
    assert "unknown" in result.contact_sheet_path.read_text()


def test_capture_failure_is_partial_and_restores_store(tmp_path: Path) -> None:
    store = _variation_store()
    session = _Session(store)

    result = render_variation_batch(
        session,
        tmp_path,
        variation_names=("A & first", "B / missing", "C 日本語"),
        capture_service=_CaptureService(fail_t=2.0),
    )

    assert [item.status for item in result.items] == ["success", "failed", "success"]
    assert result.items[1].error_type == "OSError"
    assert result.items[1].error_message == "capture backend failed"
    assert session.observed[-1][1:] == (3.0, 30.0)
    assert (_value(store, _AMOUNT), _value(store, _LATER)) == (9.0, 99.0)


def test_default_batch_directory_is_no_clobber(tmp_path: Path) -> None:
    store = _variation_store()
    session = _Session(store)

    first = render_variation_batch(
        session,
        tmp_path,
        variation_names=("A & first",),
        capture_service=_CaptureService(),
    )
    sentinel = first.output_directory / "keep.txt"
    sentinel.write_text("original")
    second = render_variation_batch(
        session,
        tmp_path,
        variation_names=("A & first",),
        capture_service=_CaptureService(),
    )

    assert first.output_directory == tmp_path / "variations"
    assert second.output_directory == tmp_path / "variations_001"
    assert sentinel.read_text() == "original"


def test_explicit_overwrite_replaces_the_whole_generation(tmp_path: Path) -> None:
    existing = tmp_path / "variations"
    existing.mkdir()
    (existing / "stale-thumbnail.png").write_bytes(b"stale")
    (existing / "keep.txt").write_text("old generation")
    capture = _CaptureService()

    result = render_variation_batch(
        _Session(_variation_store()),
        tmp_path,
        variation_names=("A & first",),
        thumbnail_format=ExportFormat.SVG,
        overwrite=True,
        capture_service=capture,
    )

    assert result.output_directory == existing
    assert result.items[0].thumbnail_path is not None
    assert result.items[0].thumbnail_path.parent == existing
    assert result.items[0].thumbnail_path.exists()
    assert not (existing / "stale-thumbnail.png").exists()
    assert not (existing / "keep.txt").exists()
    assert capture.calls[0][1].parent != existing
    assert not capture.calls[0][1].parent.exists()
    assert list(tmp_path.glob(".variations.batch-*")) == []
    assert list(tmp_path.glob(".variations.backup-*")) == []


def test_overwrite_publish_failure_rolls_back_previous_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = tmp_path / "variations"
    existing.mkdir()
    sentinel = existing / "keep.txt"
    sentinel.write_text("old generation")
    real_replace = os.replace

    def fail_staging_publish(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        if source_path.parent == tmp_path and source_path.name.startswith(
            ".variations.batch-"
        ):
            raise OSError("simulated generation publish failure")
        real_replace(source, destination)

    monkeypatch.setattr(
        variation_export_module.os,
        "replace",
        fail_staging_publish,
    )

    with pytest.raises(OSError, match="generation publish failure"):
        render_variation_batch(
            _Session(_variation_store()),
            tmp_path,
            variation_names=("A & first",),
            thumbnail_format=ExportFormat.SVG,
            overwrite=True,
            capture_service=_CaptureService(),
        )

    assert sentinel.read_text() == "old generation"
    assert list(tmp_path.glob(".variations.batch-*")) == []
    assert list(tmp_path.glob(".variations.backup-*")) == []


@pytest.mark.parametrize("columns", [True, 1.5, "2"])
def test_columns_must_be_an_integer_without_coercion(
    tmp_path: Path,
    columns: object,
) -> None:
    store = _variation_store()

    with pytest.raises(TypeError, match="columns"):
        render_variation_batch(
            _Session(store),
            tmp_path,
            variation_names=("A & first",),
            columns=columns,  # type: ignore[arg-type]
            capture_service=_CaptureService(),
        )


@pytest.mark.parametrize("columns", [0, -1])
def test_columns_must_be_positive(
    tmp_path: Path,
    columns: int,
) -> None:
    with pytest.raises(ValueError, match="columns"):
        render_variation_batch(
            _Session(_variation_store()),
            tmp_path,
            variation_names=("A & first",),
            columns=columns,
            capture_service=_CaptureService(),
        )


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("variation_name", _StringSubclass("A"), TypeError),
        ("variation_name", 1, TypeError),
        ("variation_name", " ", ValueError),
        ("seed", True, TypeError),
        ("seed", 1.0, TypeError),
        ("seed", "1", TypeError),
        ("t", True, TypeError),
        ("t", "0.0", TypeError),
        ("t", float("inf"), ValueError),
        ("status", _StringSubclass("failed"), TypeError),
        ("status", 1, TypeError),
        ("status", "pending", ValueError),
        ("thumbnail_path", "thumbnail.png", TypeError),
        ("manifest_path", "manifest.json", TypeError),
        ("error_type", _StringSubclass("ValueError"), TypeError),
        ("error_type", 1, TypeError),
        ("error_message", _StringSubclass("bad"), TypeError),
        ("error_message", object(), TypeError),
    ],
)
def test_variation_render_result_rejects_implicit_field_coercion(
    field: str,
    value: Any,
    error: type[Exception],
) -> None:
    values: dict[str, Any] = {
        "variation_name": "A",
        "seed": 1,
        "t": 0.0,
        "status": "failed",
        "error_type": "ValueError",
        "error_message": "bad",
    }
    values[field] = value

    with pytest.raises(error):
        VariationRenderResult(**values)


def test_variation_render_result_preserves_text_and_normalizes_valid_real() -> None:
    item = VariationRenderResult(
        variation_name="  A  ",
        seed=1,
        t=Fraction(1, 2),
        status="failed",
        error_type="  ValueError  ",
        error_message="  bad  ",
    )

    assert item.variation_name == "  A  "
    assert item.t == 0.5
    assert type(item.t) is float
    assert item.error_type == "  ValueError  "
    assert item.error_message == "  bad  "


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("output_directory", "output"),
        ("contact_sheet_path", "contact-sheet.svg"),
        ("summary_path", "summary.json"),
        ("items", []),
        ("items", iter(())),
        ("items", _TupleSubclass(())),
        ("items", (object(),)),
    ],
)
def test_variation_batch_result_requires_paths_and_exact_dto_tuple(
    field: str,
    value: Any,
    tmp_path: Path,
) -> None:
    item = VariationRenderResult(
        variation_name="A",
        seed=1,
        t=0.0,
        status="failed",
        error_type="ValueError",
    )
    values: dict[str, Any] = {
        "output_directory": tmp_path,
        "items": (item,),
        "contact_sheet_path": tmp_path / "contact-sheet.svg",
        "summary_path": tmp_path / "summary.json",
    }
    values[field] = value

    with pytest.raises(TypeError):
        VariationBatchResult(**values)


def test_batch_result_payloads_are_public_immutable_values(tmp_path: Path) -> None:
    item = VariationRenderResult(
        variation_name="A",
        seed=1,
        t=0.0,
        status="failed",
        error_type="ValueError",
        error_message="bad",
    )
    result = VariationBatchResult(
        output_directory=tmp_path,
        items=(item,),
        contact_sheet_path=tmp_path / "contact-sheet.svg",
        summary_path=tmp_path / "summary.json",
    )

    assert result.failure_count == 1
    with pytest.raises(TypeError, match="relative_to"):
        item.as_dict(relative_to="output")  # type: ignore[arg-type]
    with pytest.raises(AttributeError):
        item.status = "success"  # type: ignore[misc]


def test_real_render_session_and_capture_service_contract(tmp_path: Path) -> None:
    def draw(_t: float) -> Geometry:
        return Geometry.create("concat")

    existing = tmp_path / "variations"
    existing.mkdir()
    stale = existing / "stale.svg"
    stale.write_text("stale")
    with RenderSession(draw, seed=999) as session:
        create_variation(
            session.param_store,
            "Real contract",
            seed=42,
            t=1.25,
            created_at=1.0,
        )
        create_variation(
            session.param_store,
            "No seed",
            seed=None,
            t=1.25,
            created_at=2.0,
        )
        rng_before = random.getstate()
        result = render_variation_batch(
            session,
            tmp_path,
            variation_names=("Real contract", "Real contract", "No seed"),
            thumbnail_format=ExportFormat.SVG,
            overwrite=True,
        )
        rng_after = random.getstate()
        assert session.metadata.provenance.seed == 999

    first, repeated, no_seed = result.items
    assert all(item.status == "success" for item in result.items)
    assert all(item.t == pytest.approx(1.25) for item in result.items)
    assert all(
        item.thumbnail_path is not None and item.thumbnail_path.exists()
        for item in result.items
    )
    assert all(
        item.manifest_path is not None and item.manifest_path.exists()
        for item in result.items
    )
    assert result.contact_sheet_path.exists()
    assert not stale.exists()
    assert rng_after == rng_before
    manifests = [
        json.loads(item.manifest_path.read_text())  # type: ignore[union-attr]
        for item in result.items
    ]
    assert [manifest["seed"] for manifest in manifests] == [42, 42, None]
    assert manifests[0]["parameters"]["snapshot_hash"] == manifests[1]["parameters"][
        "snapshot_hash"
    ]
    assert first.thumbnail_path is not None
    assert repeated.thumbnail_path is not None
    assert first.thumbnail_path.read_bytes() == repeated.thumbnail_path.read_bytes()
    assert no_seed.seed is None
    for item, manifest in zip(result.items, manifests, strict=True):
        assert manifest["output"]["artifact_paths"] == [str(item.thumbnail_path)]
        assert manifest["output"]["artifact_paths"] == [str(item.thumbnail_path)]
        assert item.manifest_path is not None
        assert ".variations.batch-" not in item.manifest_path.read_text()
