from grafix.devtools.generate_stub import generate_stubs_str


def _protocol_method_signature(stub: str, *, protocol: str, method: str) -> str:
    """生成済み Protocol から対象 method の署名行を返す。"""

    protocol_body = stub.split(f"class {protocol}(Protocol):\n", 1)[1]
    protocol_body = protocol_body.split("\nclass ", 1)[0]
    return next(
        line.strip()
        for line in protocol_body.splitlines()
        if line.strip().startswith(f"def {method}(")
    )


def test_generated_stub_exposes_parameter_identity_controls() -> None:
    stub = generate_stubs_str()

    line_signature = next(
        line for line in stub.splitlines() if line.lstrip().startswith("def line(")
    )
    assert "key: str | int | None" in line_signature
    assert "instance_key: str | int | None" in line_signature
    assert "shared: bool" in line_signature

    assert "        instance_key: str | int | None = ...,\n" in stub
    assert "        shared: bool = ...,\n" in stub


def test_generated_stub_exposes_primitive_and_effect_selectors() -> None:
    stub = generate_stubs_str()

    primitive_select = _protocol_method_signature(
        stub,
        protocol="_G",
        method="select",
    )
    assert primitive_select.startswith(
        "def select(self, *, target: str = ..., "
        "params_by_target: Mapping[str, Mapping[str, Any]] | None = ..."
    )
    assert "key: str | int | None = ..." in primitive_select
    assert "instance_key: str | int | None = ..." in primitive_select
    assert "shared: bool = ..." in primitive_select
    assert primitive_select.endswith("-> Geometry:")

    effect_select = _protocol_method_signature(
        stub,
        protocol="_E",
        method="select",
    )
    assert effect_select.startswith(
        "def select(self, *, target: str = ..., n_inputs: int = ..., "
        "params_by_target: Mapping[str, Mapping[str, Any]] | None = ..."
    )
    assert "key: str | int | None = ..." in effect_select
    assert "instance_key: str | int | None = ..." in effect_select
    assert "shared: bool = ..." in effect_select
    assert effect_select.endswith("-> _EffectBuilder:")

    builder_select = _protocol_method_signature(
        stub,
        protocol="_EffectBuilder",
        method="select",
    )
    assert builder_select.startswith(
        "def select(self, *, target: str = ..., n_inputs: Literal[1] = ..., "
        "params_by_target: Mapping[str, Mapping[str, Any]] | None = ..."
    )
    assert builder_select.endswith("-> _EffectBuilder:")


def test_generated_stub_exposes_operation_catalogs_with_exact_types() -> None:
    stub = generate_stubs_str()

    assert _protocol_method_signature(
        stub,
        protocol="_G",
        method="catalog",
    ) == "def catalog(self) -> tuple[OperationInfo, ...]:"
    assert _protocol_method_signature(
        stub,
        protocol="_G",
        method="describe",
    ) == "def describe(self, name: str) -> OperationInfo:"
    assert _protocol_method_signature(
        stub,
        protocol="_E",
        method="catalog",
    ) == "def catalog(self) -> tuple[OperationInfo, ...]:"
    assert _protocol_method_signature(
        stub,
        protocol="_E",
        method="describe",
    ) == "def describe(self, name: str) -> OperationInfo:"
