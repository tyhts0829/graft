"""複数 resource の cleanup を最後まで実行するための内部 helper。"""

from __future__ import annotations

from collections.abc import Callable


class CleanupErrors:
    """cleanup 例外を最初の一件に集約し、後続例外を note に残す。"""

    def __init__(
        self,
        *,
        initial_error: BaseException | None = None,
        report_secondary: Callable[[str], None] | None = None,
    ) -> None:
        self._first_error = initial_error
        self._report_secondary = report_secondary

    def record(self, error: BaseException, label: str = "cleanup") -> None:
        if self._first_error is None:
            self._first_error = error
            return

        self._first_error.add_note(
            f"Secondary cleanup failure ({label}): "
            f"{type(error).__name__}: {error}"
        )
        for note in getattr(error, "__notes__", ()):
            self._first_error.add_note(note)
        if self._report_secondary is not None:
            self._report_secondary(label)

    def attempt(
        self,
        action: Callable[[], object],
        label: str = "cleanup",
    ) -> None:
        try:
            action()
        except BaseException as error:
            self.record(error, label)

    def raise_if_any(self) -> None:
        if self._first_error is not None:
            raise self._first_error
