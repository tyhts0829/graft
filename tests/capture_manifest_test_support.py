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
