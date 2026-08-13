"""
Purpose:
    ParamStoreの非変更read、明示recovery、atomic commitをfilesystem境界で所有する。
Use when:
    parameter fileのload-state、quarantine、recovery、または永続化方針を変更する場合。
Constraints:
    - 通常readは原本を変更せず、破損fileの移動は明示recovery経路だけで行う。
    - load provenance/diagnosticsをstoreへ埋め込まず、`ParamStoreLoadResult`に保持する。
Side effects:
    recoveryとwriteはparameter fileの作成、移動、置換を行い得る。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from grafix.core.parameters.codec import (
    ParamStoreDecodeResult,
    ParamStoreSchemaError,
    UnsupportedParamStoreSchemaError,
    dumps_param_store,
    loads_param_store_result,
    param_store_schema_version,
)
from grafix.core.parameters.known_operations import KnownOperationSchemaSnapshot
from grafix.core.parameters.prune_ops import prune_unknown_args_in_known_ops
from grafix.core.parameters.runtime import (
    LoadProvenance,
    ParameterLoadState,
    ParamStoreLoadDiagnostic,
)
from grafix.core.parameters.store import ParamStore
from grafix.file_io import atomic_write_text

_logger = logging.getLogger(__name__)

ParamStoreLoadStatus = Literal["missing", "loaded", "partial", "invalid"]


@dataclass(frozen=True, slots=True)
class ParamStoreLoadResult:
    """storage operation が確定した store と load state。"""

    store: ParamStore
    status: ParamStoreLoadStatus
    load_state: ParameterLoadState
    error: Exception | None = None


def _load_result(
    store: ParamStore,
    *,
    status: ParamStoreLoadStatus,
    provenance: LoadProvenance,
    diagnostics: tuple[ParamStoreLoadDiagnostic, ...] = (),
    error: Exception | None = None,
) -> ParamStoreLoadResult:
    return ParamStoreLoadResult(
        store=store,
        status=status,
        load_state=ParameterLoadState(
            provenance=provenance,
            diagnostics=diagnostics,
        ),
        error=error,
    )


def _finish_read_result(
    result: ParamStoreDecodeResult,
    *,
    source: Path,
) -> ParamStoreLoadResult:
    """decode 結果を、原本を変更しない read result へ変換する。"""

    if not result.issues:
        return _load_result(
            result.store,
            status="loaded",
            provenance="primary",
        )

    details = "\n".join(issue.describe() for issue in result.issues)
    diagnostic = ParamStoreLoadDiagnostic(
        code="partial_read",
        summary=(
            f"ParamStore の不正 entry {len(result.issues)} 件を除外し、"
            "原本を変更せず読み込みました"
        ),
        details=details,
        backup_path=None,
    )
    _logger.warning(
        "部分破損した ParamStore を非変更で読み込みました: "
        "source=%s issues=%d\n%s",
        source,
        len(result.issues),
        details,
    )
    return _load_result(
        result.store,
        status="partial",
        provenance="primary",
        diagnostics=(diagnostic,),
    )


def _invalid_read_result(path: Path, error: Exception) -> ParamStoreLoadResult:
    """decode できない原本を残し、空 store と診断を返す。"""

    diagnostic = ParamStoreLoadDiagnostic(
        code="load_error",
        summary="壊れた ParamStore を変更せず、空の状態で読み込みました",
        details=str(error),
        backup_path=None,
    )
    _logger.warning(
        "壊れた ParamStore を非変更で読み込みました: source=%s error=%s",
        path,
        error,
    )
    return _load_result(
        ParamStore(),
        status="invalid",
        provenance="primary",
        diagnostics=(diagnostic,),
        error=error,
    )


def read_param_store(path: Path) -> ParamStoreLoadResult:
    """JSON を読み込み、rename、write、unlink を行わず結果を返す。"""

    source = Path(path)
    try:
        payload = source.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _load_result(
            ParamStore(),
            status="missing",
            provenance="primary",
        )
    except UnicodeError as error:
        return _invalid_read_result(source, error)

    try:
        result = loads_param_store_result(payload)
    except UnsupportedParamStoreSchemaError:
        raise
    except (json.JSONDecodeError, ParamStoreSchemaError, TypeError) as error:
        return _invalid_read_result(source, error)
    return _finish_read_result(result, source=source)


def param_store_recovery_path(path: Path) -> Path:
    """通常保存と分離した live session recovery path を返す。"""

    target = Path(path)
    return target.with_name(f"{target.stem}.session{target.suffix}")


def _quarantine_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.corrupt-{os.getpid()}-{time.time_ns()}")


def _finish_recovered_store(
    result: ParamStoreDecodeResult,
    *,
    source: Path,
    provenance: LoadProvenance,
    repaired_recovery_path: Path | None = None,
) -> ParamStoreLoadResult:
    """partial decode の原本を退避し、修復済み journal を確定する。"""

    if not result.issues:
        return _load_result(
            result.store,
            status="loaded",
            provenance=provenance,
        )

    backup_path = _quarantine_path(source)
    os.replace(source, backup_path)
    details = "\n".join(issue.describe() for issue in result.issues)
    diagnostic = ParamStoreLoadDiagnostic(
        code="partial_quarantine",
        summary=(
            f"ParamStore の不正 entry {len(result.issues)} 件を除外し、"
            "原本を退避しました"
        ),
        details=details,
        backup_path=backup_path,
    )
    _logger.warning(
        "部分破損した ParamStore を退避しました: "
        "source=%s backup=%s issues=%d\n%s",
        source,
        backup_path,
        len(result.issues),
        details,
    )
    loaded = _load_result(
        result.store,
        status="partial",
        provenance="quarantined",
        diagnostics=(diagnostic,),
    )
    if repaired_recovery_path is not None:
        try:
            write_param_store_recovery(loaded.store, repaired_recovery_path)
        except Exception:
            # journal 確定前の失敗では、元の source を即座に戻す。
            os.replace(backup_path, source)
            raise
    return loaded


def _quarantine_failure(
    *,
    source: Path,
    error: Exception,
    summary: str,
    code: str,
) -> tuple[Path, ParamStoreLoadDiagnostic]:
    backup_path = _quarantine_path(source)
    os.replace(source, backup_path)
    return backup_path, ParamStoreLoadDiagnostic(
        code=code,
        summary=summary,
        details=str(error),
        backup_path=backup_path,
    )


def _quarantine_primary_and_return_empty(
    path: Path,
    error: Exception,
) -> ParamStoreLoadResult:
    backup_path, diagnostic = _quarantine_failure(
        source=path,
        error=error,
        summary="壊れた ParamStore を退避し、空の状態で起動しました",
        code="load_quarantine",
    )
    _logger.warning(
        "壊れた ParamStore を退避しました: source=%s backup=%s error=%s",
        path,
        backup_path,
        error,
    )
    return _load_result(
        ParamStore(),
        status="invalid",
        provenance="quarantined",
        diagnostics=(diagnostic,),
        error=error,
    )


def recover_primary_param_store(path: Path) -> ParamStoreLoadResult:
    """primary の破損を明示的に quarantine/recovery し、結果を返す。"""

    primary = Path(path)
    try:
        payload = primary.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _load_result(
            ParamStore(),
            status="missing",
            provenance="primary",
        )
    except UnicodeError as error:
        return _quarantine_primary_and_return_empty(primary, error)

    try:
        result = loads_param_store_result(payload)
    except UnsupportedParamStoreSchemaError:
        raise
    except (json.JSONDecodeError, ParamStoreSchemaError, TypeError) as error:
        return _quarantine_primary_and_return_empty(primary, error)
    return _finish_recovered_store(
        result,
        source=primary,
        provenance="primary",
        repaired_recovery_path=param_store_recovery_path(primary),
    )


def _reject_unsupported_schema_file(path: Path) -> None:
    """recovery 選択で上書き得る非現行 schema を先に拒否する。"""

    try:
        payload = path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeError):
        return
    try:
        obj = json.loads(payload)
    except (json.JSONDecodeError, UnicodeError):
        return
    try:
        param_store_schema_version(obj)
    except UnsupportedParamStoreSchemaError:
        raise
    except (ParamStoreSchemaError, TypeError):
        return


def _quarantine_recovery_and_load_primary(
    *,
    recovery: Path,
    primary: Path,
    error: Exception,
) -> ParamStoreLoadResult:
    """壊れた recovery を退避し、primary へフォールバックする。"""

    corrupt_path, diagnostic = _quarantine_failure(
        source=recovery,
        error=error,
        summary=("壊れた ParamStore session recovery を退避し、primary を読み込みました"),
        code="recovery_quarantine",
    )
    _logger.warning(
        "壊れた ParamStore session recovery を退避しました: "
        "source=%s backup=%s error=%s",
        recovery,
        corrupt_path,
        error,
    )
    primary_result = recover_primary_param_store(primary)
    return _load_result(
        primary_result.store,
        status="invalid",
        provenance="quarantined",
        diagnostics=(diagnostic, *primary_result.load_state.diagnostics),
        error=error,
    )


def recover_param_store_session(path: Path) -> ParamStoreLoadResult:
    """明示的に primary/recovery を選択し、破損時は quarantine する。"""

    primary = Path(path)
    recovery = param_store_recovery_path(primary)
    try:
        recovery_mtime = recovery.stat().st_mtime_ns
    except FileNotFoundError:
        return recover_primary_param_store(primary)
    try:
        primary_mtime = primary.stat().st_mtime_ns
    except FileNotFoundError:
        primary_mtime = -1

    _reject_unsupported_schema_file(primary)
    _reject_unsupported_schema_file(recovery)
    if recovery_mtime <= primary_mtime:
        return recover_primary_param_store(primary)

    try:
        payload = recovery.read_text(encoding="utf-8")
    except FileNotFoundError:
        return recover_primary_param_store(primary)
    except UnicodeError as error:
        return _quarantine_recovery_and_load_primary(
            recovery=recovery,
            primary=primary,
            error=error,
        )

    try:
        result = loads_param_store_result(
            payload,
            preserve_explicit_overrides=True,
        )
    except UnsupportedParamStoreSchemaError:
        raise
    except (json.JSONDecodeError, ParamStoreSchemaError, TypeError) as error:
        return _quarantine_recovery_and_load_primary(
            recovery=recovery,
            primary=primary,
            error=error,
        )

    loaded = _finish_recovered_store(
        result,
        source=recovery,
        provenance="session_recovery",
        repaired_recovery_path=recovery,
    )
    _logger.warning("未完了 session の ParamStore を復元しました: %s", recovery)
    return loaded


def write_param_store(store: ParamStore, path: Path) -> None:
    """ParamStore を変更せず、通常保存用 JSON を atomic に書く。"""

    atomic_write_text(path, dumps_param_store(store) + "\n")


def write_param_store_recovery(store: ParamStore, path: Path) -> None:
    """live override を保持した recovery journal を atomic に書く。"""

    atomic_write_text(
        path,
        dumps_param_store(store, preserve_explicit_overrides=True) + "\n",
    )


def discard_param_store_recovery(primary_path: Path) -> None:
    """primary に対応する recovery journal を明示的に削除する。"""

    param_store_recovery_path(primary_path).unlink(missing_ok=True)


def finalize_parameter_session(
    store: ParamStore,
    path: Path,
    *,
    known_operations: KnownOperationSchemaSnapshot,
) -> None:
    """既知 schema で prune して primary を書き、成功時だけ recovery を削除する。"""

    removed_unknown = prune_unknown_args_in_known_ops(store, known_operations)
    if removed_unknown:
        pairs = sorted({(str(key.op), str(key.arg)) for key in removed_unknown})
        preview = ", ".join(f"{op}.{arg}" for op, arg in pairs[:10])
        suffix = "" if len(pairs) <= 10 else ", ..."
        _logger.warning(
            "未登録引数を永続化から削除しました: count=%d pairs=%d [%s%s]",
            len(removed_unknown),
            len(pairs),
            preview,
            suffix,
        )

    primary = Path(path)
    write_param_store(store, primary)
    try:
        discard_param_store_recovery(primary)
    except OSError:
        # primary の atomic commit は完了している。旧 recovery の削除失敗は
        # commit 自体を失敗扱いにせず、次回 retry 可能な cleanup failure として残す。
        _logger.exception(
            "primary commit 後に ParamStore session recovery を削除できませんでした: %s",
            param_store_recovery_path(primary),
        )


__all__ = [
    "ParamStoreLoadResult",
    "ParamStoreLoadStatus",
    "discard_param_store_recovery",
    "finalize_parameter_session",
    "param_store_recovery_path",
    "read_param_store",
    "recover_param_store_session",
    "recover_primary_param_store",
    "write_param_store",
    "write_param_store_recovery",
]
