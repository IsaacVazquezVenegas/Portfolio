"""Data-quality checks.

Checks never discard data: they produce issues (``info``, ``warning`` or
``error``) that are written to the log, the manifest, the Excel workbook
and the README of the output package.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from .models import ExtractionConfig
from .transformation import load_country_reference


@dataclass
class QualityIssue:
    level: str  # info | warning | error
    check: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _norm(text: Optional[str]) -> str:
    return " ".join(str(text or "").lower().replace("(", " ").replace(")", " ").split())


def expected_countries(config: ExtractionConfig, task) -> Optional[List[str]]:
    ref = load_country_reference()
    raw = config.validation.expected_countries
    if raw is None:
        if task.countries == "all":
            return None
        raw = task.countries
    if config.processing.country_codes == "iso3":
        return sorted({ref.harmonise(c)[0] for c in raw})
    return sorted(set(raw))


def run_quality_checks(
    df: pd.DataFrame,
    config: ExtractionConfig,
    *,
    failed_requests: List[Dict[str, Any]],
    indicator_metadata: Dict[str, Dict[str, Any]],
    task_errors: List[Dict[str, Any]],
) -> List[QualityIssue]:
    v = config.validation
    issues: List[QualityIssue] = []
    ref = load_country_reference()

    # Records retrieved ---------------------------------------------------------
    issues.append(QualityIssue("info", "records", f"{len(df)} observation(s) in the processed dataset"))
    if len(df) < v.min_records:
        issues.append(QualityIssue("error", "records", f"fewer observations than validation.min_records ({v.min_records})"))

    # Failed requests / tasks ----------------------------------------------------
    for f in failed_requests:
        level = "error" if str(f.get("purpose", "")).startswith("data") else "warning"
        issues.append(QualityIssue(level, "failed_request", f"{f['request_id']} {f['url']}: {f['error']}",
                                   {k: f.get(k) for k in ("request_id", "purpose", "url", "status_code", "error")}))
    for t in task_errors:
        issues.append(QualityIssue("error", "incomplete_extraction",
                                   f"{t['task_id']} / {t['indicator_code']}: {t['error']}", t))

    for task in config.extractions:
        tdf = df[(df["task_id"] == task.id)] if len(df) else df
        exp_ind = v.expected_indicators or [i.code for i in task.indicators]
        exp_ind = [c for c in exp_ind if c in {i.code for i in task.indicators}]
        exp_countries = expected_countries(config, task)
        start = v.expected_start_year or task.start_year
        end = v.expected_end_year or task.end_year

        for code in exp_ind:
            idf = tdf[tdf["indicator_code"] == code] if len(tdf) else tdf
            label = f"{task.id}/{code}"
            if idf.empty:
                issues.append(QualityIssue("warning", "expected_variables", f"{label}: no observations retrieved"))
                continue
            years = sorted({int(y) for y in idf["year"].dropna()})
            countries = sorted({str(c) for c in idf["country_code"].dropna()})
            issues.append(QualityIssue(
                "info", "coverage",
                f"{label}: {len(idf)} observations, {len(countries)} countries, years "
                f"{years[0] if years else '-'}-{years[-1] if years else '-'}",
                {"observations": len(idf), "countries": len(countries), "years": years},
            ))

            if v.check_coverage and exp_countries:
                absent = sorted(set(exp_countries) - set(countries))
                if absent:
                    issues.append(QualityIssue("warning", "geographic_coverage",
                                               f"{label}: no observations for {len(absent)} expected country(ies)",
                                               {"countries": absent}))
            if v.check_coverage:
                absent_years = [y for y in range(start, end + 1) if y not in years]
                if absent_years and task.frequency != "any":
                    issues.append(QualityIssue("info", "time_coverage",
                                               f"{label}: no observations for {len(absent_years)} year(s) in {start}-{end}",
                                               {"years": absent_years}))

            if v.check_missing:
                n_missing = int(idf["missing"].sum())
                share = n_missing / len(idf)
                if n_missing:
                    level = "warning" if share > v.max_missing_share else "info"
                    issues.append(QualityIssue(level, "missing_values",
                                               f"{label}: {n_missing} observation(s) without a value ({share:.0%})",
                                               {"missing": n_missing, "share": round(share, 4)}))
                text_only = int((idf["value"].isna() & ~idf["missing"].astype(bool)).sum())
                if text_only:
                    issues.append(QualityIssue("info", "non_numeric_values",
                                               f"{label}: {text_only} observation(s) have a text/categorical value only "
                                               "(kept in value_text, not converted)"))

            if v.check_units:
                units = sorted({str(u) for u in idf["unit"].dropna()})
                if len(units) > 1:
                    issues.append(QualityIssue("warning", "units", f"{label}: several units reported {units}"))
                elif not units:
                    issues.append(QualityIssue("info", "units", f"{label}: no unit reported by source or configuration"))

            if v.check_indicator_definitions:
                spec = next(i for i in task.indicators if i.code == code)
                meta = indicator_metadata.get(f"{task.source}:{code}") or {}
                official = meta.get("name") or next(iter(idf["indicator_name"].dropna()), None)
                if meta.get("found") is False:
                    issues.append(QualityIssue("warning", "indicator_definition",
                                               f"{label}: indicator not found in the source metadata"))
                if spec.name and official and _norm(spec.name) != _norm(official):
                    issues.append(QualityIssue("warning", "indicator_definition",
                                               f"{label}: configured name '{spec.name}' differs from the source's "
                                               f"official name '{official}'"))
                if spec.unit and meta.get("unit") and _norm(spec.unit) != _norm(meta["unit"]):
                    issues.append(QualityIssue("warning", "indicator_definition",
                                               f"{label}: configured unit '{spec.unit}' differs from source unit '{meta['unit']}'"))

        if len(tdf):
            # Unexpected countries / years
            codes = sorted({str(c) for c in tdf["country_code"].dropna()})
            if exp_countries:
                unexpected = sorted(set(codes) - set(exp_countries))
                if unexpected:
                    issues.append(QualityIssue("warning", "unexpected_countries",
                                               f"{task.id}: observations for unrequested country codes", {"codes": unexpected}))
            if config.processing.country_codes == "iso3":
                aggregates = [c for c in codes if ref.is_aggregate(c)]
                if aggregates:
                    issues.append(QualityIssue("warning", "unexpected_countries",
                                               f"{task.id}: {len(aggregates)} regional/income aggregates present (not countries)",
                                               {"codes": aggregates}))
                unknown = [c for c in codes if c not in ref.iso3]
                if unknown:
                    issues.append(QualityIssue("warning", "unexpected_countries",
                                               f"{task.id}: country codes not in the reference table", {"codes": unknown}))
            years = tdf["year"].dropna().astype(int)
            out = sorted({int(y) for y in years if y < start or y > end})
            if out:
                issues.append(QualityIssue("warning", "unexpected_years", f"{task.id}: years outside {start}-{end}", {"years": out}))
            if tdf["year"].isna().any():
                issues.append(QualityIssue("warning", "unexpected_years",
                                           f"{task.id}: {int(tdf['year'].isna().sum())} observation(s) without a parsable year"))

    if v.check_duplicates and len(df):
        conflicts = int(df["duplicate_conflict"].astype(bool).sum())
        if conflicts:
            issues.append(QualityIssue("warning", "duplicates",
                                       f"{conflicts} observation(s) with the same identity but different values (flagged)"))
        else:
            issues.append(QualityIssue("info", "duplicates", "no conflicting duplicate observations"))
    return issues
