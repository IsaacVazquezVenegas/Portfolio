import pandas as pd
import pytest

from src.models import Processing
from src.transformation import (
    build_wide_panel,
    load_country_reference,
    standardise_period,
    transform,
)


def obs(**kw):
    base = {
        "source_id": "s", "provider": "P", "task_id": "t", "indicator_code": "IND", "indicator_name": "Indicator",
        "country_code": "FRA", "country_name": "France", "period": "2020", "value": None, "value_text": None,
        "dimensions": {}, "request_id": "req_00001",
    }
    base.update(kw)
    return base


@pytest.mark.parametrize("raw,expected", [
    ("2020", ("2020", 2020)),
    ("2020Q3", ("2020-Q3", 2020)),
    ("2020-q1", ("2020-Q1", 2020)),
    ("2020M04", ("2020-04", 2020)),
    ("2020-04", ("2020-04", 2020)),
    ("2020-04-15T00:00:00", ("2020-04-15", 2020)),
    ("2019-2020", ("2019-2020", 2019)),
    (None, (None, None)),
])
def test_standardise_period(raw, expected):
    assert standardise_period(raw) == expected


def test_country_harmonisation():
    ref = load_country_reference()
    assert ref.harmonise("FR") == ("FRA", True)
    assert ref.harmonise("EL") == ("GRC", True)
    assert ref.harmonise("UK") == ("GBR", True)
    assert ref.harmonise("DEU") == ("DEU", True)
    assert ref.harmonise("ZZZ") == ("ZZZ", False)
    assert ref.is_aggregate("EUU") and not ref.is_aggregate("FRA")


def test_iso2_codes_harmonised_and_original_kept():
    res = transform([obs(country_code="EL", country_name=None, value=1.0, value_text="1")], Processing())
    row = res.dataset.iloc[0]
    assert row["country_code"] == "GRC" and row["country_code_source"] == "EL"
    assert row["country_name"] == "Greece"  # filled from reference


def test_country_codes_source_mode_keeps_codes():
    res = transform([obs(country_code="EL", value=1.0)], Processing(country_codes="source"))
    assert res.dataset.iloc[0]["country_code"] == "EL"


def test_unknown_country_code_warns():
    res = transform([obs(country_code="XYZ1", value=1.0)], Processing())
    assert any("XYZ1" in w for w in res.warnings)


def test_categorical_values_are_not_converted():
    data = [obs(value_text="4"), obs(country_code="DEU", value_text="Not applicable"),
            obs(country_code="ITA", value=30.9, value_text="30.9 [24.9-36.9]")]
    df = transform(data, Processing()).dataset
    assert df.set_index("country_code")["value"].isna().to_dict() == {"DEU": True, "FRA": True, "ITA": False}
    assert not df["missing"].any()


def test_parse_plain_numbers_only_when_requested():
    data = [obs(value_text="4"), obs(country_code="DEU", value_text="Not applicable"),
            obs(country_code="ITA", value_text="30.9 [24.9-36.9]")]
    res = transform(data, Processing(numeric_values="parse_plain_numbers"))
    df = res.dataset.set_index("country_code")
    assert df.loc["FRA", "value"] == 4.0
    assert pd.isna(df.loc["DEU", "value"]) and pd.isna(df.loc["ITA", "value"])
    step = next(s for s in res.steps if s["step"] == "parse_plain_numbers")
    assert step["values_converted"] == 1


def test_missing_values_kept_and_flagged():
    data = [obs(value=None, value_text=None), obs(country_code="DEU", value_text=".."), obs(country_code="ITA", value=1.0)]
    res = transform(data, Processing(missing_values={"treatment": "keep", "markers": [".."]}))
    assert res.dataset.set_index("country_code")["missing"].to_dict() == {"DEU": True, "FRA": True, "ITA": False}
    assert len(res.dataset) == 3


def test_missing_values_dropped_and_counted():
    data = [obs(value=None, value_text=None), obs(country_code="ITA", value=1.0)]
    res = transform(data, Processing(missing_values={"treatment": "drop"}))
    assert list(res.dataset["country_code"]) == ["ITA"]
    step = next(s for s in res.steps if s["step"] == "drop_missing")
    assert step["removed"] == 1


def test_exact_duplicates_removed_conflicts_flagged():
    data = [obs(value=1.0), obs(value=1.0, request_id="req_00002"),  # exact duplicate (other request)
            obs(country_code="DEU", value=1.0), obs(country_code="DEU", value=2.0)]  # conflict
    res = transform(data, Processing())
    df = res.dataset
    assert len(df) == 3
    assert df.set_index(["country_code", "value"])["duplicate_conflict"].to_dict() == {
        ("DEU", 1.0): True, ("DEU", 2.0): True, ("FRA", 1.0): False}
    assert any("different values" in w for w in res.warnings)


def test_no_deduplication_when_disabled():
    res = transform([obs(value=1.0), obs(value=1.0)], Processing(deduplicate=False))
    assert len(res.dataset) == 2


def test_dimensions_distinguish_observations():
    data = [obs(value=1.0, dimensions={"SEX": "SEX_MLE"}), obs(value=2.0, dimensions={"SEX": "SEX_FMLE"})]
    df = transform(data, Processing()).dataset
    assert sorted(df["dimensions"]) == ["SEX=SEX_FMLE", "SEX=SEX_MLE"]
    assert not df["duplicate_conflict"].any()


def test_wide_panel_keeps_text_for_categorical_indicators():
    data = [
        obs(indicator_code="R_Group", value_text="4"),
        obs(indicator_code="R_Group", period="2022", value_text="Not applicable"),
        obs(indicator_code="PREV", value=30.5, value_text="30.5 [20-40]", dimensions={"SEX": "SEX_MLE"}),
    ]
    res = transform(data, Processing(wide_panel=True))
    wide = res.wide_panel
    assert list(wide.columns) == ["country_code", "country_name", "period", "year", "s:PREV [SEX=SEX_MLE]", "s:R_Group"]
    row2020 = wide[wide["period"] == "2020"].iloc[0]
    assert row2020["s:R_Group"] == "4" and row2020["s:PREV [SEX=SEX_MLE]"] == 30.5
    assert wide[wide["period"] == "2022"].iloc[0]["s:R_Group"] == "Not applicable"


def test_empty_input():
    res = transform([], Processing(wide_panel=True))
    assert res.dataset.empty and res.wide_panel.empty


def test_steps_are_documented():
    res = transform([obs(value=1.0)], Processing())
    names = [s["step"] for s in res.steps]
    assert names[:3] == ["parse", "standardise_periods", "harmonise_country_codes"]
    assert all({"rows_before", "rows_after", "description"} <= set(s) for s in res.steps)
