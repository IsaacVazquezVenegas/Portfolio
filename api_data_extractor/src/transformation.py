"""Processing of observations into analysis-ready datasets.

Only transformations explicitly enabled in the ``processing`` section are
applied, each one recorded as a step (with row counts) for the manifest.
Raw responses are never touched: processing works on parsed copies.

Deliberately *not* done: interpolation of missing observations, conversion
of categorical policy measures to numeric scores, merging of different
indicators into one value.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .models import Processing

REFERENCE_FILE = Path(__file__).parent / "reference" / "countries.csv"

#: Non-ISO codes used by specific providers.
COUNTRY_ALIASES = {"EL": "GRC", "UK": "GBR", "XK": "XKX"}

DATASET_COLUMNS = [
    "source_id",
    "provider",
    "task_id",
    "indicator_code",
    "indicator_name",
    "category",
    "country_code",
    "country_code_source",
    "country_name",
    "spatial_type",
    "year",
    "period",
    "dimensions",
    "dimension_labels",
    "value",
    "value_text",
    "low",
    "high",
    "unit",
    "comments",
    "missing",
    "duplicate_conflict",
    "source_record_id",
    "source_updated",
    "request_id",
    "source_url",
    "retrieved_at",
]

IDENTITY_COLUMNS = ["source_id", "indicator_code", "country_code", "period", "dimensions"]

_PLAIN_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_QUARTER = re.compile(r"^(\d{4})[-_ ]?Q([1-4])$", re.IGNORECASE)
_MONTH = re.compile(r"^(\d{4})[-_ M]?(0[1-9]|1[0-2])$")
_YEAR = re.compile(r"^(\d{4})$")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


@dataclass
class CountryReference:
    iso3: Dict[str, Dict[str, str]]
    iso2_to_iso3: Dict[str, str]

    def harmonise(self, code: Optional[str]) -> Tuple[Optional[str], bool]:
        """Return (iso3 code, recognised?)."""
        if code is None:
            return None, False
        c = str(code).strip().upper()
        if c in self.iso3:
            return c, True
        if c in COUNTRY_ALIASES:
            return COUNTRY_ALIASES[c], True
        if c in self.iso2_to_iso3:
            return self.iso2_to_iso3[c], True
        return c, False

    def name(self, iso3: Optional[str]) -> Optional[str]:
        return self.iso3.get(iso3 or "", {}).get("name")

    def is_aggregate(self, iso3: Optional[str]) -> bool:
        return self.iso3.get(iso3 or "", {}).get("is_aggregate") == "true"


@lru_cache(maxsize=1)
def load_country_reference() -> CountryReference:
    iso3: Dict[str, Dict[str, str]] = {}
    iso2: Dict[str, str] = {}
    with REFERENCE_FILE.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            iso3[row["iso3"]] = row
            if row["iso2"] and row["is_aggregate"] == "false":
                iso2.setdefault(row["iso2"], row["iso3"])
    return CountryReference(iso3=iso3, iso2_to_iso3=iso2)


def standardise_period(period: Optional[str]) -> Tuple[Optional[str], Optional[int]]:
    """Normalise to 'YYYY', 'YYYY-Qn', 'YYYY-MM' or 'YYYY-MM-DD'; return (period, year)."""
    if period is None:
        return None, None
    p = str(period).strip()
    m = _YEAR.match(p)
    if m:
        return p, int(p)
    m = _QUARTER.match(p)
    if m:
        return f"{m.group(1)}-Q{m.group(2)}", int(m.group(1))
    m = _MONTH.match(p)
    if m:
        return f"{m.group(1)}-{m.group(2)}", int(m.group(1))
    m = _DATE.match(p)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", int(m.group(1))
    head = p[:4]
    return p, int(head) if head.isdigit() else None


def format_dimensions(dims: Optional[Dict[str, Any]]) -> str:
    if not dims:
        return ""
    return "; ".join(f"{k}={dims[k]}" for k in sorted(dims))


@dataclass
class TransformResult:
    dataset: pd.DataFrame
    wide_panel: Optional[pd.DataFrame]
    steps: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _step(steps, name, description, before, after, **details):
    steps.append({"step": name, "description": description, "rows_before": before, "rows_after": after, **details})


def to_dataframe(observations: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for o in observations:
        row = {c: o.get(c) for c in DATASET_COLUMNS}
        row["dimensions"] = format_dimensions(o.get("dimensions"))
        labels = {k: v for k, v in (o.get("dimension_labels") or {}).items() if v}
        row["dimension_labels"] = format_dimensions(labels)
        row["country_code_source"] = o.get("country_code")
        rows.append(row)
    df = pd.DataFrame(rows, columns=DATASET_COLUMNS)
    df["value"] = pd.to_numeric(df["value"], errors="coerce").astype("float64")
    for col in ("low", "high"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df = df.astype({c: "object" for c in DATASET_COLUMNS if c not in ("value", "low", "high")})
    return df


def transform(observations: List[Dict[str, Any]], processing: Processing) -> TransformResult:
    steps: List[Dict[str, Any]] = []
    warnings: List[str] = []
    df = to_dataframe(observations)
    n = len(df)
    _step(steps, "parse", "Observations parsed from raw API responses into the standard long format", n, n)

    # 1. Period standardisation ----------------------------------------------
    if processing.standardise_periods:
        parsed = [standardise_period(p) for p in df["period"]]
        changed = sum(1 for (new, _), old in zip(parsed, df["period"]) if new != old)
        df["period"] = [p for p, _ in parsed]
        df["year"] = pd.array([y for _, y in parsed], dtype="Int64")
        _step(steps, "standardise_periods", "Periods normalised to YYYY / YYYY-Qn / YYYY-MM; 'year' derived",
              n, n, values_changed=changed)
    else:
        df["year"] = pd.array([standardise_period(p)[1] for p in df["period"]], dtype="Int64")

    # 2. Country identifiers -----------------------------------------------------
    ref = load_country_reference()
    if processing.country_codes == "iso3":
        results = [ref.harmonise(c) for c in df["country_code_source"]]
        df["country_code"] = [r[0] for r in results]
        changed = int(sum(1 for (new, _), old in zip(results, df["country_code_source"]) if new != old))
        unknown = sorted({str(c) for (c, ok) in results if not ok and c is not None})
        _step(steps, "harmonise_country_codes", "Country identifiers mapped to ISO 3166-1 alpha-3 (reference: src/reference/countries.csv)",
              n, n, values_changed=changed, unrecognised_codes=unknown)
        if unknown:
            warnings.append(f"country codes not found in the reference table and left unchanged: {unknown}")
    filled = 0
    names = []
    for code, name in zip(df["country_code"], df["country_name"]):
        if (name is None or (isinstance(name, float) and pd.isna(name))) and ref.name(code):
            names.append(ref.name(code))
            filled += 1
        else:
            names.append(name)
    df["country_name"] = names
    if filled:
        _step(steps, "fill_country_names", "Missing country names filled from the reference table", n, n, values_filled=filled)

    # 3. Numeric conversion ------------------------------------------------------
    if processing.numeric_values == "parse_plain_numbers":
        converted = 0
        values = df["value"].tolist()
        for i, (v, t) in enumerate(zip(values, df["value_text"])):
            if pd.isna(v) and isinstance(t, str) and _PLAIN_NUMBER.match(t.strip()):
                values[i] = float(t)
                converted += 1
        df["value"] = pd.Series(values, index=df.index, dtype="float64")
        _step(steps, "parse_plain_numbers", "Text values that are plain numbers converted to numeric 'value' (categories untouched)",
              n, n, values_converted=converted)

    # 4. Missing values ----------------------------------------------------------
    markers = {m.strip().lower() for m in processing.missing_values.markers}

    def is_missing(v, t) -> bool:
        if not pd.isna(v):
            return False
        if t is None or (isinstance(t, float) and pd.isna(t)):
            return True
        text = str(t).strip()
        return text == "" or text.lower() in markers

    df["missing"] = [is_missing(v, t) for v, t in zip(df["value"], df["value_text"])]
    n_missing = int(df["missing"].sum())
    if processing.missing_values.treatment == "drop" and n_missing:
        before = len(df)
        df = df[~df["missing"]].copy()
        _step(steps, "drop_missing", "Observations without any value removed (missing_values.treatment = drop)",
              before, len(df), removed=before - len(df), markers=sorted(markers))
    else:
        _step(steps, "flag_missing", "Observations without any value kept and flagged (missing = True)",
              len(df), len(df), flagged=n_missing, markers=sorted(markers))

    # 5. Duplicates --------------------------------------------------------------
    df["duplicate_conflict"] = False
    if processing.deduplicate and len(df):
        before = len(df)
        content_cols = IDENTITY_COLUMNS + ["value", "value_text", "low", "high", "unit"]
        df = df.drop_duplicates(subset=content_cols, keep="first").copy()
        removed = before - len(df)
        _step(steps, "remove_exact_duplicates", "Exact duplicate observations (same identity and same values) removed",
              before, len(df), removed=removed)
        if removed:
            warnings.append(f"{removed} exact duplicate observation(s) removed")
    if len(df):
        conflict = df.duplicated(subset=IDENTITY_COLUMNS, keep=False)
        df["duplicate_conflict"] = conflict.values
        if conflict.any():
            warnings.append(
                f"{int(conflict.sum())} observation(s) share the same identity but have different values; "
                "kept and flagged as duplicate_conflict"
            )

    # 6. Order -------------------------------------------------------------------
    df = df.sort_values(["source_id", "indicator_code", "country_code", "period", "dimensions"],
                        kind="stable", na_position="last").reset_index(drop=True)
    df = df[DATASET_COLUMNS]

    wide = None
    if processing.wide_panel:
        wide = build_wide_panel(df)
        _step(steps, "wide_panel", "Country-year panel built (one column per indicator and dimension combination)",
              len(df), len(wide), columns=len(wide.columns))
    return TransformResult(dataset=df, wide_panel=wide, steps=steps, warnings=warnings)


def wide_column_name(row) -> str:
    name = f"{row['source_id']}:{row['indicator_code']}"
    if row["dimensions"]:
        name += f" [{row['dimensions']}]"
    return name


def build_wide_panel(df: pd.DataFrame) -> pd.DataFrame:
    """One row per country-period; numeric indicators use 'value', others keep 'value_text'."""
    if df.empty:
        return pd.DataFrame(columns=["country_code", "country_name", "period", "year"])
    data = df[~df["duplicate_conflict"].astype(bool)].copy()
    data["column"] = data.apply(wide_column_name, axis=1)
    numeric_cols = set(data.groupby("column")["value"].apply(lambda s: s.notna().any()).loc[lambda s: s].index)
    data["cell"] = [v if c in numeric_cols else t for c, v, t in zip(data["column"], data["value"], data["value_text"])]
    data["year"] = data["year"].astype("object")
    data["country_name"] = data["country_name"].astype("object")
    wide = data.pivot_table(index=["country_code", "period"], columns="column", values="cell", aggfunc="first")
    wide = wide.reset_index()
    wide.columns.name = None
    meta = data.drop_duplicates(["country_code", "period"])[["country_code", "period", "country_name", "year"]]
    wide = meta.merge(wide, on=["country_code", "period"], how="right")
    cols = ["country_code", "country_name", "period", "year"] + sorted(c for c in wide.columns if c not in
                                                                      ("country_code", "country_name", "period", "year"))
    return wide[cols].sort_values(["country_code", "period"]).reset_index(drop=True)


#: Dimensions that qualify a series rather than name it: listed after the others in a label.
QUALIFYING_DIMENSIONS = ("SEX", "AGEGROUP", "RESIDENCEAREATYPE", "WEALTHQUINTILE", "EDUCATIONLEVEL")


def dimension_label_parts(dimension_labels: Optional[str]) -> List[str]:
    """'SEX=Both sexes; TOBACCO_INDICATOR=Ban on ...' -> ['Ban on ...', 'Both sexes'] (qualifiers last)."""
    if not dimension_labels or (isinstance(dimension_labels, float) and pd.isna(dimension_labels)):
        return []
    pairs = []
    for part in str(dimension_labels).split("; "):
        key, _, label = part.partition("=")
        if label:
            pairs.append((key in QUALIFYING_DIMENSIONS, key, label))
    return [label for _, _, label in sorted(pairs, key=lambda p: (p[0], QUALIFYING_DIMENSIONS.index(p[1]) if p[0] else 0))]


def series_label(indicator_label: str, dimension_labels: Optional[str] = None) -> str:
    """Readable name of a series: the indicator's short label, then its dimension labels."""
    return " · ".join([indicator_label] + dimension_label_parts(dimension_labels))


def labelled_wide_panel(wide: pd.DataFrame, df: pd.DataFrame, labels: Dict[Tuple[str, str], str]) -> pd.DataFrame:
    """The wide panel with readable column names; its first row holds the original column codes.

    ``labels`` maps (source_id, indicator_code) to the indicator's short label (configuration
    ``label``, else its name). Labels are made unique by appending the code when two collide.
    """
    if wide.empty:
        return wide
    keys = df.drop_duplicates(["source_id", "indicator_code", "dimensions"])
    readable: Dict[str, str] = {}
    for _, row in keys.iterrows():
        column = wide_column_name(row)
        base = labels.get((row["source_id"], row["indicator_code"])) or row.get("indicator_name") or row["indicator_code"]
        readable[column] = series_label(str(base), row.get("dimension_labels"))
    fixed = {"country_code": "Country code", "country_name": "Country", "period": "Period", "year": "Year"}
    names, seen = [], {}
    for col in wide.columns:
        name = fixed.get(col) or readable.get(col) or col
        seen[name] = seen.get(name, 0) + 1
        names.append(name)
    names = [f"{n} ({c})" if seen[n] > 1 and c not in fixed else n for n, c in zip(names, wide.columns)]
    codes = pd.DataFrame([list(wide.columns)], columns=names)
    out = wide.copy()
    out.columns = names
    return pd.concat([codes, out.astype(object)], ignore_index=True)


def dataframe_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """JSON-safe records (NaN -> None)."""
    return json.loads(df.to_json(orient="records", force_ascii=False))
