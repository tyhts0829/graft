import pytest

from grafix.core.parameters import ParamMeta, normalize_input


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", True),
        ("false", False),
        (0, False),
        (1, True),
    ],
)
def test_normalize_bool(value, expected):
    out, err = normalize_input(value, ParamMeta(kind="bool"))
    assert out == expected
    assert err is None


def test_normalize_int_and_error():
    out, err = normalize_input("10", ParamMeta(kind="int"))
    assert out == 10 and err is None
    out2, err2 = normalize_input("x", ParamMeta(kind="int"))
    assert out2 is None and err2 == "invalid_int"


def test_normalize_float_and_error():
    out, err = normalize_input("0.25", ParamMeta(kind="float"))
    assert out == 0.25 and err is None
    out2, err2 = normalize_input("bad", ParamMeta(kind="float"))
    assert out2 is None and err2 == "invalid_float"


def test_normalize_str():
    out, err = normalize_input(123, ParamMeta(kind="str"))
    assert out == "123"
    assert err is None


def test_normalize_choice_coerces_to_first():
    meta = ParamMeta(kind="choice", choices=["red", "green"])
    out, err = normalize_input("blue", meta)
    assert out == "red"
    assert err == "choice_coerced"


def test_normalize_vec3():
    out, err = normalize_input([1, "2.5", 3], ParamMeta(kind="vec3"))
    assert out == (1.0, 2.5, 3.0)
    assert err is None
    out2, err2 = normalize_input([1, 2], ParamMeta(kind="vec3"))
    assert out2 is None and err2 == "invalid_length"


def test_normalize_rgb():
    out, err = normalize_input((300, -5, 10.9), ParamMeta(kind="rgb"))
    assert out == (255, 0, 10)
    assert err is None
    out2, err2 = normalize_input((1, 2), ParamMeta(kind="rgb"))
    assert out2 is None and err2 == "invalid_length"
