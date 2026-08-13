"""
Purpose:
    operation・scene・CPU/GPU cache・capture queue の resource 上限を quality 別の immutable runtime profile に束ねる。
Use when:
    preview/final の resource policy、cache 容量、capture admission 上限の配線を変更・調査する場合。
Constraints:
    - draft preview と final capture の profile を独立に選択できる状態を維持する。
    - ここは limit value の正本に限定し、allocation・cache・queue それぞれの enforcement は対応 owner に留める。
    - profile は mutable runtime state や使用量 counter を保持しない。
See:
    grafix.core.resource_budget
"""

from __future__ import annotations

from dataclasses import dataclass

from grafix.core.preview_quality import PreviewQuality
from grafix.core.resource_budget import DEFAULT_RESOURCE_BUDGET, ResourceBudget
from grafix.core.value_validation import exact_integer

DEFAULT_CPU_CACHE_BYTES = 256 * 1024 * 1024
DEFAULT_CPU_CACHE_ENTRIES = 4096
DEFAULT_GPU_CACHE_BYTES = 256 * 1024 * 1024
DEFAULT_CAPTURE_QUEUE_PENDING_JOBS = 16
DEFAULT_CAPTURE_QUEUE_BYTES = int(DEFAULT_RESOURCE_BUDGET.max_output_bytes)


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    """1 quality profile の operation/scene/cache/capture 上限。

    Parameters
    ----------
    per_operation : ResourceBudget
        primitive/effect 1 回ごとの計算量・出力量上限。
    scene : ResourceBudget
        1 scene 全体の集約上限。
    cpu_cache_bytes : int
        realize cache が保持できる推定 byte 数。
    cpu_cache_entries : int
        realize cache が保持できる entry 数。
    gpu_cache_bytes : int
        renderer の GPU mesh cache 上限。
    capture_queue_pending_jobs : int
        in-flight を含む capture request 件数上限。
    capture_queue_bytes : int
        process copy を含めた capture snapshot の推定 byte 上限。
    """

    per_operation: ResourceBudget = DEFAULT_RESOURCE_BUDGET
    scene: ResourceBudget = DEFAULT_RESOURCE_BUDGET
    cpu_cache_bytes: int = DEFAULT_CPU_CACHE_BYTES
    cpu_cache_entries: int = DEFAULT_CPU_CACHE_ENTRIES
    gpu_cache_bytes: int = DEFAULT_GPU_CACHE_BYTES
    capture_queue_pending_jobs: int = DEFAULT_CAPTURE_QUEUE_PENDING_JOBS
    capture_queue_bytes: int = DEFAULT_CAPTURE_QUEUE_BYTES

    def __post_init__(self) -> None:
        if not isinstance(self.per_operation, ResourceBudget):
            raise TypeError("per_operation は ResourceBudget である必要があります")
        if not isinstance(self.scene, ResourceBudget):
            raise TypeError("scene は ResourceBudget である必要があります")
        for name in (
            "cpu_cache_bytes",
            "cpu_cache_entries",
            "gpu_cache_bytes",
            "capture_queue_pending_jobs",
            "capture_queue_bytes",
        ):
            object.__setattr__(
                self,
                name,
                exact_integer(getattr(self, name), name=name, minimum=0),
            )

    @property
    def gpu_candidate_cache_bytes(self) -> int:
        """GPU mesh cache 手前の index candidate 用上限を返す。"""

        return self.gpu_cache_bytes // 4


@dataclass(frozen=True, slots=True)
class RuntimeLimitProfiles:
    """interactive preview と final capture の独立した上限 profile。

    Parameters
    ----------
    preview : RuntimeLimits
        draft preview 評価へ適用する上限。
    final : RuntimeLimits
        final capture 評価へ適用する上限。
    """

    preview: RuntimeLimits
    final: RuntimeLimits

    def __post_init__(self) -> None:
        if not isinstance(self.preview, RuntimeLimits):
            raise TypeError("preview は RuntimeLimits である必要があります")
        if not isinstance(self.final, RuntimeLimits):
            raise TypeError("final は RuntimeLimits である必要があります")

    def for_quality(self, quality: PreviewQuality) -> RuntimeLimits:
        """指定 quality に対応する profile を返す。"""

        if quality == "draft":
            return self.preview
        if quality == "final":
            return self.final
        raise ValueError(f"unknown quality: {quality!r}")


DEFAULT_PREVIEW_RUNTIME_LIMITS = RuntimeLimits()
DEFAULT_FINAL_RUNTIME_LIMITS = RuntimeLimits()
DEFAULT_RUNTIME_LIMIT_PROFILES = RuntimeLimitProfiles(
    preview=DEFAULT_PREVIEW_RUNTIME_LIMITS,
    final=DEFAULT_FINAL_RUNTIME_LIMITS,
)


__all__ = [
    "DEFAULT_CAPTURE_QUEUE_BYTES",
    "DEFAULT_CAPTURE_QUEUE_PENDING_JOBS",
    "DEFAULT_CPU_CACHE_BYTES",
    "DEFAULT_CPU_CACHE_ENTRIES",
    "DEFAULT_FINAL_RUNTIME_LIMITS",
    "DEFAULT_GPU_CACHE_BYTES",
    "DEFAULT_PREVIEW_RUNTIME_LIMITS",
    "DEFAULT_RUNTIME_LIMIT_PROFILES",
    "RuntimeLimitProfiles",
    "RuntimeLimits",
]
