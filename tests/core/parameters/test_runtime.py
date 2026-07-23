from dataclasses import FrozenInstanceError

import pytest

from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.runtime import (
    ParameterLoadState,
    ParamStoreLoadDiagnostic,
    ParamStoreRuntime,
)


def test_parameter_load_state_is_frozen_and_kept_outside_store_runtime() -> None:
    diagnostic = ParamStoreLoadDiagnostic(code="partial", summary="partial")
    state = ParameterLoadState(
        provenance="session_recovery",
        diagnostics=(diagnostic,),
    )

    assert state.provenance == "session_recovery"
    assert state.diagnostics == (diagnostic,)
    with pytest.raises(FrozenInstanceError):
        state.provenance = "primary"  # type: ignore[misc]
    assert not hasattr(ParamStoreRuntime(), "load_provenance")
    assert not hasattr(ParamStoreRuntime(), "load_diagnostics")


def test_runtime_constructor_is_keyword_only_and_canonicalizes_group_sets() -> None:
    loaded = {("line", "loaded")}
    observed = {("line", "observed")}
    reconciled = {(("line", "old"), ("line", "new"))}
    display_order = {("line", "site"): 7}
    key = ParameterKey(op="line", site_id="site", arg="length")
    effective = {key: 12.5}
    warned = {("line", "unknown")}

    runtime = ParamStoreRuntime(
        loaded_groups=loaded,
        observed_groups=observed,
        reconcile_applied=reconciled,
        display_order_by_group=display_order,
        next_display_order=8,
        last_effective_by_key=effective,
        warned_unknown_args=warned,
    )

    assert runtime.loaded_groups == loaded
    assert runtime.loaded_groups is not loaded
    assert runtime.observed_groups == observed
    assert runtime.observed_groups is not observed
    assert runtime.reconcile_applied is reconciled
    assert runtime.display_order_by_group is display_order
    assert runtime.next_display_order == 8
    assert runtime.last_effective_by_key is effective
    assert runtime.warned_unknown_args is warned
    assert runtime.last_source_by_key == {}
    assert runtime.effective_revision == 0

    with pytest.raises(TypeError):
        ParamStoreRuntime(loaded, observed)  # type: ignore[misc]


def test_default_group_sets_advance_visibility_revision_only_on_change() -> None:
    runtime = ParamStoreRuntime()
    token = runtime.visibility_cache_token()

    runtime.observed_groups.add(("line", "site"))
    assert runtime.visibility_revision == 1
    assert runtime.visibility_cache_token() != token

    token = runtime.visibility_cache_token()
    runtime.observed_groups.add(("line", "site"))
    assert runtime.visibility_revision == 1
    assert runtime.visibility_cache_token() == token

    runtime.loaded_groups = {("line", "loaded")}
    assert runtime.visibility_revision == 2
    token = runtime.visibility_cache_token()
    runtime.loaded_groups.discard(("line", "loaded"))
    assert runtime.visibility_revision == 3
    assert runtime.visibility_cache_token() != token


def test_constructor_group_sets_are_tracked_after_canonicalization() -> None:
    loaded = {("line", "a")}
    observed = {("line", "a")}
    runtime = ParamStoreRuntime(loaded_groups=loaded, observed_groups=observed)
    token = runtime.visibility_cache_token()

    runtime.loaded_groups.clear()
    runtime.loaded_groups.add(("line", "b"))

    assert runtime.visibility_revision == 2
    assert runtime.visibility_cache_token() != token


def test_effective_change_log_returns_sparse_keys_and_detects_gaps() -> None:
    runtime = ParamStoreRuntime()
    first = ParameterKey(op="line", site_id="a", arg="length")
    second = ParameterKey(op="line", site_id="b", arg="length")

    runtime.record_effective_changes((first,))
    revision = runtime.effective_revision
    runtime.record_effective_changes((first, second, first))

    assert runtime.effective_revision == revision + 1
    assert runtime.effective_changes_since(revision) == frozenset(
        {first, second}
    )
    assert runtime.effective_changes_since(runtime.effective_revision) == frozenset()
    assert runtime.effective_changes_since(-1) is None


def test_empty_effective_change_set_does_not_advance_revision() -> None:
    runtime = ParamStoreRuntime()

    runtime.record_effective_changes(())

    assert runtime.effective_revision == 0
    assert runtime.effective_changes_since(0) == frozenset()
