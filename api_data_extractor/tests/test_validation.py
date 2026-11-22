import json
from pathlib import Path

import pytest

from conftest import ROOT, write_config
from src.models import ExtractionConfig, ExtractionPlan
from src.validation import (
    ConfigValidationError,
    generate_schema,
    load_plan,
    parse_config,
    validate_config_file,
)


def errors_of(data):
    with pytest.raises(ConfigValidationError) as exc:
        parse_config(data)
    return "\n".join(exc.value.errors)


def test_valid_config(config_dict):
    cfg = parse_config(config_dict)
    assert cfg.project.name == "TEST_PROJECT"
    assert cfg.processing.preserve_raw is True
    assert cfg.extractions[0].country_batch_size == 50


def test_missing_mandatory_field(config_dict):
    del config_dict["project"]["description"]
    assert "project.description: Field required" in errors_of(config_dict)


def test_unknown_field_is_rejected(config_dict):
    config_dict["extractions"][0]["python_code"] = "import os"
    assert "unknown field" in errors_of(config_dict)


def test_invalid_type(config_dict):
    config_dict["extractions"][0]["start_year"] = "two thousand"
    assert "extractions.0.start_year" in errors_of(config_dict)


def test_unknown_connector(config_dict):
    config_dict["sources"][0]["connector"] = "magic"
    assert "unknown connector 'magic'" in errors_of(config_dict)


def test_invalid_date_range(config_dict):
    config_dict["extractions"][0]["start_year"] = 2020
    config_dict["extractions"][0]["end_year"] = 2010
    assert "end_year (2010) is before start_year (2020)" in errors_of(config_dict)


def test_unknown_source_reference(config_dict):
    config_dict["extractions"][0]["source"] = "nope"
    assert "unknown source 'nope'" in errors_of(config_dict)


def test_https_required(config_dict):
    config_dict["sources"][0]["base_url"] = "http://ghoapi.azureedge.net/api"
    assert "must use https" in errors_of(config_dict)


def test_secret_headers_refused(config_dict):
    config_dict["sources"][0]["request"]["headers"] = {"Authorization": "Bearer abc"}
    assert "cannot be set in a configuration" in errors_of(config_dict)


def test_auth_requires_env_var(config_dict):
    config_dict["sources"][0]["authentication"] = {"method": "api_key_header", "param_name": "X-Key"}
    assert "requires 'env_var'" in errors_of(config_dict)


def test_unsupported_filter_for_connector(config_dict):
    config_dict["extractions"][0]["filters"] = {"DROP TABLE": "x"}
    assert "not supported by connector 'who_gho'" in errors_of(config_dict)


def test_invalid_country_codes(config_dict):
    config_dict["extractions"][0]["countries"] = ["fra", "DEU", "DEU"]
    assert "invalid country code" in errors_of(config_dict)


def test_unsupported_template_placeholder(config_dict):
    config_dict["sources"][0].update(connector="generic_rest", api_type="rest", endpoint="data/{__class__}",
                                     response={"fields": {"country_code": "c", "period": "p", "value": "v"}})
    assert "unsupported placeholder" in errors_of(config_dict)


def test_generic_rest_requires_response_mapping(config_dict):
    config_dict["sources"][0].update(connector="generic_rest", api_type="rest", endpoint="x/{country}")
    assert "requires a 'response' mapping" in errors_of(config_dict)


def test_unsupported_operation_preserve_raw_false(config_dict):
    config_dict["processing"]["preserve_raw"] = False
    assert "processing.preserve_raw" in errors_of(config_dict)


def test_output_folder_cannot_escape(config_dict):
    config_dict["output"]["folder_name"] = "../elsewhere"
    assert "plain folder name" in errors_of(config_dict)


def test_yaml_syntax_error(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("project: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigValidationError) as exc:
        validate_config_file(p)
    assert "not syntactically valid" in exc.value.title


def test_yaml_cannot_construct_python_objects(tmp_path):
    p = tmp_path / "evil.yaml"
    p.write_text("project: !!python/object/apply:os.system ['echo hacked']\n", encoding="utf-8")
    with pytest.raises(ConfigValidationError):
        validate_config_file(p)


def test_missing_authentication_env_var(tmp_path, config_dict):
    config_dict["sources"][0]["authentication"] = {"method": "bearer_token", "env_var": "TEST_API_TOKEN"}
    path = write_config(tmp_path, config_dict)
    with pytest.raises(ConfigValidationError) as exc:
        validate_config_file(path, environ={})
    assert "TEST_API_TOKEN" in exc.value.render()
    report = validate_config_file(path, environ={"TEST_API_TOKEN": "secret-value"})
    assert report.config.sources[0].authentication.method == "bearer_token"


def _plan(status="draft", outstanding=None):
    plan = json.loads((ROOT / "projects" / "WHO_MPOWER" / "extraction_plan.json").read_text(encoding="utf-8"))
    plan["approval"] = {"status": status}
    if status == "approved":
        plan["approval"].update(approved_by="Researcher", approved_on="2026-11-19")
    plan["outstanding_decisions"] = outstanding or []
    return plan


def test_unapproved_plan_blocks_execution(tmp_path, config_dict):
    (tmp_path / "plan.json").write_text(json.dumps(_plan("draft")), encoding="utf-8")
    config_dict["project"]["extraction_plan"] = "plan.json"
    path = write_config(tmp_path, config_dict)
    with pytest.raises(ConfigValidationError) as exc:
        validate_config_file(path)
    assert "approval status 'draft'" in exc.value.render()
    report = validate_config_file(path, require_plan_approval=False)
    assert any("draft" in w for w in report.warnings)


def test_approved_plan(tmp_path, config_dict):
    (tmp_path / "plan.json").write_text(json.dumps(_plan("approved")), encoding="utf-8")
    config_dict["project"]["extraction_plan"] = "plan.json"
    report = validate_config_file(write_config(tmp_path, config_dict))
    assert report.plan.approval.status == "approved"


def test_plan_cannot_be_approved_with_outstanding_decisions(tmp_path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(_plan("approved", ["pick years"])), encoding="utf-8")
    with pytest.raises(ConfigValidationError) as exc:
        load_plan(p)
    assert "outstanding_decisions" in exc.value.render()


def test_plan_confirmed_requires_evidence():
    plan = _plan()
    plan["indicator_mapping"][0]["evidence"] = []
    with pytest.raises(Exception) as exc:
        ExtractionPlan.model_validate(plan)
    assert "has no evidence" in str(exc.value)


def test_project_files_are_valid():
    load_plan(ROOT / "projects" / "WHO_MPOWER" / "extraction_plan.json")
    validate_config_file(ROOT / "projects" / "WHO_MPOWER" / "extraction_config.yaml", require_plan_approval=False)
    for example in (ROOT / "config").glob("*.yaml"):
        validate_config_file(example, require_plan_approval=False, environ={"EXAMPLE_API_KEY": "x"})


@pytest.mark.parametrize("filename,model,title", [
    ("extraction_config.schema.json", ExtractionConfig, "Extraction configuration"),
    ("extraction_plan.schema.json", ExtractionPlan, "Extraction plan"),
])
def test_committed_schemas_match_models(filename, model, title):
    committed = json.loads((ROOT / "schemas" / filename).read_text(encoding="utf-8"))
    assert committed == generate_schema(model, title), "run: python -m src.main schemas"
