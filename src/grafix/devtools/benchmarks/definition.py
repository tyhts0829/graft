"""
Purpose:
    benchmark workload の metadata・callback・frozen parameter・source identity を、process 間で case ID から復元できる immutable definition に束ねる。
Use when:
    benchmark case の追加、CaseSpec compatibility、scaled fixture、workload/support source fingerprint を変更・調査する場合。
Constraints:
    - parameter tree は strict JSON value として freeze し、setup ごとに独立した mutable copy を渡す。
    - setup/workload/postprocess/measurement context と明示 support source を source identity に含め、意味変更を compatibility key から漏らさない。
    - execution・retry・catalog selection を definition に持ち込まない。
Side effects:
    CaseSpec 作成時に callable source と明示 support file を読み込む。
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grafix.devtools.benchmarks.schema import (
    BenchmarkOutput,
    CaseSpec,
    case_compatibility_key,
    freeze_json_object,
    materialize_json_object,
)


@dataclass(frozen=True, slots=True)
class CaseDefinition:
    """Process 間で case ID から再構築できる静的定義。"""

    case_id: str
    version: int
    label: str
    category: str
    suite: str
    fixture: str
    parameters: Mapping[str, object]
    tags: tuple[str, ...]
    selectable_suites: tuple[str, ...]
    setup: Callable[[dict[str, Any], int], object]
    workload: Callable[[object], object]
    postprocess: Callable[[object, object], BenchmarkOutput] | None = None
    measurement_context: Callable[[object], AbstractContextManager[object]] | None = None
    support_source_files: tuple[Path, ...] = ()
    support_implementations: tuple[Callable[..., object], ...] = ()
    checksum_policy: str = "exact"
    self_sampling: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parameters",
            freeze_json_object(self.parameters),
        )

    def spec(self, *, seed: int) -> CaseSpec:
        """定義と実装sourceから比較可能なCaseSpecを作る。"""

        implementations: tuple[Callable[..., object], ...] = (
            self.setup,
            self.workload,
            *((self.postprocess,) if self.postprocess is not None else ()),
            *((self.measurement_context,) if self.measurement_context is not None else ()),
            *self.support_implementations,
        )
        return make_case_spec(
            case_id=self.case_id,
            version=self.version,
            label=self.label,
            category=self.category,
            suite=self.suite,
            fixture=self.fixture,
            parameters=self.parameters,
            seed=seed,
            implementation=implementations,
            support_source_files=self.support_source_files,
            tags=self.tags,
            checksum_policy=self.checksum_policy,
            self_sampling=self.self_sampling,
        )

    def materialize_parameters(self) -> dict[str, Any]:
        """setup 一回分の独立した plain JSON tree を返す。"""

        return materialize_json_object(freeze_json_object(self.parameters))


def make_case_spec(
    *,
    case_id: str,
    version: int,
    label: str,
    category: str,
    suite: str,
    fixture: str,
    parameters: Mapping[str, Any],
    seed: int,
    implementation: Callable[..., object] | tuple[Callable[..., object], ...],
    support_source_files: tuple[str | Path, ...] = (),
    tags: tuple[str, ...] = (),
    checksum_policy: str = "exact",
    self_sampling: bool = False,
) -> CaseSpec:
    """case 定義と workload source から CaseSpec を作る。"""

    implementations = implementation if isinstance(implementation, tuple) else (implementation,)
    digest = hashlib.sha256(b"grafix.benchmark.case-source.v1\0")
    for function in implementations:
        try:
            source = inspect.getsource(function).encode("utf-8")
        except (OSError, TypeError) as exc:
            raise ValueError(
                "benchmark implementation source を取得できません: "
                f"{function.__module__}.{function.__qualname__}"
            ) from exc
        _update_framed_hash(
            digest,
            f"{function.__module__}.{function.__qualname__}".encode("utf-8"),
        )
        _update_framed_hash(digest, source)
    for source_path in sorted(
        (Path(path).resolve() for path in support_source_files),
        key=lambda path: str(path),
    ):
        _update_framed_hash(digest, source_path.name.encode("utf-8"))
        _update_framed_hash(digest, source_path.read_bytes())
    source_sha256 = digest.hexdigest()
    frozen_parameters = freeze_json_object(parameters)
    return CaseSpec(
        case_id=case_id,
        version=version,
        label=label,
        category=category,
        suite=suite,
        fixture=fixture,
        parameters=frozen_parameters,
        seed=seed,
        source_sha256=source_sha256,
        compatibility_key=case_compatibility_key(
            case_id=case_id,
            version=version,
            fixture=fixture,
            parameters=frozen_parameters,
            seed=seed,
            source_sha256=source_sha256,
            checksum_policy=checksum_policy,
            self_sampling=self_sampling,
        ),
        checksum_policy=checksum_policy,
        tags=tags,
        self_sampling=self_sampling,
    )


def _update_framed_hash(digest: Any, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, byteorder="big", signed=False))
    digest.update(value)


def define_case(
    case_id: str,
    label: str,
    *,
    category: str,
    suite: str,
    fixture: str,
    parameters: Mapping[str, object],
    tags: tuple[str, ...],
    selectable_suites: tuple[str, ...],
    setup: Callable[[dict[str, Any], int], object],
    workload: Callable[[object], object],
    postprocess: Callable[[object, object], BenchmarkOutput] | None = None,
    measurement_context: Callable[[object], AbstractContextManager[object]] | None = None,
    support_source_files: tuple[Path, ...] = (),
    support_implementations: tuple[Callable[..., object], ...] = (),
    self_sampling: bool = False,
) -> CaseDefinition:
    """共通version/checksum policyで一つのcaseを定義する。"""

    return CaseDefinition(
        case_id=case_id,
        version=1,
        label=label,
        category=category,
        suite=suite,
        fixture=fixture,
        parameters=parameters,
        tags=tags,
        selectable_suites=selectable_suites,
        setup=setup,
        workload=workload,
        postprocess=postprocess,
        measurement_context=measurement_context,
        support_source_files=support_source_files,
        support_implementations=support_implementations,
        checksum_policy="exact",
        self_sampling=bool(self_sampling),
    )


def scaled_case_definitions(
    *,
    prefix: str,
    label: str,
    values: tuple[int, ...],
    parameter_name: str,
    category: str,
    suite: str,
    fixture: str,
    setup: Callable[[dict[str, Any], int], object],
    workload: Callable[[object], object],
    suites: tuple[tuple[str, ...], ...],
    support_source_files: tuple[Path, ...] = (),
    support_implementations: tuple[Callable[..., object], ...] = (),
) -> tuple[CaseDefinition, ...]:
    """一つの整数parameterを拡大したcase列を定義する。"""

    return tuple(
        define_case(
            f"{prefix}.{parameter_name}_{value}",
            f"{label} ({value:,})",
            category=category,
            suite=suite,
            fixture=fixture,
            parameters={parameter_name: value},
            tags=("scaling", "exact-checksum"),
            selectable_suites=selectable,
            setup=setup,
            workload=workload,
            support_source_files=support_source_files,
            support_implementations=support_implementations,
        )
        for value, selectable in zip(values, suites, strict=True)
    )


__all__ = [
    "CaseDefinition",
    "define_case",
    "make_case_spec",
    "scaled_case_definitions",
]
