"""Missing-value codes (Stage 4), readable labels and the page's data views."""

import csv
import json

import pandas as pd
import pytest

from conftest import ROOT, write_config
from src.models import Processing
from src.page_export import export_run
from src.transformation import labelled_wide_panel, series_label, transform
from src.validation import ConfigValidationError, validate_config_file
from src.value_codes import inspect_value_codes, plan_entries, suggest_meaning
from test_page_export import fake_run, load
from test_transformation import obs


@pytest.mark.parametrize("code,meaning", [
    (". . .", "no_data"), ("..", "no_data"), ("—", "no_data"), ("n/a", "no_data"), ("NA", "no_data"),
    ("Not applicable", "not_applicable"), ("Yes", "category"), ("Partially", "category"), ("EUR", "category"),
])
def test_suggested_meaning(code, meaning):
    assert suggest_meaning(code) == meaning


def test_inspect_value_codes_lists_markers_and_summarises_categories(tmp_path):
    path = tmp_path / "dataset.csv"
    pd.DataFrame({
        "source_id": ["s"] * 6, "indicator_code": ["A", "A", "B", "B", "B", "C"],
        "value": ["", "", "", "", "3", ""], "value_text": [". . .", "Yes", "—", "No", "3", ". . ."],
    }).to_csv(path, index=False)
    found = inspect_value_codes(path)
    codes = {f["code"]: f for f in found if f["code"]}
    assert codes[". . ."]["rows"] == 2 and codes[". . ."]["indicators"] == 2
    assert codes["—"]["note"] and "follow-up" in codes["—"]["note"]
    other = [f for f in found if f["code"] is None][0]
    assert other["distinct_codes"] == 2 and other["rows"] == 2
    entries = plan_entries(found, "RUN")
    assert {e["code"] for e in entries} == {". . .", "—"} and all("RUN" in e["evidence"] for e in entries)


def _plan_with_codes(meaning):
    plan = json.loads((ROOT / "projects" / "WHO_MPOWER" / "extraction_plan.json").read_text(encoding="utf-8"))
    plan["approval"] = {"status": "approved", "approved_by": "R", "approved_on": "2026-11-22"}
    plan["outstanding_decisions"] = []
    plan["missing_value_codes"] = [{"source": "who_gho", "code": "..", "meaning": meaning}]
    return plan


def test_no_data_codes_must_be_configuration_markers(tmp_path, config_dict):
    (tmp_path / "plan.json").write_text(json.dumps(_plan_with_codes("no_data")), encoding="utf-8")
    config_dict["project"]["extraction_plan"] = "plan.json"
    config_dict["processing"]["missing_values"] = {"markers": []}
    with pytest.raises(ConfigValidationError) as exc:
        validate_config_file(write_config(tmp_path, config_dict))
    assert "classifies ['..'] as 'no_data'" in exc.value.render()
    config_dict["processing"]["missing_values"] = {"markers": [".."]}
    assert validate_config_file(write_config(tmp_path, config_dict)).plan is not None


def test_unrecorded_marker_is_a_warning(tmp_path, config_dict):
    (tmp_path / "plan.json").write_text(json.dumps(_plan_with_codes("not_applicable")), encoding="utf-8")
    config_dict["project"]["extraction_plan"] = "plan.json"
    config_dict["processing"]["missing_values"] = {"markers": ["-"]}
    report = validate_config_file(write_config(tmp_path, config_dict))
    assert any("['-'] are not recorded" in w for w in report.warnings)


def test_series_label_puts_qualifiers_last():
    assert series_label("Prevalence", "SEX=Female; TOBACCO_INDICATOR=Daily smoking") == "Prevalence · Daily smoking · Female"
    assert series_label("Level") == "Level"


def test_wide_view_has_readable_names_and_a_code_row():
    data = [obs(indicator_code="R", value_text="4"),
            obs(indicator_code="Q", value_text="Yes", dimensions={"Q": "Q1"}, dimension_labels={"Q": "Ban on TV ads"})]
    res = transform(data, Processing(wide_panel=True))
    if "dimension_labels" in res.dataset and not res.dataset["dimension_labels"].any():
        res.dataset["dimension_labels"] = ["", "Q=Ban on TV ads"]
    view = labelled_wide_panel(res.wide_panel, res.dataset, {("s", "Q"): "E1 Direct bans", ("s", "R"): "R level"})
    assert list(view.columns)[:4] == ["Country code", "Country", "Period", "Year"]
    assert "E1 Direct bans · Ban on TV ads" in view.columns and "R level" in view.columns
    assert view.iloc[0]["R level"] == "s:R" and view.iloc[1]["R level"] == "4"


def test_views_hold_values_missing_marks_and_config_group_order(tmp_path):
    run = fake_run(tmp_path)
    (run / "extraction_config.yaml").write_text(
        "extractions:\n  - source: who_gho\n    indicators:\n"
        "      - {code: PREV, label: Smoking prevalence}\n      - {code: P_Group, label: Protect level}\n",
        encoding="utf-8")
    summary = export_run(run, tmp_path / "site", plan_id="TEST")
    target = tmp_path / "site" / "data" / "TEST" / "runs" / run.name
    index = load(target / summary["views"])
    assert index["years"] == [2020, 2022] and index["groups"] == ["M", "P"]  # PREV comes first in the configuration
    labels = {s["code"]: s["label"] for s in index["series"]}
    assert labels == {"PREV": "Smoking prevalence · Both sexes", "P_Group": "Protect level"}
    cells = {}
    for part in index["parts"]:
        cells.update(load(target / part))
    assert cells["who_gho:P_Group"]["FRA"] == ["4", "5"]
    assert cells["who_gho:P_Group"]["DEU"] == ["Not applicable", {"m": ""}]
    assert cells["who_gho:PREV [SEX=Both sexes]"]["FRA"] == [30.5, None]


def test_view_spec_written_by_claude_is_published(tmp_path):
    run = fake_run(tmp_path)
    (run / "view_spec.json").write_text(json.dumps({"default_view": "country", "note": "test"}), encoding="utf-8")
    export_run(run, tmp_path / "site", plan_id="TEST")
    assert (tmp_path / "site" / "data" / "TEST" / "runs" / run.name / "view_spec.json").exists()
