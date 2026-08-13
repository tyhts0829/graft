"""
Purpose:
    capture manifest系テストへrepositoryやsource fileに依存しないprovenance fixtureを提供する。
Use when:
    export/publish/manifestのテストで、内容が安定した有効なCaptureProvenanceが必要なとき。
Constraints:
    - sourceとGitは意図的にunavailableとして表し、実repositoryを探索しない。
    - 時刻以外の値を決定的に保ち、テスト対象外の環境差をmanifestへ混入させない。
    - quality、origin、parameter revisionは完成captureを表す固定値を維持する。
"""

from __future__ import annotations

from grafix.core.capture_provenance import (
    CaptureProvenance,
    ConfigProvenance,
    FrameProvenance,
    GitProvenance,
    ParameterSnapshotProvenance,
    SessionProvenance,
    SourceProvenance,
)


def _capture_provenance(t: float) -> CaptureProvenance:
    return CaptureProvenance(
        session=SessionProvenance(
            grafix_version="test",
            source=SourceProvenance(
                module=None,
                qualname=None,
                path=None,
                sha256=None,
                hash_scope=None,
                unavailable_reason="test fixture has no source file",
            ),
            git=GitProvenance(
                available=False,
                unavailable_reason="test fixture is repository independent",
            ),
            config=ConfigProvenance(
                path=None,
                effective_json="{}",
                sha256="config-sha256",
            ),
            parameter_source="test",
            parameter_store_path=None,
            parameter_load_provenance="primary",
            seed=None,
        ),
        frame=FrameProvenance(
            t=t,
            frame_index=None,
            quality="final",
            origin="headless",
            parameters=ParameterSnapshotProvenance(
                revision=0,
                entry_count=0,
                sha256="parameters-sha256",
            ),
        ),
    )
