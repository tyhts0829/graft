from grafix.interactive.parameter_gui.widgets import _filter_choices_by_query_and


def _choice(stem: str, rel: str):
    return (stem, rel, rel.lower().endswith(".ttc"), f"{rel} {stem}".lower())


def test_filter_choices_by_query_and_matches_all_tokens_case_insensitive():
    choices = (
        _choice("NotoSansJP-Regular", "NotoSansJP-Regular.otf"),
        _choice("SFNS", "SFNS.ttf"),
        _choice("NotoSerifJP-Regular", "NotoSerifJP-Regular.otf"),
    )

    out = _filter_choices_by_query_and(choices, query="noto sans")
    assert [item[1] for item in out] == ["NotoSansJP-Regular.otf"]

    out2 = _filter_choices_by_query_and(choices, query="SANS NOTO")
    assert [item[1] for item in out2] == ["NotoSansJP-Regular.otf"]

    out3 = _filter_choices_by_query_and(choices, query="noto jp")
    assert [item[1] for item in out3] == [
        "NotoSansJP-Regular.otf",
        "NotoSerifJP-Regular.otf",
    ]


def test_filter_choices_by_query_and_empty_query_returns_all():
    choices = (
        _choice("A", "A.ttf"),
        _choice("B", "B.otf"),
    )
    out = _filter_choices_by_query_and(choices, query="")
    assert [item[1] for item in out] == ["A.ttf", "B.otf"]

