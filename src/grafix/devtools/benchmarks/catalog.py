"""
Purpose:
    packaged benchmark provider を一つの catalog へ収集し、suite または explicit case ID から実行対象を決定する。
Use when:
    workload provider の追加、case ID 重複、stable listing、suite/case selection を変更・調査する場合。
Constraints:
    - provider 列の正本をここに限定し、provider 側から catalog/executor/runner へ逆依存させない。
    - duplicate case ID と unknown suite/ID を拒否し、全件/suite 選択は case ID 順の決定的結果にする。
    - explicit case ID 選択では caller の要求順を保つ。
Side effects:
    catalog import 時に packaged workload provider module を import する。
"""

from __future__ import annotations

from grafix.devtools.benchmarks import (
    effect_benchmark,
    interactive_scenario_benchmark,
    mp_draw_benchmark,
    parameter_edit_benchmark,
    parameter_hotpath_benchmark,
    perf_hotpath_benchmark,
    primitive_benchmark,
    remaining_effect_benchmark,
    renderer_benchmark,
    system_benchmark,
)
from grafix.devtools.benchmarks.definition import CaseDefinition


def case_definitions() -> tuple[CaseDefinition, ...]:
    """全 provider の case を重複検証して安定順で返す。"""

    definitions = tuple(
        definition
        for provider in (
            effect_benchmark,
            remaining_effect_benchmark,
            primitive_benchmark,
            parameter_hotpath_benchmark,
            parameter_edit_benchmark,
            perf_hotpath_benchmark,
            interactive_scenario_benchmark,
            renderer_benchmark,
            mp_draw_benchmark,
            system_benchmark,
        )
        for definition in provider.case_definitions()
    )
    case_ids = [definition.case_id for definition in definitions]
    duplicate_ids = sorted(case_id for case_id in set(case_ids) if case_ids.count(case_id) > 1)
    if duplicate_ids:
        raise ValueError("duplicate benchmark case: " + ", ".join(duplicate_ids))
    return tuple(sorted(definitions, key=lambda definition: definition.case_id))


def definition_for_case(case_id: str) -> CaseDefinition:
    """一意な case ID を解決する。"""

    by_id = {definition.case_id: definition for definition in case_definitions()}
    try:
        return by_id[case_id]
    except KeyError as exc:
        raise ValueError(f"unknown benchmark case: {case_id}") from exc


def select_case_definitions(
    *,
    suites: tuple[str, ...],
    case_ids: tuple[str, ...] = (),
) -> tuple[CaseDefinition, ...]:
    """Suite または明示 ID で case を選ぶ。未知 ID/suite は拒否する。"""

    definitions = case_definitions()
    by_id = {definition.case_id: definition for definition in definitions}
    if case_ids:
        duplicate_ids = sorted(case_id for case_id in set(case_ids) if case_ids.count(case_id) > 1)
        if duplicate_ids:
            raise ValueError("duplicate benchmark case: " + ", ".join(duplicate_ids))
        unknown_ids = sorted(set(case_ids) - set(by_id))
        if unknown_ids:
            raise ValueError(f"unknown benchmark case: {', '.join(unknown_ids)}")
        return tuple(by_id[case_id] for case_id in case_ids)

    available_suites = {
        suite for definition in definitions for suite in definition.selectable_suites
    } | {"all"}
    unknown_suites = sorted(set(suites) - available_suites)
    if unknown_suites:
        raise ValueError(f"unknown benchmark suite: {', '.join(unknown_suites)}")
    if "all" in suites:
        return definitions
    selected = [
        definition for definition in definitions if set(suites) & set(definition.selectable_suites)
    ]
    return tuple(selected)


__all__ = ["case_definitions", "definition_for_case", "select_case_definitions"]
