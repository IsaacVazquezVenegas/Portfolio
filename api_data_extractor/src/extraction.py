"""Extraction workflow (specification §6.3).

1. Load configuration            7. Extract observations
2. Validate configuration        8. Apply explicitly specified transformations
3. Initialise connectors         9. Perform data-quality checks
4. Establish API connections    10. Export outputs
5. Retrieve & preserve raw data 11. Generate documentation
6. Check response status        12. Record execution status
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import requests

from . import APP_NAME
from .api_client import ApiClient, ApiError, RawStore, utc_now_iso
from .connectors import get_connector_class
from .connectors.base import ConnectorError
from .export import describe_files, sha256_file, write_csv, write_json, write_workbook
from .logging_config import add_file_handler, get_logger, remove_handler
from .metadata import data_dictionary, manifest, readme
from .quality import QualityIssue, run_quality_checks
from .transformation import TransformResult, dataframe_records, transform
from .validation import ValidationReport, validate_config_file

logger = logging.getLogger(APP_NAME)

STATUS_SUCCESS = "success"
STATUS_WARNINGS = "completed_with_warnings"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"


class OutputExistsError(Exception):
    pass


class ExtractionAborted(Exception):
    pass


@dataclass
class ExtractionResult:
    status: str
    output_dir: Path
    rows: int
    issues: List[QualityIssue] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    manifest: Dict[str, Any] = field(default_factory=dict)


def resolve_output_dir(report: ValidationReport, now: datetime, overwrite: Optional[bool]) -> Path:
    out = report.config.output
    name = out.folder_name
    for key, value in {
        "project": report.config.project.name,
        "date": now.strftime("%Y%m%d"),
        "year": now.strftime("%Y"),
        "timestamp": now.strftime("%Y%m%d_%H%M%S"),
    }.items():
        name = name.replace("{" + key + "}", value)
    folder = (report.config_path.parent / out.directory / name).resolve()
    allow = out.overwrite if overwrite is None else overwrite
    if folder.exists() and any(folder.iterdir()):
        if not allow:
            raise OutputExistsError(
                f"output folder already exists and is not empty: {folder} "
                "(set output.overwrite: true or pass --overwrite)"
            )
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def run_extraction(
    config_path: Union[str, Path],
    *,
    session_factory: Optional[Callable[[], requests.Session]] = None,
    sleep: Optional[Callable[[float], None]] = None,
    overwrite: Optional[bool] = None,
    require_plan_approval: bool = True,
    now: Optional[datetime] = None,
    environ=None,
) -> ExtractionResult:
    get_logger()
    # 1-2. Load and validate -----------------------------------------------------
    report = validate_config_file(config_path, require_plan_approval=require_plan_approval, environ=environ)
    config = report.config
    now = now or datetime.now(timezone.utc)
    started_at = now.isoformat(timespec="seconds")
    output_dir = resolve_output_dir(report, now, overwrite)
    log_handler = add_file_handler(output_dir / "extraction_log.txt")
    try:
        return _execute(report, output_dir, started_at, session_factory, sleep, environ, log_handler)
    finally:
        remove_handler(log_handler)


def _execute(report, output_dir, started_at, session_factory, sleep, environ, log_handler) -> ExtractionResult:
    config = report.config
    logger.info("Project %s (configuration version %s)", config.project.name, config.project.version)
    logger.info("Configuration: %s", report.config_path)
    logger.info("Output folder: %s", output_dir)
    for w in report.warnings:
        logger.warning("validation: %s", w)

    # 3-4. Connectors --------------------------------------------------------------
    raw_store = RawStore()
    connectors = {}
    for source in config.sources:
        client_kwargs = {"environ": environ}
        if session_factory:
            client_kwargs["session"] = session_factory()
        if sleep:
            client_kwargs["sleep"] = sleep
        client = ApiClient(source.id, source.request, source.authentication, raw_store, **client_kwargs)
        connectors[source.id] = get_connector_class(source.connector)(source, client)
        logger.info("Source %s: connector '%s', %s", source.id, source.connector, source.base_url)

    # 5-7. Retrieval ---------------------------------------------------------------
    observations: List[Dict[str, Any]] = []
    indicator_metadata: Dict[str, Dict[str, Any]] = {}
    task_errors: List[Dict[str, Any]] = []
    aborted = False
    for task in config.extractions:
        if aborted:
            break
        source = config.source(task.source)
        connector = connectors[source.id]
        logger.info("Task %s: %d indicator(s), countries=%s, years %d-%d", task.id, len(task.indicators),
                    "all" if task.countries == "all" else len(task.countries), task.start_year, task.end_year)
        for ind in task.indicators:
            key = f"{source.id}:{ind.code}"
            if key not in indicator_metadata and config.validation.check_indicator_definitions:
                meta = connector.fetch_indicator_metadata(ind)
                if meta is not None:
                    indicator_metadata[key] = meta
            try:
                obs = connector.fetch_observations(task, ind)
            except (ApiError, ConnectorError) as exc:
                msg = str(exc)
                logger.error("Task %s / %s failed: %s", task.id, ind.code, msg)
                task_errors.append({"task_id": task.id, "source_id": source.id, "indicator_code": ind.code, "error": msg})
                if config.validation.fail_on_request_error:
                    logger.error("validation.fail_on_request_error is set: aborting the extraction")
                    aborted = True
                    break
                continue
            meta = indicator_metadata.get(key, {})
            for o in obs:
                o["source_id"] = source.id
                o["provider"] = source.provider
                o["task_id"] = task.id
                o["indicator_code"] = o.get("indicator_code") or ind.code
                o["indicator_name"] = o.get("indicator_name") or meta.get("name") or ind.name
                o["unit"] = o.get("unit") or ind.unit or meta.get("unit")
                o["category"] = ind.category
            logger.info("Task %s / %s: %d observation(s)", task.id, ind.code, len(obs))
            observations.extend(obs)

    retrieved = {r.request_id: r.retrieved_at for r in raw_store.records}
    for o in observations:
        o["retrieved_at"] = retrieved.get(o.get("request_id"))

    # Raw data is written first, before any processing.
    raw_records = [r.to_dict() for r in raw_store.records]
    write_json(output_dir / "raw_data.json", raw_records)
    logger.info("Raw responses preserved: %d request(s) -> raw_data.json", len(raw_records))

    # 8. Processing ------------------------------------------------------------------
    result: TransformResult = transform(observations, config.processing)
    for w in result.warnings:
        logger.warning("processing: %s", w)
    df = result.dataset

    # 9. Quality checks ----------------------------------------------------------------
    failed = [r for r in raw_records if r.get("error")]
    issues = [QualityIssue("warning", "configuration", w) for w in report.warnings]
    issues += [QualityIssue("warning", "processing", w) for w in result.warnings]
    issues += run_quality_checks(df, config, failed_requests=failed, indicator_metadata=indicator_metadata,
                                 task_errors=task_errors)
    for i in issues:
        log = {"error": logger.error, "warning": logger.warning}.get(i.level, logger.info)
        log("quality [%s] %s", i.check, i.message)

    # 10. Export ---------------------------------------------------------------------------
    files: List[str] = ["raw_data.json"]
    out = config.output
    issues_df = _issues_frame(issues)
    if "csv" in out.formats:
        write_csv(output_dir / "dataset.csv", df)
        files.append("dataset.csv")
        if result.wide_panel is not None:
            write_csv(output_dir / "dataset_wide.csv", result.wide_panel)
            files.append("dataset_wide.csv")
    if "xlsx" in out.formats:
        sheets = {"data": df}
        if result.wide_panel is not None:
            sheets["wide_panel"] = result.wide_panel
        sheets["quality_checks"] = issues_df
        write_workbook(output_dir / "dataset.xlsx", sheets)
        files.append("dataset.xlsx")
    if "json" in out.formats:
        write_json(output_dir / "dataset.json", dataframe_records(df))
        files.append("dataset.json")

    # 11. Documentation -------------------------------------------------------------------
    dictionary = data_dictionary(config, df, indicator_metadata, result.steps, report.plan)
    if out.data_dictionary:
        write_workbook(output_dir / "data_dictionary.xlsx", {"variables": dictionary["variables"], "columns": dictionary["columns"]})
        files.append("data_dictionary.xlsx")
    shutil.copyfile(report.config_path, output_dir / "extraction_config.yaml")
    files.append("extraction_config.yaml")
    if report.plan_path and report.plan_path.is_file():
        shutil.copyfile(report.plan_path, output_dir / "extraction_plan.json")
        files.append("extraction_plan.json")

    # 12. Status ---------------------------------------------------------------------------
    status = _status(df, issues, task_errors, aborted)
    logger.info("Extraction status: %s (%d rows)", status, len(df))
    finished_at = utc_now_iso()
    log_handler.flush()
    remove_handler(log_handler)  # close the log so that its checksum is final
    files.append("extraction_log.txt")

    rows_by_indicator = (
        df.groupby(["task_id", "indicator_code"]).size().rename("n").reset_index()
        .apply(lambda r: (f"{r['task_id']}/{r['indicator_code']}", int(r["n"])), axis=1).tolist()
        if len(df) else []
    )
    manifest_data = manifest(
        config=config,
        config_path=str(report.config_path),
        config_sha256=sha256_file(report.config_path),
        plan=report.plan,
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        raw_records=raw_records,
        rows=len(df),
        rows_by_indicator=dict(rows_by_indicator),
        issues=issues,
        steps=result.steps,
        files=describe_files(output_dir, files),
        source_metadata={sid: c.source_metadata() for sid, c in connectors.items()},
        indicator_metadata=indicator_metadata,
        task_errors=task_errors,
    )
    write_json(output_dir / "extraction_manifest.json", manifest_data)
    files.append("extraction_manifest.json")
    if out.readme:
        manifest_for_readme = dict(manifest_data, output_files=manifest_data["output_files"] + [{"file": "extraction_manifest.json"}])
        (output_dir / "README.md").write_text(readme(config, manifest_for_readme, issues, dictionary["variables"], report.plan), encoding="utf-8")
        files.append("README.md")

    return ExtractionResult(status=status, output_dir=output_dir, rows=len(df), issues=issues, files=files,
                            manifest=manifest_data)


def _status(df, issues, task_errors, aborted) -> str:
    if aborted or (len(df) == 0 and (task_errors or any(i.level == "error" for i in issues))):
        return STATUS_FAILED
    if task_errors or any(i.level == "error" for i in issues):
        return STATUS_PARTIAL
    if any(i.level == "warning" for i in issues):
        return STATUS_WARNINGS
    return STATUS_SUCCESS


def _issues_frame(issues: List[QualityIssue]):
    import pandas as pd

    return pd.DataFrame(
        [{"level": i.level, "check": i.check, "message": i.message,
          "details": json.dumps(i.details, ensure_ascii=False, default=str) if i.details else ""} for i in issues],
        columns=["level", "check", "message", "details"],
    )
