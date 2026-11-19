"""Level 2 live tests against public APIs.

Skipped by default. Run with:  python -m pytest --live tests/test_live.py
"""

import copy

import pandas as pd
import pytest

from conftest import base_config, write_config
from src.extraction import run_extraction

pytestmark = pytest.mark.live


def test_who_gho_live(tmp_path):
    cfg = copy.deepcopy(base_config())
    cfg["sources"][0]["request"] = {"min_interval_seconds": 0.5}
    cfg["extractions"][0]["indicators"] = [
        {"code": "R_Group", "name": "Raising taxes on tobacco"},
        {"code": "M_Est_tob_curr_std", "filters": {"Dim1": "SEX_BTSX"}},
    ]
    cfg["extractions"][0].update(start_year=2007, end_year=2022)
    result = run_extraction(write_config(tmp_path, cfg))
    assert result.status in ("success", "completed_with_warnings"), result.manifest["errors"]
    df = pd.read_csv(result.output_dir / "dataset.csv", keep_default_na=False, na_values=[""])
    r = df[df.indicator_code == "R_Group"]
    assert set(r.country_code) == {"FRA", "DEU"} and r.value.isna().all()  # categorical, never numeric
    assert {2007, 2022} <= set(r.year)
    p = df[df.indicator_code == "M_Est_tob_curr_std"]
    assert len(p) > 0 and p.value.notna().all()


def test_eurostat_live(tmp_path):
    cfg = copy.deepcopy(base_config())
    cfg["sources"] = [{
        "id": "estat", "provider": "Eurostat", "api_name": "Statistics API", "api_type": "json-stat",
        "connector": "eurostat", "base_url": "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0",
        "documentation_url": "https://wikis.ec.europa.eu/display/EUROSTATHELP/API+Statistics+-+data+query",
    }]
    cfg["extractions"] = [{
        "id": "pop", "source": "estat", "countries": ["FR", "EL"], "start_year": 2020, "end_year": 2021,
        "filters": {"sex": "T", "age": "TOTAL", "unit": "NR"}, "indicators": [{"code": "demo_pjan"}],
    }]
    result = run_extraction(write_config(tmp_path, cfg))
    df = pd.read_csv(result.output_dir / "dataset.csv")
    assert set(df.country_code) == {"FRA", "GRC"} and len(df) == 4 and df.value.notna().all()


def test_world_bank_live(tmp_path):
    cfg = copy.deepcopy(base_config())
    cfg["sources"] = [{
        "id": "wb", "provider": "World Bank", "api_name": "WDI API", "api_type": "rest", "connector": "world_bank",
        "base_url": "https://api.worldbank.org/v2",
        "documentation_url": "https://datahelpdesk.worldbank.org/knowledgebase/articles/889392",
    }]
    cfg["extractions"] = [{
        "id": "pop", "source": "wb", "countries": ["FRA", "DEU"], "start_year": 2019, "end_year": 2020,
        "indicators": [{"code": "SP.POP.TOTL"}],
    }]
    result = run_extraction(write_config(tmp_path, cfg))
    df = pd.read_csv(result.output_dir / "dataset.csv")
    assert set(df.country_code) == {"FRA", "DEU"} and len(df) == 4
