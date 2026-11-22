"""Documentation of an extraction: data dictionary, manifest and README."""

from __future__ import annotations

import platform
import sys
from typing import Any, Dict, List, Optional

import pandas as pd

from . import APP_NAME, __version__
from .connectors.base import BaseConnector
from .models import ExtractionConfig, ExtractionPlan
from .quality import QualityIssue

COLUMN_DESCRIPTIONS = {
    "source_id": "Identifier of the data source in the configuration",
    "provider": "Institution providing the data",
    "task_id": "Extraction task (configuration 'extractions[].id') that retrieved the observation",
    "indicator_code": "Technical indicator code at the source (dataset code for Eurostat)",
    "indicator_name": "Official indicator name (source metadata, else configuration)",
    "category": "Category assigned in the configuration (e.g. MPOWER component)",
    "country_code": "Country identifier (ISO 3166-1 alpha-3 when processing.country_codes = iso3)",
    "country_code_source": "Country identifier exactly as returned by the source",
    "country_name": "Country name (source label, else reference table)",
    "spatial_type": "Type of geographic unit reported by the source (e.g. COUNTRY)",
    "year": "Calendar year derived from the period",
    "period": "Observation period (YYYY, YYYY-Qn, YYYY-MM)",
    "dimensions": "Other dimensions of the observation as 'TYPE=CODE; ...' (e.g. SEX=SEX_MLE)",
    "dimension_labels": "Human-readable labels of the dimension codes",
    "value": "Numeric value, only when the source provides it as a number",
    "value_text": "Value exactly as displayed by the source (may be a category or a range)",
    "low": "Lower bound of the uncertainty interval, when provided",
    "high": "Upper bound of the uncertainty interval, when provided",
    "unit": "Unit of measurement (source, else configuration)",
    "comments": "Source comments or observation flags",
    "missing": "True when the observation has no value at all (or a configured missing marker)",
    "duplicate_conflict": "True when another observation has the same identity but a different value",
    "source_record_id": "Record identifier at the source, when provided",
    "source_updated": "Last update timestamp of the record/dataset at the source",
    "request_id": "Identifier of the HTTP response in raw_data.json that contains this observation",
    "source_url": "URL of the request that returned the observation",
    "retrieved_at": "UTC timestamp of the request",
}


def data_dictionary(
    config: ExtractionConfig,
    df: pd.DataFrame,
    indicator_metadata: Dict[str, Dict[str, Any]],
    steps: List[Dict[str, Any]],
    plan: Optional[ExtractionPlan],
) -> Dict[str, pd.DataFrame]:
    processing = "; ".join(s["step"] for s in steps)
    columns = pd.DataFrame(
        [{"column": c, "description": COLUMN_DESCRIPTIONS.get(c, ""), "type": str(df[c].dtype) if c in df else ""}
         for c in df.columns]
    )
    plan_map = {}
    if plan:
        plan_map = {(m.source, m.indicator_code): m for m in plan.indicator_mapping}
    rows = []
    for task in config.extractions:
        source = config.source(task.source)
        for ind in task.indicators:
            sub = df[(df["task_id"] == task.id) & (df["indicator_code"] == ind.code)] if len(df) else df
            meta = indicator_metadata.get(f"{task.source}:{ind.code}") or {}
            pm = plan_map.get((task.source, ind.code))
            years = sorted({int(y) for y in sub["year"].dropna()}) if len(sub) else []
            units = sorted({str(u) for u in sub["unit"].dropna()}) if len(sub) else []
            rows.append({
                "variable_name": ind.code,
                "official_name": meta.get("name") or (next(iter(sub["indicator_name"].dropna()), None) if len(sub) else None) or ind.name,
                "definition": ind.definition or meta.get("definition") or (pm.definition if pm else None),
                "research_variable": pm.research_variable if pm else None,
                "category": ind.category,
                "source": f"{source.provider} - {source.api_name}",
                "source_id": source.id,
                "indicator_code": ind.code,
                "unit": ind.unit or (units[0] if len(units) == 1 else ", ".join(units)) or meta.get("unit"),
                "measurement_scale": ind.measurement_scale,
                "filters": "; ".join(f"{k}={v}" for k, v in sorted(BaseConnector.merged_filters(task, ind).items())),
                "observations": int(len(sub)),
                "numeric_observations": int(sub["value"].notna().sum()) if len(sub) else 0,
                "countries_covered": int(sub["country_code"].nunique()) if len(sub) else 0,
                "years_covered": f"{years[0]}-{years[-1]}" if years else "",
                "dimensions": ", ".join(sorted({d.split('=')[0] for s in sub["dimensions"] if s for d in s.split('; ')})) if len(sub) else "",
                "processing_applied": processing,
                "documentation": (pm.documentation_url if pm and pm.documentation_url else source.documentation_url),
            })
    return {"variables": pd.DataFrame(rows), "columns": columns}


def manifest(
    *,
    config: ExtractionConfig,
    config_path: str,
    config_sha256: str,
    plan: Optional[ExtractionPlan],
    started_at: str,
    finished_at: str,
    status: str,
    raw_records: List[Dict[str, Any]],
    rows: int,
    rows_by_indicator: Dict[str, int],
    issues: List[QualityIssue],
    steps: List[Dict[str, Any]],
    files: List[Dict[str, Any]],
    source_metadata: Dict[str, Any],
    indicator_metadata: Dict[str, Dict[str, Any]],
    task_errors: List[Dict[str, Any]],
) -> Dict[str, Any]:
    endpoints = sorted({r["url"].split("?")[0] for r in raw_records})
    return {
        "project": config.project.name,
        "description": config.project.description,
        "status": status,
        "execution": {"started_at": started_at, "finished_at": finished_at},
        "configuration": {
            "file": config_path,
            "version": config.project.version,
            "schema_version": config.schema_version,
            "sha256": config_sha256,
            "extraction_plan": config.project.extraction_plan,
            "plan_approval": plan.approval.model_dump(mode="json") if plan else None,
        },
        "environment": {
            "application": APP_NAME,
            "application_version": __version__,
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "sources": [
            {
                "id": s.id,
                "provider": s.provider,
                "api_name": s.api_name,
                "connector": s.connector,
                "base_url": s.base_url,
                "api_version": s.api_version,
                "documentation_url": s.documentation_url,
                "source_metadata": source_metadata.get(s.id, {}),
            }
            for s in config.sources
        ],
        "parameters": [t.model_dump(mode="json") for t in config.extractions],
        "endpoints_accessed": endpoints,
        "requests": {
            "total": len(raw_records),
            "failed": sum(1 for r in raw_records if r.get("error")),
            "list": [
                {k: r[k] for k in ("request_id", "purpose", "url", "params", "status_code", "attempts", "retrieved_at", "error")}
                for r in raw_records
            ],
        },
        "records": {"dataset_rows": rows, "by_indicator": rows_by_indicator},
        "indicator_metadata": indicator_metadata,
        "errors": [i.to_dict() for i in issues if i.level == "error"],
        "warnings": [i.to_dict() for i in issues if i.level == "warning"],
        "task_errors": task_errors,
        "processing_steps": steps,
        "output_files": files,
    }


def readme(
    config: ExtractionConfig,
    manifest_data: Dict[str, Any],
    issues: List[QualityIssue],
    dictionary: pd.DataFrame,
    plan: Optional[ExtractionPlan] = None,
) -> str:
    lines = [
        f"# {config.project.name} – extraction output",
        "",
        config.project.description,
        "",
        f"* **Status:** {manifest_data['status']}",
        f"* **Executed:** {manifest_data['execution']['started_at']} → {manifest_data['execution']['finished_at']} (UTC)",
        f"* **Configuration version:** {config.project.version}",
        f"* **Application:** {APP_NAME} {__version__}, Python {manifest_data['environment']['python_version']}",
        f"* **Requests:** {manifest_data['requests']['total']} ({manifest_data['requests']['failed']} failed)",
        f"* **Observations in dataset:** {manifest_data['records']['dataset_rows']}",
        "",
        "## Sources",
        "",
    ]
    for s in config.sources:
        lines.append(f"* **{s.provider}** – {s.api_name} (`{s.base_url}`), documentation: {s.documentation_url}")
    lines += ["", "## Variables", "", "| Code | Name | Observations | Countries | Years |", "|---|---|---|---|---|"]
    for _, r in dictionary.iterrows():
        name = str(r["official_name"] or "").replace("|", "/")
        lines.append(f"| `{r['indicator_code']}` | {name} | {r['observations']} | {r['countries_covered']} | {r['years_covered']} |")
    lines += ["", "## Files", "", "| File | Content |", "|---|---|"]
    descriptions = {
        "dataset.csv": "Processed observations, long format (UTF-8 with BOM for Excel)",
        "dataset.xlsx": "Same data in Excel, plus wide panel (if requested) and quality checks",
        "dataset.json": "Processed observations as JSON records",
        "dataset_wide.csv": "Country-period panel, one column per indicator/dimension",
        "raw_data.json": "Every HTTP response exactly as received (secrets redacted)",
        "data_dictionary.xlsx": "Definitions, sources, units, coverage and processing of every variable",
        "extraction_log.txt": "Detailed execution log",
        "extraction_manifest.json": "Machine-readable record of the execution (parameters, requests, checksums...)",
        "extraction_config.yaml": "Exact copy of the configuration that was executed",
        "extraction_plan.json": "Copy of the extraction plan linked to the configuration",
        "README.md": "This file",
    }
    for f in manifest_data["output_files"]:
        lines.append(f"| `{f['file']}` | {descriptions.get(f['file'], '')} |")
    lines += ["| `README.md` | This file |", "", "## Processing applied", ""]
    for s in manifest_data["processing_steps"]:
        lines.append(f"1. **{s['step']}** – {s['description']} ({s['rows_before']} → {s['rows_after']} rows)")
    lines += ["", "## Quality checks", ""]
    for level in ("error", "warning", "info"):
        for i in issues:
            if i.level == level:
                lines.append(f"* **{level.upper()}** [{i.check}] {i.message}")
    excluded = plan.excluded_rows() if plan else []
    if excluded:
        lines += [
            "",
            "## Not included in this dataset",
            "",
            "Items of the researcher's request that this extraction deliberately leaves out "
            "(from the Requirements Register in `extraction_plan.json`):",
            "",
            "| Row | Researcher's wording | Status | Reason |",
            "|---|---|---|---|",
        ]
        for r in excluded:
            wording = r.user_wording.replace("|", "/")
            reason = (r.reason or "").replace("|", "/")
            lines.append(f"| {r.id} | {wording} | {r.status.replace('_', ' ')} | {reason} |")
    lines += [
        "",
        "## Notes",
        "",
        "* `value` is populated only when the source provides a number. Categorical policy measures "
        "(e.g. MPOWER scores) are kept as text in `value_text` and were **not** converted to numeric scores.",
        "* Missing observations were not interpolated.",
        "* Every row can be traced to its HTTP response through `request_id` (see `raw_data.json`).",
        "",
    ]
    return "\n".join(lines)
