from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from grafix.core.parameters import key as key_module
from grafix.core.parameters.key import caller_site_id, make_site_id


def _compiled_site_resolvers(
    filename: Path,
    *,
    source_owner: str | None = None,
) -> tuple[Callable[[str | None], str], Callable[[str | None], str]]:
    # 同じ test process 内の別 filename が CodeType cache key として衝突しないよう、
    # fixture 固有値も code constants に含める。
    code = compile(
        (
            f"def resolve(key):\n    {str(filename)!r}\n"
            "    return make_site_id(key=key)\n"
        ),
        str(filename),
        "exec",
    )
    resolvers: list[Callable[[str | None], str]] = []
    for module_name in ("__main__", "__mp_main__"):
        namespace: dict[str, object] = {
            "__name__": module_name,
            "make_site_id": make_site_id,
        }
        if source_owner is not None:
            namespace["__grafix_source_owner__"] = source_owner
        exec(code, namespace)
        resolvers.append(
            cast(Callable[[str | None], str], namespace["resolve"])
        )
    return (resolvers[0], resolvers[1])


def test_site_id_stable_same_expression():
    ids = [caller_site_id(skip=1) for _ in range(2)]
    assert ids[0] == ids[1]


def helper_other():
    return caller_site_id(skip=1)


def test_site_id_differs_on_other_function():
    a = caller_site_id(skip=1)
    c = helper_other()
    assert a != c


def test_site_id_does_not_persist_absolute_project_path() -> None:
    def get_site_id() -> str:
        return caller_site_id(skip=1)

    site_id = get_site_id()

    assert str(Path.cwd().resolve()) not in site_id
    assert "tests/core/parameters/test_site_id.py" in site_id


def test_explicit_key_discards_instruction_location() -> None:
    first = caller_site_id(skip=1, key="stable")
    second = caller_site_id(skip=1, key="stable")

    assert first == second
    assert first.endswith("|str:6:stable")


@pytest.mark.parametrize("key", [None, "stable"], ids=("automatic", "explicit"))
@pytest.mark.parametrize("location", ["inside", "outside"])
def test_direct_main_aliases_share_site_id_without_changing_parent_identity(
    tmp_path: Path,
    key: str | None,
    location: str,
) -> None:
    if location == "inside":
        filename = Path.cwd() / "tests/core/parameters/direct_main_site_fixture.py"
        expected_file_id = "tests/core/parameters/direct_main_site_fixture.py"
    else:
        filename = tmp_path / "direct_main_site_fixture.py"
        expected_file_id = filename.name
    parent, worker = _compiled_site_resolvers(filename)

    parent_site_id = parent(key)
    worker_site_id = worker(key)

    assert worker_site_id == parent_site_id
    assert parent_site_id.startswith(expected_file_id)
    if key is None:
        assert parent_site_id.startswith(f"{expected_file_id}:")
    else:
        assert parent_site_id == f"{expected_file_id}|str:6:stable"


def test_main_alias_is_canonicalized_before_automatic_site_cache(
    tmp_path: Path,
) -> None:
    key_module._automatic_site_id.cache_clear()
    parent, worker = _compiled_site_resolvers(tmp_path / "cached_site.py")

    assert parent(None) == worker(None)

    info = key_module._automatic_site_id.cache_info()
    assert info.misses == 1
    assert info.hits == 1


def test_explicit_source_owner_is_not_canonicalized_for_site_id(
    tmp_path: Path,
) -> None:
    _parent, worker = _compiled_site_resolvers(
        tmp_path / "explicit_owner_site.py",
        source_owner="__mp_main__",
    )

    site_id = worker("stable")

    assert site_id == "__mp_main__|str:6:stable"


def test_explicit_key_rejects_unsupported_types() -> None:
    with pytest.raises(TypeError, match=r"str\|int\|None"):
        caller_site_id(skip=1, key=object())  # type: ignore[arg-type]


def test_instance_key_is_appended_to_semantic_key() -> None:
    site_id = caller_site_id(skip=1, key="petal", instance_key=7)

    assert site_id.endswith("|str:5:petal|instance:int:7")


def test_string_and_integer_semantic_keys_do_not_collide() -> None:
    integer = caller_site_id(skip=1, key=1)
    string = caller_site_id(skip=1, key="1")

    assert integer != string
    assert integer.endswith("|int:1")
    assert string.endswith("|str:1:1")


@pytest.mark.parametrize("value", [True, ""])
def test_semantic_keys_reject_bool_and_empty_string(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        caller_site_id(skip=1, key=value)  # type: ignore[arg-type]


def test_shared_semantic_site_rejects_instance_key() -> None:
    with pytest.raises(ValueError, match="instance_key"):
        caller_site_id(skip=1, key="petals", instance_key=0, shared=True)


def test_automatic_site_id_uses_location_cache() -> None:
    key_module._automatic_site_id.cache_clear()

    ids = [caller_site_id(skip=1) for _ in range(3)]
    info = key_module._automatic_site_id.cache_info()

    assert len(set(ids)) == 1
    assert info.misses == 1
    assert info.hits == 2


@pytest.mark.parametrize("factory", [make_site_id, caller_site_id])
def test_site_id_fails_when_python_frame_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[], str],
) -> None:
    monkeypatch.setattr(key_module.inspect, "currentframe", lambda: None)

    with pytest.raises(RuntimeError, match="frame could not be resolved"):
        factory()
