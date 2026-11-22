"""Stage 1 record in the extraction plan: scoping status, register links, render-plan, README."""

import copy
import json

import pytest

from conftest import ROOT, no_sleep, write_config
from src.extraction import run_extraction
from src.main import main
from src.render import render_plan
from src.validation import ConfigValidationError, load_plan, parse_plan, plan_warnings
from test_extraction import who_session

PROJECT_PLAN = ROOT / "projects" / "WHO_MPOWER" / "extraction_plan.json"
EXAMPLE_PLAN = ROOT / "docs" / "examples" / "WHO_MPOWER_stage1_plan.json"
EXAMPLE_RENDERED = ROOT / "docs" / "examples" / "WHO_MPOWER_stage1_rendered.md"


def project_plan():
    return json.loads(PROJECT_PLAN.read_text(encoding="utf-8"))


def example_plan():
    return json.loads(EXAMPLE_PLAN.read_text(encoding="utf-8"))


def approve(plan):
    plan["outstanding_decisions"] = []
    plan["approval"] = {"status": "approved", "approved_by": "Researcher", "approved_on": "2026-11-22"}
    return plan


def plan_errors(data):
    with pytest.raises(ConfigValidationError) as exc:
        parse_plan(data)
    return "\n".join(exc.value.errors)


def row(plan, row_id):
    return next(r for r in plan["scoping"]["requirements_register"] if r["id"] == row_id)


# -- the scoping status ---------------------------------------------------------


def test_scoping_checkpoint_needs_only_stage_1():
    plan = parse_plan(example_plan())
    assert plan.approval.status == "scoping"
    assert plan.research is None and plan.sources == [] and plan.indicator_mapping == []
    assert len(plan.scoping.requirements_register) == 19


def test_scoping_status_requires_scoping_section():
    plan = example_plan()
    del plan["scoping"]
    assert "must contain the 'scoping' section" in plan_errors(plan)


def test_draft_requires_the_later_stages():
    plan = example_plan()
    plan["approval"] = {"status": "draft"}
    errors = plan_errors(plan)
    assert "requires ['research', 'expected_data_structure', 'sources', 'indicator_mapping']" in errors


def test_ambiguous_rows_block_draft():
    plan = project_plan()
    row(plan, "R2").update(status="ambiguous", questions=[])
    assert "still 'ambiguous'" in plan_errors(plan)


def test_plan_without_scoping_is_still_valid_but_warned():
    plan = project_plan()
    del plan["scoping"]
    for m in plan["indicator_mapping"]:
        del m["register_rows"]
    warnings = plan_warnings(parse_plan(plan))
    assert any("no 'scoping' section" in w for w in warnings)


# -- register rows -------------------------------------------------------------


@pytest.mark.parametrize("change,message", [
    ({"status": "parked"}, "must give a 'reason'"),
    ({"status": "dropped"}, "must give a 'reason'"),
    ({"status": "needs_another_source"}, "must give a 'reason'"),
    ({"candidates": []}, "names no candidate retrieval unit"),
    ({"user_wording": None}, "must keep their 'user_wording'"),
    ({"kind_of_data": ["vibes"]}, "kind_of_data"),
    ({"id": "row1"}, "requirements_register.1.id"),
])
def test_register_row_rules(change, message):
    plan = example_plan()
    plan["scoping"]["requirements_register"][1].update(change)
    assert message in plan_errors(plan)


def test_assumed_row_may_have_no_wording():
    plan = parse_plan(project_plan())
    r8 = next(r for r in plan.scoping.requirements_register if r.id == "R8")
    assert r8.origin == "assumed" and r8.user_wording is None


def test_duplicate_row_ids():
    plan = example_plan()
    plan["scoping"]["requirements_register"][1]["id"] = "R1"
    assert "duplicate register row id(s) ['R1']" in plan_errors(plan)


def test_references_must_exist():
    plan = example_plan()
    plan["scoping"]["questions"][0]["rows"].append("R99")
    assert "question 'Q1' refers to unknown register row(s) ['R99']" in plan_errors(plan)

    plan = example_plan()
    row(plan, "R1")["questions"] = ["Q9"]
    assert "register row 'R1' refers to unknown question(s) ['Q9']" in plan_errors(plan)

    plan = project_plan()
    plan["indicator_mapping"][0]["register_rows"] = ["R42"]
    assert "refers to unknown register row(s) ['R42']" in plan_errors(plan)


def test_question_answer_consistency():
    plan = example_plan()
    plan["scoping"]["questions"][0]["status"] = "answered"
    assert "records no answer" in plan_errors(plan)

    plan = example_plan()
    plan["scoping"]["questions"][0]["answer"] = "Yes"
    assert "records an answer but its status is 'open'" in plan_errors(plan)

    plan = example_plan()
    plan["scoping"]["questions"][0].update(status="answered", answer="No, policy data only.")
    assert parse_plan(plan).scoping.questions[0].answer == "No, policy data only."


# -- traceability between register rows and indicators ------------------------


def test_project_plan_is_fully_traced():
    plan = load_plan(PROJECT_PLAN)
    assert plan.uncovered_rows() == [] and plan.unlinked_indicators() == []
    assert plan_warnings(plan) == []


def test_draft_warns_about_untraced_rows_and_indicators():
    plan = project_plan()
    plan["indicator_mapping"][0]["register_rows"] = []
    warnings = plan_warnings(parse_plan(plan))
    assert any("row R1 ('WHO MPOWER') is 'clear' but no indicator mapping covers it" in w for w in warnings)
    assert any("indicator 'M_Group' names no register row" in w for w in warnings)


def test_approval_requires_full_traceability():
    parse_plan(approve(project_plan()))  # the migrated plan can be approved as it is

    plan = approve(project_plan())
    plan["indicator_mapping"][0]["register_rows"] = []
    errors = plan_errors(plan)
    assert "['R1'] are 'clear' but no indicator mapping covers them" in errors

    plan = approve(project_plan())
    plan["indicator_mapping"][0]["register_rows"] = ["R1", "R2"]  # R2 stays covered
    plan["indicator_mapping"][1]["register_rows"] = []
    assert "indicator(s) ['P_Group'] name no register row" in plan_errors(plan)


def test_approval_requires_resolved_rows_and_questions():
    plan = approve(project_plan())
    plan["scoping"]["requirements_register"].append({
        "id": "R9", "user_wording": "Workplace cessation", "interpreted_concept": "Workplace programmes",
        "kind_of_data": ["programme_service"], "status": "needs_another_source", "reason": "Not in GHO"})
    assert "['R9'] still 'need another source'" in plan_errors(plan)

    plan["scoping"]["requirements_register"][-1]["status"] = "parked"
    plan["scoping"]["questions"] = [{"id": "Q1", "text": "Keep it?", "default": "Park", "rows": ["R9"]}]
    assert "scoping question(s) ['Q1'] are open" in plan_errors(plan)

    plan["scoping"]["questions"][0]["status"] = "default_applied"
    parsed = parse_plan(plan)
    assert [r.id for r in parsed.excluded_rows()] == ["R9"]


# -- render-plan and README -----------------------------------------------------


def test_committed_rendered_example_matches_plan():
    plan = load_plan(EXAMPLE_PLAN)
    assert EXAMPLE_RENDERED.read_text(encoding="utf-8") == render_plan(plan, plan_warnings(plan)), (
        "run: python -m src.main render-plan --plan docs/examples/WHO_MPOWER_stage1_plan.json "
        "--output docs/examples/WHO_MPOWER_stage1_rendered.md"
    )


def test_render_full_plan():
    text = render_plan(load_plan(PROJECT_PLAN))
    for expected in ("# Extraction plan — WHO_MPOWER", "## Stage 1 — Scoping", "### Requirements Register",
                     "| R8 | — | Context |", "## Indicator mapping", "| R7 | Raise taxes (R) policy level |",
                     "## Known limitations", "## Outstanding decisions"):
        assert expected in text


def test_render_escapes_table_cells():
    plan = example_plan()
    row(plan, "R2")["user_wording"] = "Bans | public places"
    assert "| R2 | Bans / public places |" in render_plan(parse_plan(plan))


def test_render_plan_cli(tmp_path, capsys):
    out = tmp_path / "plan.md"
    assert main(["render-plan", "--plan", str(EXAMPLE_PLAN), "--output", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# Extraction plan — WHO_MPOWER_policies")
    assert main(["validate-plan", "--plan", str(EXAMPLE_PLAN)]) == 0
    printed = capsys.readouterr().out
    assert "register: 19 row(s) (2 ambiguous, 13 clear, 4 needs another source); 5 open question(s)" in printed


def test_readme_lists_items_left_out(tmp_path, config_dict):
    plan = project_plan()
    plan["scoping"]["requirements_register"].append({
        "id": "R9", "user_wording": "Cigarette prices", "interpreted_concept": "Retail price of cigarettes",
        "kind_of_data": ["price_tax"], "status": "parked", "reason": "Not needed for the test case"})
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    config = copy.deepcopy(config_dict)
    config["project"]["extraction_plan"] = "plan.json"
    path = write_config(tmp_path, config)
    result = run_extraction(path, session_factory=who_session, sleep=no_sleep, environ={},
                            require_plan_approval=False)
    readme = (result.output_dir / "README.md").read_text(encoding="utf-8")
    assert "## Not included in this dataset" in readme
    assert "| R9 | Cigarette prices | parked | Not needed for the test case |" in readme
