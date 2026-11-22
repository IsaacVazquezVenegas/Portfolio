"""Data Fetcher page export: plan history, approval from the page, run summaries."""

import csv
import json

import pytest
from openpyxl import Workbook

from conftest import ROOT
from src.main import main
from src.page_export import (
    ApprovalError,
    apply_approval,
    diff_plans,
    export_plan,
    export_run,
    plan_fingerprint,
)

PROJECT_PLAN = ROOT / "projects" / "WHO_MPOWER" / "extraction_plan.json"
EXAMPLE_PLAN = ROOT / "docs" / "examples" / "WHO_MPOWER_stage1_plan.json"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def approvable(tmp_path):
    plan = load(PROJECT_PLAN)
    plan["outstanding_decisions"] = []
    path = tmp_path / "extraction_plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path, plan


# -- fingerprint and approval ------------------------------------------------

def test_fingerprint_ignores_approval_block():
    plan = load(PROJECT_PLAN)
    other = dict(plan, approval={"status": "approved", "approved_by": "X", "approved_on": "2026-11-22"})
    assert plan_fingerprint(plan) == plan_fingerprint(other)
    changed = dict(plan, known_limitations=plan["known_limitations"] + ["new"])
    assert plan_fingerprint(plan) != plan_fingerprint(changed)


def test_apply_approval_records_the_researcher(tmp_path):
    path, plan = approvable(tmp_path)
    record = {"status": "approved", "plan_sha256": plan_fingerprint(plan), "by_name": "Pedro",
              "at": "2026-11-22T15:00:00Z", "note": "OK for the overview"}
    block = apply_approval(path, record)
    saved = load(path)
    assert saved["approval"] == block
    assert block["approved_by"] == "Pedro" and block["approved_on"] == "2026-11-22"
    assert "OK for the overview" in block["notes"]


def test_apply_approval_refuses_another_version(tmp_path):
    path, plan = approvable(tmp_path)
    record = {"status": "approved", "plan_sha256": "0" * 64, "by_name": "Pedro", "at": "2026-11-22"}
    with pytest.raises(ApprovalError, match="another version"):
        apply_approval(path, record)
    assert load(path)["approval"]["status"] == "draft"


def test_apply_approval_refuses_withdrawn_or_unnamed(tmp_path):
    path, plan = approvable(tmp_path)
    sha = plan_fingerprint(plan)
    with pytest.raises(ApprovalError, match="withdrawn"):
        apply_approval(path, {"status": "withdrawn", "plan_sha256": sha, "by_name": "Pedro"})
    with pytest.raises(ApprovalError, match="names no researcher"):
        apply_approval(path, {"status": "approved", "plan_sha256": sha, "by_name": " "})


def test_apply_approval_keeps_plan_validation(tmp_path):
    # The project plan still lists outstanding decisions: approval must be refused, file untouched.
    path = tmp_path / "extraction_plan.json"
    path.write_text(PROJECT_PLAN.read_text(encoding="utf-8"), encoding="utf-8")
    plan = load(path)
    with pytest.raises(ApprovalError, match="cannot be approved"):
        apply_approval(path, {"status": "approved", "plan_sha256": plan_fingerprint(plan), "by_name": "Pedro"})
    assert load(path) == plan


# -- history -------------------------------------------------------------------

def test_diff_reports_register_rows_by_id():
    old = load(EXAMPLE_PLAN)
    new = json.loads(json.dumps(old))
    r19 = next(r for r in new["scoping"]["requirements_register"] if r["id"] == "R19")
    r19["status"] = "dropped"
    r19["reason"] = "Not needed"
    new["scoping"]["requirements_register"] = [r for r in new["scoping"]["requirements_register"] if r["id"] != "R14"]
    changes = {c["path"]: c for c in diff_plans(old, new)}
    assert changes["scoping.requirements_register[R19].status"]["before"] == "ambiguous"
    assert changes["scoping.requirements_register[R19].status"]["after"] == "dropped"
    assert changes["scoping.requirements_register[R19].reason"]["change"] == "added"
    assert changes["scoping.requirements_register[R14]"]["change"] == "removed"


def test_export_plan_logs_changes_and_lists_projects(tmp_path):
    site = tmp_path / "site"
    plan_path = tmp_path / "plan.json"
    plan = load(EXAMPLE_PLAN)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    first = export_plan(plan_path, site, note="first")
    assert first["changes"] == 1
    again = export_plan(plan_path, site)
    assert again["changes"] == 0  # nothing changed, nothing logged
    plan["scoping"]["purpose"] = "Descriptive overview"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    export_plan(plan_path, site, note="purpose stated")
    history = load(site / "data" / plan["plan_id"] / "history.json")
    assert [h["note"] for h in history] == ["purpose stated", "first"]
    assert history[0]["changes"][0]["path"] == "scoping.purpose"
    projects = load(site / "data" / "projects.json")
    assert projects[0]["id"] == plan["plan_id"] and projects[0]["status"] == "scoping"
    assert projects[0]["open_questions"] == 5


# -- runs ----------------------------------------------------------------------

COLUMNS = ["source_id", "indicator_code", "indicator_name", "category", "country_code", "country_name",
           "year", "dimension_labels", "value", "value_text", "unit", "missing"]


def fake_run(tmp_path):
    run = tmp_path / "TEST_20261122_120000"
    run.mkdir()
    rows = [
        ["who_gho", "P_Group", "Protect", "P", "FRA", "France", "2020", "", "", "4", "level", "False"],
        ["who_gho", "P_Group", "Protect", "P", "FRA", "France", "2022", "", "", "5", "level", "False"],
        ["who_gho", "P_Group", "Protect", "P", "DEU", "Germany", "2020", "", "", "Not applicable", "level", "False"],
        ["who_gho", "P_Group", "Protect", "P", "DEU", "Germany", "2022", "", "", "", "level", "True"],
        ["who_gho", "PREV", "Prevalence", "M", "FRA", "France", "2020", "SEX=Both sexes", "30.5", "30.5", "%", "False"],
        ["who_gho", "PREV", "Prevalence", "M", "DEU", "Germany", "2020", "SEX=Both sexes", "22.1", "22.1", "%", "False"],
    ]
    with open(run / "dataset.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        w.writerows(rows)
    (run / "extraction_manifest.json").write_text(json.dumps({
        "project": "TEST", "status": "success",
        "execution": {"started_at": "2026-11-22T12:00:00+00:00", "finished_at": "2026-11-22T12:00:05+00:00"},
        "configuration": {"sha256": "abc", "plan_approval": {"status": "approved", "approved_by": "Pedro"}},
        "requests": [{}, {}], "errors": [], "warnings": []}), encoding="utf-8")
    (run / "extraction_config.yaml").write_text("schema_version: '1.0'\n", encoding="utf-8")
    (run / "raw_data.json").write_text(json.dumps([{"request_id": f"req_{i}", "body": "x" * 50} for i in range(5)]),
                                       encoding="utf-8")
    wb = Workbook()
    wb.active.title = "data"
    wb.active.append(["country", "value"])
    wb.active.append(["FRA", 1.5])
    wb.save(run / "dataset.xlsx")
    return run


def test_export_run_summarises_and_publishes_servable_files(tmp_path, monkeypatch):
    import src.page_export as pe

    monkeypatch.setattr(pe, "MAX_PART_BYTES", 150)  # forces several raw parts
    run = fake_run(tmp_path)
    site = tmp_path / "site"
    res = export_run(run, site, plan_id="TEST")
    target = site / "data" / "TEST" / "runs" / run.name
    summary = load(target / "summary.json")
    assert summary["rows"] == 6 and summary["test_run"] is False
    series = {s["key"]: s for s in summary["series"]}
    p = series["who_gho:P_Group"]
    assert p["kind"] == "categorical" and p["ordinal"] is True
    assert p["coverage"] == {"DEU": "vm", "FRA": "vv"}
    assert p["distribution"]["2020"] == {"4": 1, "Not applicable": 1}
    prev = series["who_gho:PREV [SEX=Both sexes]"]
    assert prev["kind"] == "numeric" and prev["distribution"]["2020"][2] == pytest.approx(26.3)
    files = {f["name"]: f for f in summary["files"]}
    assert files["extraction_config.yaml"]["path"] == "extraction_config.yaml.txt"
    assert files["dataset.xlsx"]["build"] == "xlsx"
    spec = load(target / files["dataset.xlsx"]["path"])
    assert spec["sheets"][0]["rows"] == [["country", "value"], ["FRA", 1.5]]
    parts = files["raw_data.json"]["parts"]
    assert len(parts) > 1
    joined = [rec for part in parts for rec in load(target / part)]
    assert [r["request_id"] for r in joined] == [f"req_{i}" for i in range(5)]
    served = {".csv", ".json", ".txt", ".md"}
    assert all(f.suffix in served for f in target.iterdir())
    assert load(site / "data" / "projects.json")[0]["runs"][0]["rows"] == 6
    assert res["series"] == 2


def test_cli_page_export(tmp_path, capsys):
    site = tmp_path / "site"
    assert main(["page-export", "--plan", str(EXAMPLE_PLAN), "--site", str(site), "--note", "x"]) == 0
    assert "change(s) logged" in capsys.readouterr().out
    assert main(["page-export", "--site", str(site)]) == 1
