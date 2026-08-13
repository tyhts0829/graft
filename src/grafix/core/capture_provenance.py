"""
Purpose:
    session、parameter snapshot、評価済み frame の provenance を immutable value と canonical JSON identity で固定する。
Use when:
    capture の source/config/parameter/frame identity、hash、manifest section を変更・調査する場合。
Constraints:
    - canonical JSON は strict な有限 JSON value だけを受理し、key 順や process に依存させない。
    - capture worker には確定済み snapshot を渡し、source・Git・config を再探索させない。
    - filesystem/Git/package の収集は export/application 層に留める。
See:
    architecture.md §9
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from grafix.core.parameters.runtime import LoadProvenance
from grafix.core.runtime_config import RuntimeConfig
from grafix.core.value_validation import (
    exact_bool,
    exact_integer,
    exact_string,
    exact_string_choice,
    finite_real,
)

_HASH_ALGORITHM = "sha256"


def normalize_provenance_seed(seed: object, *, parameter_name: str) -> int | None:
    if seed is None:
        return None
    return exact_integer(seed, name=parameter_name)


def sha256_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def provenance_json_value(value: object) -> object:
    """owned provenance schema の値を strict JSON value へ射影する。"""

    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("provenance JSON numbers must be finite")
        return value
    if isinstance(value, Path):
        return str(value)
    if type(value) is dict:
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("provenance JSON object keys must be exact strings")
            normalized[key] = provenance_json_value(item)
        return normalized
    if isinstance(value, (list, tuple)):
        if type(value) not in {list, tuple}:
            raise TypeError(
                f"unsupported provenance JSON sequence: {type(value)!r}"
            )
        return [provenance_json_value(item) for item in value]
    raise TypeError(f"unsupported provenance JSON value: {type(value)!r}")


def canonical_provenance_json(value: object) -> str:
    return json.dumps(
        provenance_json_value(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """draw callable と、その hash 対象。"""

    module: str | None
    qualname: str | None
    path: Path | None
    sha256: str | None
    hash_scope: Literal["validated_source_bytes", "file", "callable_source"] | None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        for name in ("module", "qualname", "sha256", "unavailable_reason"):
            value = getattr(self, name)
            if value is not None:
                exact_string(value, name=name)
        if self.path is not None and not isinstance(self.path, Path):
            raise TypeError("path は Path または None である必要があります")
        if self.hash_scope is not None:
            exact_string_choice(
                self.hash_scope,
                name="hash_scope",
                choices=("validated_source_bytes", "file", "callable_source"),
            )

    @property
    def available(self) -> bool:
        return self.sha256 is not None

    def as_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "module": self.module,
            "qualname": self.qualname,
            "path": None if self.path is None else str(self.path),
            "hash": (
                None
                if self.sha256 is None
                else {"algorithm": _HASH_ALGORITHM, "value": self.sha256}
            ),
            "hash_scope": self.hash_scope,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True, slots=True)
class GitProvenance:
    """source を含む Git repository の取得結果。"""

    available: bool
    root: Path | None = None
    commit: str | None = None
    dirty: bool | None = None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        exact_bool(self.available, name="available")
        if self.root is not None and not isinstance(self.root, Path):
            raise TypeError("root は Path または None である必要があります")
        for name in ("commit", "unavailable_reason"):
            value = getattr(self, name)
            if value is not None:
                exact_string(value, name=name)
        if self.dirty is not None:
            exact_bool(self.dirty, name="dirty")

    def as_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "root": None if self.root is None else str(self.root),
            "commit": self.commit,
            "dirty": self.dirty,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True, slots=True)
class ConfigProvenance:
    """effective RuntimeConfig の immutable JSON snapshot。"""

    path: Path | None
    effective_json: str
    sha256: str

    def __post_init__(self) -> None:
        if self.path is not None and not isinstance(self.path, Path):
            raise TypeError("path は Path または None である必要があります")
        exact_string(self.effective_json, name="effective_json")
        exact_string(self.sha256, name="sha256")

    @classmethod
    def from_config(cls, config: RuntimeConfig) -> ConfigProvenance:
        if not isinstance(config, RuntimeConfig):
            raise TypeError("config は RuntimeConfig である必要があります")
        effective_json = canonical_provenance_json(asdict(config))
        return cls(
            path=config.config_path,
            effective_json=effective_json,
            sha256=sha256_digest(effective_json.encode("utf-8")),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "path": None if self.path is None else str(self.path),
            "effective": json.loads(self.effective_json),
            "snapshot_hash": {
                "algorithm": _HASH_ALGORITHM,
                "value": self.sha256,
            },
        }


@dataclass(frozen=True, slots=True)
class SessionProvenance:
    """RenderSession または interactive run の開始時に一度だけ探索する情報。"""

    grafix_version: str
    source: SourceProvenance
    git: GitProvenance
    config: ConfigProvenance
    parameter_source: str
    parameter_store_path: Path | None
    parameter_load_provenance: LoadProvenance
    seed: int | None

    def __post_init__(self) -> None:
        exact_string(self.grafix_version, name="grafix_version")
        if not isinstance(self.source, SourceProvenance):
            raise TypeError("source は SourceProvenance である必要があります")
        if not isinstance(self.git, GitProvenance):
            raise TypeError("git は GitProvenance である必要があります")
        if not isinstance(self.config, ConfigProvenance):
            raise TypeError("config は ConfigProvenance である必要があります")
        exact_string(self.parameter_source, name="parameter_source")
        if self.parameter_store_path is not None and not isinstance(
            self.parameter_store_path,
            Path,
        ):
            raise TypeError("parameter_store_path は Path または None である必要があります")
        exact_string_choice(
            self.parameter_load_provenance,
            name="parameter_load_provenance",
            choices=("primary", "session_recovery", "quarantined"),
        )
        object.__setattr__(
            self,
            "seed",
            normalize_provenance_seed(self.seed, parameter_name="seed"),
        )


@dataclass(frozen=True, slots=True)
class ParameterSnapshotProvenance:
    """1 frame の実効 parameter snapshot を識別する hash。"""

    revision: int
    entry_count: int
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "revision",
            exact_integer(self.revision, name="revision", minimum=0),
        )
        object.__setattr__(
            self,
            "entry_count",
            exact_integer(self.entry_count, name="entry_count", minimum=0),
        )
        exact_string(self.sha256, name="sha256")


@dataclass(frozen=True, slots=True)
class FrameProvenance:
    """評価済み frame と parameter snapshot の結び付き。"""

    t: float
    frame_index: int | None
    quality: Literal["draft", "final"]
    origin: Literal["headless", "interactive"]
    parameters: ParameterSnapshotProvenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "t", finite_real(self.t, name="t"))
        if self.frame_index is not None:
            object.__setattr__(
                self,
                "frame_index",
                exact_integer(self.frame_index, name="frame_index", minimum=0),
            )
        object.__setattr__(
            self,
            "quality",
            exact_string_choice(
                self.quality,
                name="quality",
                choices=("draft", "final"),
            ),
        )
        object.__setattr__(
            self,
            "origin",
            exact_string_choice(
                self.origin,
                name="origin",
                choices=("headless", "interactive"),
            ),
        )
        if not isinstance(self.parameters, ParameterSnapshotProvenance):
            raise TypeError(
                "parameters は ParameterSnapshotProvenance である必要があります"
            )


@dataclass(frozen=True, slots=True)
class CaptureProvenance:
    """worker が再探索せず manifest へ直列化できる完全 provenance。"""

    session: SessionProvenance
    frame: FrameProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.session, SessionProvenance):
            raise TypeError("session は SessionProvenance である必要があります")
        if not isinstance(self.frame, FrameProvenance):
            raise TypeError("frame は FrameProvenance である必要があります")

    def manifest_sections(self) -> dict[str, object]:
        session = self.session
        parameters = self.frame.parameters
        return {
            "grafix": {"version": session.grafix_version},
            "source": session.source.as_dict(),
            "git": session.git.as_dict(),
            "config": session.config.as_dict(),
            "parameters": {
                "source": session.parameter_source,
                "store_path": (
                    None
                    if session.parameter_store_path is None
                    else str(session.parameter_store_path)
                ),
                "load_provenance": session.parameter_load_provenance,
                "revision": parameters.revision,
                "entry_count": parameters.entry_count,
                "snapshot_hash": {
                    "algorithm": _HASH_ALGORITHM,
                    "value": parameters.sha256,
                },
            },
            "seed": session.seed,
            "frame": {
                "t": self.frame.t,
                "index": self.frame.frame_index,
                "quality": self.frame.quality,
                "origin": self.frame.origin,
            },
        }




__all__ = [
    "CaptureProvenance",
    "ConfigProvenance",
    "FrameProvenance",
    "GitProvenance",
    "ParameterSnapshotProvenance",
    "SessionProvenance",
    "SourceProvenance",
    "canonical_provenance_json",
    "normalize_provenance_seed",
    "provenance_json_value",
    "sha256_digest",
]
