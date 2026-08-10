from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from grafix.__main__ import main as grafix_main
from grafix.devtools.benchmarks import cli
from grafix.devtools.benchmarks.catalog import case_definitions
from grafix.devtools.benchmarks.schema import (
    CaseResult,
    Sample,
    evaluate_contract,
    summarize_samples,
)


def test_list_outputs_registry_json(capsys) -> None:
    assert cli.main(["list", "--suite", "smoke", "--json"]) == 0
    entries = json.loads(capsys.readouterr().out)

    assert entries
    assert all("smoke" in entry["selectable_suites"] for entry in entries)
    assert all(entry["id"] for entry in entries)


def test_top_level_cli_dispatches_benchmark_actions(capsys) -> None:
    assert grafix_main(["benchmark", "list", "--suite", "smoke"]) == 0
    assert "core.concat_recipe.parts_10" in capsys.readouterr().out


def test_run_and_report_cli_create_schema_v4_artifacts(
    tmp_path: Path,
    capsys,
) -> None:
    assert (
        cli.main(
            [
                "run",
                "--case",
                "core.concat_recipe.parts_10",
                "--profile",
                "smoke",
                "--samples",
                "1",
                "--warmup",
                "0",
                "--target-ms",
                "0",
                "--run-id",
                "test-run",
                "--out",
                str(tmp_path),
            ]
        )
        == 0
    )
    run_path = tmp_path / "runs" / "test-run.json"
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 4
    assert payload["cases"][0]["samples"]
    assert payload["cases"][0]["checksum"]
    assert payload["cases"][0]["metrics"]
    assert payload["cases"][0]["contracts"] == []

    capsys.readouterr()
    assert cli.main(["report", "--out", str(tmp_path)]) == 0
    report_output = capsys.readouterr().out
    assert "report.html" in report_output
    assert "overview.svg" in report_output
    assert "warnings.json" in report_output
    assert (tmp_path / "report.html").is_file()
    assert (tmp_path / "overview.svg").is_file()
    assert (tmp_path / "warnings.json").is_file()

    assert (
        cli.main(
            [
                "run",
                "--case",
                "core.concat_recipe.parts_10",
                "--run-id",
                "test-run",
                "--out",
                str(tmp_path),
            ]
        )
        == 2
    )

    payload["meta"]["created_at"] = "not-a-date"
    run_path.write_text(json.dumps(payload), encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["report", "--out", str(tmp_path)]) == 2
    assert "invalid created_at" in capsys.readouterr().err
    assert (
        cli.main(
            [
                "run",
                "--case",
                "core.concat_recipe.parts_10",
                "--mode",
                "process-cold",
                "--warmup",
                "1",
                "--out",
                str(tmp_path),
            ]
        )
        == 2
    )


def test_cli_writes_self_sampling_scenario_with_effective_case_policy(
    tmp_path: Path,
) -> None:
    assert (
        cli.main(
            [
                "run",
                "--case",
                "interactive.slider.input_to_present.rows_32.workers_0",
                "--samples",
                "3",
                "--warmup",
                "2",
                "--target-ms",
                "1000",
                "--run-id",
                "self-sampling",
                "--out",
                str(tmp_path),
            ]
        )
        == 0
    )
    payload = json.loads((tmp_path / "runs" / "self-sampling.json").read_text(encoding="utf-8"))
    assert payload["meta"]["samples"] == 3
    assert payload["meta"]["warmup"] == 2
    case = payload["cases"][0]
    assert case["spec"]["self_sampling"] is True
    assert len(case["samples"]) == 1
    latency = next(
        metric for metric in case["metrics"] if metric["name"] == "ux01.input_to_present"
    )
    assert latency["distribution"]["count"] == 12


def test_cli_rejects_ignored_or_duplicate_selection_arguments(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit):
        cli.main(
            [
                "run",
                "--suite",
                "smoke",
                "--case",
                "core.concat_recipe.parts_10",
            ]
        )

    assert (
        cli.main(
            [
                "run",
                "--case",
                "core.concat_recipe.parts_10",
                "--case",
                "core.concat_recipe.parts_10",
                "--out",
                str(tmp_path),
            ]
        )
        == 2
    )


def test_report_cli_returns_two_when_only_duplicate_run_ids_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    definition = next(
        definition
        for definition in case_definitions()
        if definition.case_id == "core.concat_recipe.parts_10"
    )
    sample = Sample(elapsed_ns=100, iterations=1)
    result = CaseResult(
        spec=definition.spec(seed=0),
        status="ok",
        samples=(sample,),
        stats=summarize_samples((sample,)),
        checksum="checksum",
        checksum_kind="exact",
        setup_rss_bytes=10,
        baseline_rss_bytes=10,
        peak_rss_bytes=10,
        peak_rss_delta_bytes=0,
    )
    monkeypatch.setattr(cli, "run_case_isolated", lambda *_args, **_kwargs: result)
    assert (
        cli.main(
            [
                "run",
                "--case",
                definition.case_id,
                "--samples",
                "1",
                "--warmup",
                "0",
                "--target-ms",
                "0",
                "--run-id",
                "duplicate",
                "--out",
                str(tmp_path),
            ]
        )
        == 0
    )
    original = tmp_path / "runs" / "duplicate.json"
    (tmp_path / "runs" / "duplicate-copy.json").write_text(
        original.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    capsys.readouterr()

    assert cli.main(["report", "--out", str(tmp_path)]) == 2
    captured = capsys.readouterr()
    assert "duplicate benchmark run_id 'duplicate'" in captured.err
    assert "no valid schema v4 runs found" in captured.err
    assert (tmp_path / "report.html").is_file()
    assert (tmp_path / "overview.svg").is_file()
    assert (tmp_path / "warnings.json").is_file()
    warning_payload = json.loads((tmp_path / "warnings.json").read_text(encoding="utf-8"))
    assert warning_payload["valid_runs"] == 0
    assert warning_payload["latest_warning_count"] == 0
    assert warning_payload["historical_warning_count"] == 1


def test_report_cli_returns_two_for_chart_generation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_report(_out: str | Path) -> None:
        raise cli.BenchmarkReportGenerationError("synthetic chart failure")

    monkeypatch.setattr(cli, "write_report", fail_report)

    assert cli.main(["report", "--out", str(tmp_path)]) == 2
    assert "synthetic chart failure" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    (
        ["run", "--case", ""],
        ["run", "--case", ","],
        ["run", "--suite", ""],
        ["run", "--target-ms", "nan"],
        ["run", "--target-ms", "inf"],
        ["run", "--target-ms=-1e-10"],
        ["run", "--timeout", "nan"],
        ["run", "--timeout", "inf"],
    ),
)
def test_run_rejects_empty_selectors_and_non_finite_values(
    arguments: list[str],
) -> None:
    assert cli.main(arguments) == 2


def test_effective_child_environment_forces_deterministic_hash_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "random")

    environment = cli._effective_child_environment(mode="warm")
    assert environment["PYTHONHASHSEED"] == "0"
    assert environment["PYTHONPYCACHEPREFIX"] == "<isolated-empty>"


def test_run_cli_returns_nonzero_for_hard_contract_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = next(
        definition
        for definition in case_definitions()
        if definition.case_id == "core.concat_recipe.parts_10"
    )
    sample = Sample(elapsed_ns=100, iterations=1)
    failed = evaluate_contract(
        contract_id="synthetic.hard",
        severity="hard",
        actual=False,
        comparator="eq",
        limit=True,
        reason="synthetic hard contract",
    )
    result = CaseResult(
        spec=definition.spec(seed=0),
        status="contract-failure",
        samples=(sample,),
        stats=summarize_samples((sample,)),
        checksum="checksum",
        checksum_kind="exact",
        setup_rss_bytes=10,
        baseline_rss_bytes=10,
        peak_rss_bytes=10,
        peak_rss_delta_bytes=0,
        contracts=(failed,),
        error="failed hard contracts: synthetic.hard",
    )
    monkeypatch.setattr(cli, "run_case_isolated", lambda *_args, **_kwargs: result)

    assert (
        cli.main(
            [
                "run",
                "--case",
                definition.case_id,
                "--samples",
                "1",
                "--warmup",
                "0",
                "--target-ms",
                "0",
                "--run-id",
                "contract-failure",
                "--out",
                str(tmp_path),
            ]
        )
        == 1
    )
    payload = json.loads((tmp_path / "runs" / "contract-failure.json").read_text(encoding="utf-8"))
    assert payload["cases"][0]["status"] == "contract-failure"
    assert payload["cases"][0]["contracts"][0]["severity"] == "hard"
    assert cli.main(["report", "--out", str(tmp_path)]) == 1
    assert (tmp_path / "report.html").is_file()
    assert (tmp_path / "overview.svg").is_file()

    succeeded = replace(
        result,
        status="ok",
        contracts=(),
        error=None,
    )
    monkeypatch.setattr(cli, "run_case_isolated", lambda *_args, **_kwargs: succeeded)
    assert (
        cli.main(
            [
                "run",
                "--case",
                definition.case_id,
                "--samples",
                "1",
                "--warmup",
                "0",
                "--target-ms",
                "0",
                "--run-id",
                "zz-latest-success",
                "--out",
                str(tmp_path),
            ]
        )
        == 0
    )

    # Historical failure は report に残るが、終了 code は最新 run だけで決める。
    assert cli.main(["report", "--out", str(tmp_path)]) == 0
