"""End-to-end extraction tests with mocked APIs (no network)."""

import json

import pandas as pd
import pytest

from conftest import FakeResponse, FakeSession, mock, no_sleep, ok, write_config
from src.connectors.eurostat import decode_jsonstat
from src.connectors.who import WhoGhoConnector
from src.extraction import OutputExistsError, run_extraction
from src.validation import parse_config


def who_session():
    s = FakeSession()
    s.add("/api/Indicator", ok(mock("who_indicator_r_group.json")))
    s.add("/DIMENSION/COUNTRY/", ok(mock("who_dimension_country.json")))
    s.add("$skiptoken=3", ok(mock("who_r_group_page2.json")))
    s.add("/api/R_Group", ok(mock("who_r_group_page1.json")))
    return s


def run(tmp_path, cfg, session, **kw):
    path = write_config(tmp_path, cfg)
    return run_extraction(path, session_factory=lambda: session, sleep=no_sleep, environ={}, **kw)


def test_who_filter_building(config_dict):
    cfg = parse_config(config_dict)
    task = cfg.extractions[0]
    task.filters["Dim1"] = ["SEX_MLE", "SEX_FMLE"]
    f = WhoGhoConnector(cfg.sources[0], client=None).build_filter(task, task.indicators[0], ["FRA", "D'X"])
    assert f == ("SpatialDim in ('FRA','D''X') and TimeDim ge 2007 and TimeDim le 2008 and "
                 "Dim1 in ('SEX_MLE','SEX_FMLE') and SpatialDimType eq 'COUNTRY'")


def test_who_end_to_end(tmp_path, config_dict):
    session = who_session()
    result = run(tmp_path, config_dict, session)
    out = result.output_dir
    assert out == (tmp_path / "output" / "TEST_PROJECT").resolve()

    expected = {"dataset.csv", "dataset_wide.csv", "dataset.xlsx", "dataset.json", "raw_data.json",
                "data_dictionary.xlsx", "extraction_log.txt", "extraction_manifest.json",
                "extraction_config.yaml", "README.md"}
    assert expected <= {p.name for p in out.iterdir()}

    # Raw data preserved exactly, including the duplicate record and both pages.
    raw = json.loads((out / "raw_data.json").read_text(encoding="utf-8"))
    data_requests = [r for r in raw if r["purpose"] == "data:R_Group"]
    assert len(data_requests) == 2
    assert data_requests[1]["body"] == mock("who_r_group_page2.json")

    df = pd.read_csv(out / "dataset.csv", dtype={"value_text": str})
    assert len(df) == 4  # 5 records, one exact duplicate removed
    fra = df[(df.country_code == "FRA") & (df.year == 2007)].iloc[0]
    assert fra.value_text == "Not applicable" and pd.isna(fra.value) and fra.country_name == "France"
    assert set(df.request_id) == {"req_00002", "req_00003"}

    manifest = json.loads((out / "extraction_manifest.json").read_text(encoding="utf-8"))
    assert manifest["records"]["dataset_rows"] == 4
    assert manifest["requests"]["failed"] == 0
    assert manifest["environment"]["application_version"] == "1.0.0"
    assert {f["file"] for f in manifest["output_files"]} >= {"dataset.csv", "raw_data.json", "extraction_log.txt"}
    assert any(s["step"] == "remove_exact_duplicates" and s["removed"] == 1 for s in manifest["processing_steps"])
    assert result.status == "completed_with_warnings"  # no plan linked + duplicate removed

    dictionary = pd.read_excel(out / "data_dictionary.xlsx", sheet_name="variables")
    assert dictionary.iloc[0]["official_name"] == "Raising taxes on tobacco"
    assert dictionary.iloc[0]["countries_covered"] == 2
    log = (out / "extraction_log.txt").read_text(encoding="utf-8")
    assert "req_00002" in log and "Extraction status" in log


def test_existing_output_is_not_overwritten(tmp_path, config_dict):
    run(tmp_path, config_dict, who_session())
    with pytest.raises(OutputExistsError):
        run(tmp_path, config_dict, who_session())
    assert run(tmp_path, config_dict, who_session(), overwrite=True).rows == 4


def test_failed_indicator_gives_partial_status(tmp_path, config_dict):
    config_dict["extractions"][0]["indicators"].append({"code": "NOPE"})
    session = who_session()
    session.routes.insert(0, {"match": "/api/NOPE", "responses": [FakeResponse(404, {})]})
    result = run(tmp_path, config_dict, session)
    assert result.status == "partial"
    manifest = result.manifest
    assert manifest["task_errors"][0]["indicator_code"] == "NOPE"
    assert "not found" in manifest["task_errors"][0]["error"]
    assert any(e["check"] == "failed_request" for e in manifest["errors"])


def test_fail_on_request_error_aborts(tmp_path, config_dict):
    config_dict["extractions"][0]["indicators"].insert(0, {"code": "NOPE"})
    config_dict["validation"] = {"fail_on_request_error": True}
    session = who_session()
    session.routes.insert(0, {"match": "/api/NOPE", "responses": [FakeResponse(500, {})]})
    result = run(tmp_path, config_dict, session)
    assert result.status == "failed" and result.rows == 0


def test_indicator_name_mismatch_is_reported(tmp_path, config_dict):
    config_dict["extractions"][0]["indicators"][0]["name"] = "Something else"
    result = run(tmp_path, config_dict, who_session())
    assert any(i.check == "indicator_definition" and "differs" in i.message for i in result.issues)


def test_unexpected_and_missing_countries(tmp_path, config_dict):
    config_dict["extractions"][0]["countries"] = ["FRA", "DEU", "ITA"]
    result = run(tmp_path, config_dict, who_session())
    geo = [i for i in result.issues if i.check == "geographic_coverage"]
    assert geo and geo[0].details["countries"] == ["ITA"]


def wb_config(config_dict):
    config_dict["sources"] = [{
        "id": "wb", "provider": "World Bank", "api_name": "Indicators API v2", "api_type": "rest",
        "connector": "world_bank", "base_url": "https://api.worldbank.org/v2",
        "documentation_url": "https://datahelpdesk.worldbank.org/knowledgebase/articles/889392",
        "request": {"min_interval_seconds": 0},
    }]
    config_dict["extractions"] = [{
        "id": "smoking", "source": "wb", "countries": ["FRA", "DEU"], "start_year": 2019, "end_year": 2020,
        "indicators": [{"code": "SH.PRV.SMOK", "unit": "% of adults"}],
    }]
    return config_dict


def test_world_bank_pagination(tmp_path, config_dict):
    s = FakeSession()
    s.add("/v2/indicator/", ok(mock("wb_indicator.json")))
    s.add(lambda m, u, p: p.get("page") == 2, ok(mock("wb_page2.json")))
    s.add("/country/FRA;DEU/indicator/SH.PRV.SMOK", ok(mock("wb_page1.json")))
    result = run(tmp_path, wb_config(config_dict), s)
    df = result.manifest and pd.read_csv(result.output_dir / "dataset.csv")
    assert len(df) == 3
    assert df["missing"].sum() == 1  # 2019 FRA has value null
    assert set(df["country_code"]) == {"FRA", "DEU"}
    assert df["unit"].dropna().unique().tolist() == ["% of adults"]
    assert result.manifest["sources"][0]["source_metadata"]["last_updated_by_indicator"]["SH.PRV.SMOK"] == "2026-10-28"
    dictionary = pd.read_excel(result.output_dir / "data_dictionary.xlsx")
    assert "15 years" in dictionary.iloc[0]["definition"]


def test_world_bank_error_message(tmp_path, config_dict):
    s = FakeSession()
    s.add("/v2/indicator/", ok(mock("wb_indicator.json")))
    s.add("/country/", ok(mock("wb_error.json")))
    result = run(tmp_path, wb_config(config_dict), s)
    assert result.status == "failed"
    assert "Invalid value" in result.manifest["task_errors"][0]["error"]


def test_jsonstat_decoding():
    cells = decode_jsonstat(mock("eurostat_smoking.json"))
    assert len(cells) == 5
    by = {(c["codes"]["smoking"], c["codes"]["geo"], c["codes"]["time"]): c for c in cells}
    assert by[("NSM", "DE", "2014")]["value"] == 78.3
    assert by[("NSM", "EL", "2014")]["value"] is None and by[("NSM", "EL", "2014")]["status"] == ":"
    assert by[("SM_DAY", "DE", "2014")]["value"] == 15.9


def eurostat_config(config_dict):
    config_dict["sources"] = [{
        "id": "estat", "provider": "Eurostat", "api_name": "Statistics API", "api_type": "json-stat",
        "connector": "eurostat", "base_url": "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0",
        "documentation_url": "https://wikis.ec.europa.eu/display/EUROSTATHELP/API+Statistics+-+data+query",
        "request": {"min_interval_seconds": 0},
    }]
    config_dict["extractions"] = [{
        "id": "ehis", "source": "estat", "countries": ["DE", "EL"], "start_year": 2014, "end_year": 2019,
        "filters": {"sex": "T", "unit": "PC"},
        "indicators": [{"code": "hlth_ehis_sk1e", "filters": {"smoking": ["NSM", "SM_DAY"]}}],
    }]
    return config_dict


def test_eurostat_end_to_end(tmp_path, config_dict):
    s = FakeSession().add("/data/hlth_ehis_sk1e", ok(mock("eurostat_smoking.json")))
    result = run(tmp_path, eurostat_config(config_dict), s)
    params = s.calls[0]["params"]
    assert params["geo"] == ["DE", "EL"] and params["smoking"] == ["NSM", "SM_DAY"]
    assert params["sinceTimePeriod"] == "2014" and params["untilTimePeriod"] == "2019"
    df = pd.read_csv(result.output_dir / "dataset.csv")
    assert set(df["country_code"]) == {"DEU", "GRC"}
    assert set(df["country_code_source"]) == {"DE", "EL"}
    assert df["unit"].unique().tolist() == ["Percentage"]
    flagged = df[df["comments"].notna()].iloc[0]
    assert flagged["missing"] and "flag ':'" in flagged["comments"]


def test_eurostat_reserved_filter_rejected(config_dict):
    cfg = eurostat_config(config_dict)
    cfg["extractions"][0]["filters"]["geo"] = "FR"
    with pytest.raises(Exception) as exc:
        parse_config(cfg)
    assert "set by the engine" in str(exc.value)


def test_eurostat_dataset_not_found(tmp_path, config_dict):
    s = FakeSession().add("/data/", FakeResponse(404, mock("eurostat_404.json")))
    result = run(tmp_path, eurostat_config(config_dict), s)
    assert result.status == "failed"
    assert "not available" in result.manifest["task_errors"][0]["error"]


def generic_config(config_dict):
    config_dict["sources"] = [{
        "id": "gen", "provider": "Example", "api_name": "Example API", "api_type": "rest",
        "connector": "generic_rest", "base_url": "https://api.example.org/v1",
        "endpoint": "indicators/{indicator}/data", "documentation_url": "https://api.example.org/docs",
        "request": {"min_interval_seconds": 0},
        "pagination": {"type": "page", "page_param": "p", "total_pages_path": "meta.total_pages"},
        "response": {"records_path": "data",
                     "fields": {"country_code": "iso", "period": "yr", "value": "v", "unit": "u"}},
    }]
    config_dict["extractions"] = [{
        "id": "gen_task", "source": "gen", "countries": ["FRA", "DEU"], "start_year": 2020, "end_year": 2020,
        "query_params": {"countries": "{countries}", "from": "{start_year}", "to": "{end_year}"},
        "indicators": [{"code": "ABC"}],
    }]
    return config_dict


def test_generic_rest_page_pagination(tmp_path, config_dict):
    s = FakeSession()
    s.add(lambda m, u, p: p.get("p") == 2, ok(mock("generic_page2.json")))
    s.add("/indicators/ABC/data", ok(mock("generic_page1.json")))
    result = run(tmp_path, generic_config(config_dict), s)
    assert s.calls[0]["params"] == {"countries": "FRA;DEU", "from": "2020", "to": "2020", "p": 1}
    df = pd.read_csv(result.output_dir / "dataset.csv", keep_default_na=False, na_values=[""])
    assert len(df) == 2
    deu = df[df.country_code == "DEU"].iloc[0]
    assert pd.isna(deu.value) and deu.value_text == "n/a"


def test_generic_rest_requires_country_placeholder(config_dict):
    cfg = generic_config(config_dict)
    cfg["extractions"][0]["query_params"] = {}
    with pytest.raises(Exception) as exc:
        parse_config(cfg)
    assert "would not be sent" in str(exc.value)


def test_secrets_never_reach_outputs(tmp_path, config_dict):
    secret = "very-secret-key-42"
    cfg = generic_config(config_dict)
    cfg["sources"][0]["authentication"] = {"method": "api_key_query", "env_var": "EX_KEY", "param_name": "key"}
    s = FakeSession()
    s.add(lambda m, u, p: p.get("p") == 2, ok(mock("generic_page2.json")))
    s.add("/indicators/ABC/data", ok(mock("generic_page1.json")))
    path = write_config(tmp_path, cfg)
    result = run_extraction(path, session_factory=lambda: s, sleep=no_sleep, environ={"EX_KEY": secret})
    assert s.calls[0]["params"]["key"] == secret
    for f in result.output_dir.iterdir():
        if f.suffix in (".json", ".csv", ".txt", ".md", ".yaml"):
            assert secret not in f.read_text(encoding="utf-8"), f.name
