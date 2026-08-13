"""
Purpose:
    source、Git、設定、parameter状態をcaptureに結び付ける再現性metadataを構築する。
Use when:
    capture provenanceの入力、取得時点、parameter状態との整合性を検討するとき。
Constraints:
    - source/Git/configの発見はbuilder構築時に固定し、frame生成ごとに再探索しない。
    - parameterのsourceとload provenanceは同じprovider sampleから導出する。
    - parameter hashはeffective値と保存対象storeをrevision情報ごと同一snapshotに束縛する。
Side effects:
    builder構築時にsourceとpackage metadataを読み、期限付きGit subprocessを呼び出し得る。
"""

from __future__ import annotations

import inspect
import subprocess
from collections.abc import Callable
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

from grafix.core.capture_provenance import (
    CaptureProvenance,
    ConfigProvenance,
    FrameProvenance,
    GitProvenance,
    ParameterSnapshotProvenance,
    SessionProvenance,
    SourceProvenance,
    canonical_provenance_json,
    normalize_provenance_seed,
    provenance_json_value,
    sha256_digest,
)
from grafix.core.parameters.codec import encode_param_store
from grafix.core.parameters.runtime import ParameterCaptureState
from grafix.core.parameters.store import ParamStore
from grafix.core.runtime_config import RuntimeConfig
from grafix.core.value_validation import exact_string

_GIT_TIMEOUT_S = 2.0


def _parameter_source_text(source: str | Path) -> str:
    if type(source) is str:
        return exact_string(source, name="parameter_state.source")
    if isinstance(source, Path):
        return str(source)
    raise TypeError("parameter_state.source は str または Path である必要があります")


def _snapshot_source(draw: Callable[[float], object]) -> SourceProvenance:
    draw_type = type(draw)
    module = getattr(draw, "__module__", getattr(draw_type, "__module__", None))
    qualname = getattr(
        draw,
        "__qualname__",
        getattr(draw, "__name__", getattr(draw_type, "__qualname__", None)),
    )
    explicit_path = getattr(draw, "__grafix_source_path__", None)
    path: Path | None = None
    if explicit_path is not None:
        explicit_text = str(explicit_path).strip()
        if explicit_text and not (
            explicit_text.startswith("<") and explicit_text.endswith(">")
        ):
            path = Path(explicit_text).expanduser().resolve(strict=False)

    validated_source = getattr(draw, "__grafix_source_bytes__", None)
    if isinstance(validated_source, (bytes, bytearray, memoryview)):
        payload = bytes(validated_source)
        return SourceProvenance(
            module=None if module is None else str(module),
            qualname=None if qualname is None else str(qualname),
            path=path,
            sha256=sha256_digest(payload),
            hash_scope="validated_source_bytes",
        )

    code = getattr(draw, "__code__", None)
    filename = getattr(code, "co_filename", None)
    if path is None and filename and not (
        str(filename).startswith("<") and str(filename).endswith(">")
    ):
        path = Path(str(filename)).expanduser().resolve(strict=False)
    elif path is None:
        try:
            found = inspect.getsourcefile(draw) or inspect.getfile(draw)
        except (OSError, TypeError):
            found = None
        if found:
            path = Path(found).expanduser().resolve(strict=False)

    if path is not None:
        try:
            payload = path.read_bytes()
        except OSError as exc:
            file_error = f"source file could not be read: {type(exc).__name__}"
        else:
            return SourceProvenance(
                module=None if module is None else str(module),
                qualname=None if qualname is None else str(qualname),
                path=path,
                sha256=sha256_digest(payload),
                hash_scope="file",
            )
    else:
        file_error = "source file is unavailable"

    try:
        source = inspect.getsource(draw)
    except (OSError, TypeError):
        return SourceProvenance(
            module=None if module is None else str(module),
            qualname=None if qualname is None else str(qualname),
            path=path,
            sha256=None,
            hash_scope=None,
            unavailable_reason=file_error,
        )
    return SourceProvenance(
        module=None if module is None else str(module),
        qualname=None if qualname is None else str(qualname),
        path=path,
        sha256=sha256_digest(source.encode("utf-8")),
        hash_scope="callable_source",
    )


def _run_git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        check=False,
        text=True,
        timeout=_GIT_TIMEOUT_S,
    )


def _snapshot_git(source_path: Path | None) -> GitProvenance:
    if source_path is None:
        return GitProvenance(
            available=False,
            unavailable_reason="source path is unavailable",
        )
    cwd = source_path.parent
    try:
        root_result = _run_git("rev-parse", "--show-toplevel", cwd=cwd)
        if root_result.returncode != 0:
            return GitProvenance(
                available=False,
                unavailable_reason="source is not inside an accessible Git repository",
            )
        root = Path(root_result.stdout.strip()).resolve(strict=False)
        commit_result = _run_git("rev-parse", "HEAD", cwd=root)
        status_result = _run_git("status", "--porcelain", cwd=root)
    except (OSError, subprocess.SubprocessError) as exc:
        return GitProvenance(
            available=False,
            unavailable_reason=f"Git metadata lookup failed: {type(exc).__name__}",
        )
    if commit_result.returncode != 0 or status_result.returncode != 0:
        return GitProvenance(
            available=False,
            root=root,
            unavailable_reason="Git commit or status could not be read",
        )
    return GitProvenance(
        available=True,
        root=root,
        commit=commit_result.stdout.strip(),
        dirty=bool(status_result.stdout.strip()),
    )


def _grafix_version() -> str:
    try:
        return version("grafix")
    except PackageNotFoundError:
        return "unknown"


def _parameter_snapshot(store: ParamStore) -> ParameterSnapshotProvenance:
    runtime = store.runtime_view()
    effective_entries = [
        {
            "op": key.op,
            "site_id": key.site_id,
            "arg": key.arg,
            "value": provenance_json_value(value),
            "source": (
                None
                if runtime.last_source_by_key.get(key) is None
                else runtime.last_source_by_key[key]
            ),
        }
        for key, value in sorted(
            runtime.last_effective_by_key.items(),
            key=lambda item: (item[0].op, item[0].site_id, item[0].arg),
        )
    ]
    # draw が parameter を使わない場合も、空 snapshot を一意に識別する。永続 store
    # payload も含め、未観測 UI state の違いを同じ hash と誤認しない。
    payload = {
        "effective": effective_entries,
        "store": encode_param_store(store, preserve_explicit_overrides=True),
    }
    text = canonical_provenance_json(payload)
    return ParameterSnapshotProvenance(
        revision=store.revision,
        entry_count=len(effective_entries),
        sha256=sha256_digest(text.encode("utf-8")),
    )


class CaptureProvenanceBuilder:
    """filesystem/Git/config探索を構築時だけ行い、frame snapshotは純粋に作る。"""

    def __init__(
        self,
        draw: Callable[[float], object],
        *,
        config: RuntimeConfig,
        parameter_state: ParameterCaptureState
        | Callable[[], ParameterCaptureState],
        parameter_store_path: Path | None,
        seed: int | None = None,
    ) -> None:
        if not callable(draw):
            raise TypeError("draw は callable である必要があります")
        if not isinstance(config, RuntimeConfig):
            raise TypeError("config は RuntimeConfig である必要があります")
        self._parameter_state_provider: (
            Callable[[], ParameterCaptureState] | None
        )
        if callable(parameter_state):
            self._parameter_state_provider = parameter_state
            initial_parameter_state = parameter_state()
        else:
            self._parameter_state_provider = None
            initial_parameter_state = parameter_state
        if type(initial_parameter_state) is not ParameterCaptureState:
            raise TypeError(
                "parameter_state provider は exact ParameterCaptureState を返す必要があります"
            )
        if parameter_store_path is not None and not isinstance(
            parameter_store_path,
            Path,
        ):
            raise TypeError(
                "parameter_store_path は Path または None である必要があります"
            )
        seed = normalize_provenance_seed(seed, parameter_name="seed")
        source = _snapshot_source(draw)
        self._session = SessionProvenance(
            grafix_version=_grafix_version(),
            source=source,
            git=_snapshot_git(source.path),
            config=ConfigProvenance.from_config(config),
            parameter_source=_parameter_source_text(
                initial_parameter_state.source
            ),
            parameter_store_path=parameter_store_path,
            parameter_load_provenance=initial_parameter_state.load_provenance,
            seed=seed,
        )
        # Parameter snapshot は immutable であり、store の永続状態と
        # effective/source の両 revision が同じ間は安全に共有できる。
        # 複数 store を跨いだ誤 hit を防ぐため identity も key に含める。
        self._parameter_cache_store: ParamStore | None = None
        self._parameter_cache_store_revision = -1
        self._parameter_cache_effective_revision = -1
        self._parameter_cache_snapshot: ParameterSnapshotProvenance | None = None

    @property
    def session(self) -> SessionProvenance:
        return self._session

    def _parameter_snapshot_for_store(
        self, store: ParamStore
    ) -> ParameterSnapshotProvenance:
        store_revision = store.revision
        effective_revision = store.effective_revision
        cached = self._parameter_cache_snapshot
        if (
            cached is not None
            and self._parameter_cache_store is store
            and self._parameter_cache_store_revision == store_revision
            and self._parameter_cache_effective_revision == effective_revision
        ):
            return cached

        snapshot = _parameter_snapshot(store)
        self._parameter_cache_store = store
        self._parameter_cache_store_revision = store_revision
        self._parameter_cache_effective_revision = effective_revision
        self._parameter_cache_snapshot = snapshot
        return snapshot

    def frame(
        self,
        store: ParamStore,
        *,
        t: float,
        frame_index: int | None,
        quality: Literal["draft", "final"],
        origin: Literal["headless", "interactive"],
        provenance_seed: int | None | Literal["session"] = "session",
    ) -> CaptureProvenance:
        """main process の確定済み store から immutable frame provenance を返す。

        ``provenance_seed`` が ``"session"`` なら構築時の session seed を使う。
        int/None はこの frame の provenance だけを明示的に上書きし、
        source/Git/config の再探索や乱数 global state の変更は行わない。
        parameter state provider が指定されている場合は、この frame を固定する
        時点で一度だけ取得し、source と load provenance を同じ標本から更新する。
        """

        if not isinstance(store, ParamStore):
            raise TypeError("store は ParamStore である必要があります")
        if provenance_seed == "session" and type(provenance_seed) is not str:
            raise TypeError("provenance_seed は int、None、または 'session' である必要があります")
        session = self._session
        parameter_state_provider = self._parameter_state_provider
        if parameter_state_provider is not None:
            parameter_state = parameter_state_provider()
            if type(parameter_state) is not ParameterCaptureState:
                raise TypeError(
                    "parameter_state provider は exact ParameterCaptureState を返す必要があります"
                )
            session = replace(
                session,
                parameter_source=_parameter_source_text(
                    parameter_state.source
                ),
                parameter_load_provenance=parameter_state.load_provenance,
            )
        if provenance_seed != "session":
            session = replace(
                session,
                seed=normalize_provenance_seed(
                    provenance_seed,
                    parameter_name="provenance_seed",
                ),
            )

        return CaptureProvenance(
            session=session,
            frame=FrameProvenance(
                t=t,
                frame_index=frame_index,
                quality=quality,
                origin=origin,
                parameters=self._parameter_snapshot_for_store(store),
            ),
        )




__all__ = ["CaptureProvenanceBuilder"]
