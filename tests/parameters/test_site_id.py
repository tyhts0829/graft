from src.parameters.key import caller_site_id


def test_site_id_stable_same_expression():
    ids = [caller_site_id(skip=1) for _ in range(2)]
    assert ids[0] == ids[1]


def helper_other():
    return caller_site_id(skip=1)


def test_site_id_differs_on_other_function():
    a = caller_site_id(skip=1)
    c = helper_other()
    assert a != c
