"""
Purpose:
    parameter、label、effect topologyの一frame分の観測をcanonical recordとして受け渡す。
Use when:
    DSLやworkerからframe終端のParamStore mergeへ新しい観測情報を運ぶ場合。
Constraints:
    - recordはprocess間で渡せるimmutableかつcanonicalな値だけを保持する。
    - bufferはframe-localとし、producerからlive storeを直接変更しない。
    - 途中までのbufferは失敗frameでmergeせず、成功境界でまとめて確定する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from grafix.core.value_validation import (
    exact_bool,
    exact_string,
    exact_string_choice,
)

from .effects import EffectStepTopology
from .identity import identity_string
from .key import ParameterKey
from .meta import ParamMeta
from .source import ValueSource
from .validation import validate_parameter_value


@dataclass(frozen=True, slots=True, kw_only=True)
class FrameParamRecord:
    """1 引数ぶんの観測・解決結果。"""

    key: ParameterKey
    base: Any
    meta: ParamMeta
    effective: Any
    source: ValueSource
    explicit: bool

    def __post_init__(self) -> None:
        """process 間で渡せる canonical な観測レコードへ固定する。"""

        if type(self.key) is not ParameterKey:
            raise TypeError("key は ParameterKey である必要があります")
        if type(self.meta) is not ParamMeta:
            raise TypeError("meta は ParamMeta である必要があります")
        object.__setattr__(
            self,
            "base",
            validate_parameter_value(
                self.base,
                kind=self.meta.kind,
                choices=self.meta.choices,
            ),
        )
        object.__setattr__(
            self,
            "effective",
            validate_parameter_value(
                self.effective,
                kind=self.meta.kind,
                choices=self.meta.choices,
            ),
        )
        object.__setattr__(
            self,
            "source",
            cast(
                ValueSource,
                exact_string_choice(
                    self.source,
                    name="source",
                    choices=("code", "ui", "midi_live", "midi_frozen"),
                ),
            ),
        )
        object.__setattr__(
            self,
            "explicit",
            exact_bool(self.explicit, name="explicit"),
        )


@dataclass(frozen=True, slots=True)
class FrameLabelRecord:
    """(op, site_id) に紐づくラベル設定の記録。"""

    op: str
    site_id: str
    label: str

    def __post_init__(self) -> None:
        identity_string(self.op, name="FrameLabelRecord.op")
        identity_string(self.site_id, name="FrameLabelRecord.site_id")
        exact_string(self.label, name="FrameLabelRecord.label")


@dataclass(frozen=True, slots=True)
class FrameEffectChainRecord:
    """1回のEffectBuilder適用で観測した完全なcode topology。"""

    chain_id: str
    steps: tuple[EffectStepTopology, ...]

    def __post_init__(self) -> None:
        identity_string(self.chain_id, name="FrameEffectChainRecord.chain_id")
        if type(self.steps) is not tuple or not all(
            type(step) is EffectStepTopology for step in self.steps
        ):
            raise TypeError("FrameEffectChainRecord.steps は topology tuple である必要があります")


class FrameParamsBuffer:
    """フレーム内のパラメータ観測を蓄積する単純なバッファ。"""

    def __init__(self) -> None:
        self._records: list[FrameParamRecord] = []
        self._labels: list[FrameLabelRecord] = []
        self._effect_chains: list[FrameEffectChainRecord] = []
        self._effect_chain_observation_complete = False

    def record(
        self,
        *,
        key: ParameterKey,
        base: Any,
        meta: ParamMeta,
        effective: Any,
        source: ValueSource,
        explicit: bool,
    ) -> None:
        self._records.append(
            FrameParamRecord(
                key=key,
                base=base,
                meta=meta,
                effective=effective,
                source=source,
                explicit=explicit,
            )
        )

    def set_label(self, *, op: str, site_id: str, label: str) -> None:
        self._labels.append(
            FrameLabelRecord(op=op, site_id=site_id, label=label)
        )

    def record_effect_chain(
        self,
        *,
        chain_id: str,
        steps: tuple[EffectStepTopology, ...],
    ) -> None:
        """effect chainのcode topologyを記録する。"""

        self._effect_chains.append(
            FrameEffectChainRecord(
                chain_id=chain_id,
                steps=tuple(steps),
            )
        )

    @property
    def records(self) -> list[FrameParamRecord]:
        return self._records

    @property
    def labels(self) -> list[FrameLabelRecord]:
        return self._labels

    @property
    def effect_chains(self) -> list[FrameEffectChainRecord]:
        return self._effect_chains

    @property
    def effect_chain_observation_complete(self) -> bool:
        """このbufferが一つの完全な成功evaluationを表すか返す。"""

        return bool(self._effect_chain_observation_complete)

    def complete_effect_chain_observation(self) -> None:
        """effect chainが0件の場合も含め、成功evaluationの完了を記録する。"""

        self._effect_chain_observation_complete = True

    def clear(self) -> None:
        self._records.clear()
        self._labels.clear()
        self._effect_chains.clear()
        self._effect_chain_observation_complete = False
