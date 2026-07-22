from __future__ import annotations

import pytest

from grafix.core.lifecycle import CleanupErrors


def test_cleanup_errors_preserves_initial_error_and_reports_secondary_steps() -> None:
    calls: list[str] = []
    reported: list[str] = []
    initial_error = RuntimeError("session failed")
    errors = CleanupErrors(
        initial_error=initial_error,
        report_secondary=reported.append,
    )

    def fail() -> None:
        calls.append("fail")
        raise KeyboardInterrupt

    errors.attempt(fail, "secondary")
    errors.attempt(lambda: calls.append("finish"), "finish")

    with pytest.raises(RuntimeError) as exc_info:
        errors.raise_if_any()

    assert exc_info.value is initial_error
    assert calls == ["fail", "finish"]
    assert reported == ["secondary"]
    assert initial_error.__notes__ == [
        "Secondary cleanup failure (secondary): KeyboardInterrupt: "
    ]


def test_cleanup_errors_raises_first_cleanup_error_and_notes_later_failures() -> None:
    first_error = RuntimeError("first")
    second_error = OSError("second")
    errors = CleanupErrors()

    def fail_first() -> None:
        raise first_error

    def fail_second() -> None:
        raise second_error

    errors.attempt(fail_first, "first step")
    errors.attempt(fail_second, "second step")

    with pytest.raises(RuntimeError) as exc_info:
        errors.raise_if_any()

    assert exc_info.value is first_error
    assert first_error.__notes__ == [
        "Secondary cleanup failure (second step): OSError: second"
    ]
