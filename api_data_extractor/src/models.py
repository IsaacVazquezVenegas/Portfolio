"""Formal data models for extraction configurations and extraction plans.

These Pydantic models are the single source of truth for both documents.
The JSON Schemas in ``schemas/`` are generated from them
(``python -m src.main schemas``) and a test guarantees they stay in sync.

All models forbid unknown fields, so a typo or an LLM-invented option is
rejected instead of being silently ignored.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Dict, List, Literal, Optional, Union
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"

IDENTIFIER = r"^[A-Za-z][A-Za-z0-9_\-]{0,63}$"
PROJECT_NAME = r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$"
ENV_VAR = r"^[A-Z_][A-Z0-9_]{0,127}$"
COUNTRY_CODE = re.compile(r"^[A-Z0-9_]{2,10}$")

#: Placeholders that may appear in endpoint / query-parameter templates.
#: Templates are filled by plain string substitution, never evaluated.
TEMPLATE_PLACEHOLDERS = {"indicator", "countries", "country", "start_year", "end_year"}
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")

#: Headers that must never be set in a configuration file (secrets belong in
#: environment variables, handled by the ``authentication`` block).
FORBIDDEN_HEADERS = {"authorization", "cookie", "proxy-authorization", "x-api-key", "api-key"}

FilterValue = Union[str, int, List[Union[str, int]]]


def template_placeholders(template: str) -> set:
    return set(_PLACEHOLDER_RE.findall(template))


def _check_template(value: Optional[str], field: str) -> Optional[str]:
    if value is None:
        return value
    unknown = template_placeholders(value) - TEMPLATE_PLACEHOLDERS
    if unknown:
        raise ValueError(
            f"{field} uses unsupported placeholder(s) {sorted(unknown)}; "
            f"allowed placeholders are {sorted(TEMPLATE_PLACEHOLDERS)}"
        )
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# A. Project identification
# ---------------------------------------------------------------------------


class ProjectInfo(StrictModel):
    name: str = Field(pattern=PROJECT_NAME, description="Short identifier, used in output folder names.")
    description: str = Field(min_length=1)
    version: str = Field(pattern=r"^\d+\.\d+(\.\d+)?$", description="Version of this configuration.")
    created: date
    extraction_plan: Optional[str] = Field(
        None, description="Path (relative to the configuration file) of the related extraction_plan.json."
    )
    authors: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# B. Data sources
# ---------------------------------------------------------------------------


class Authentication(StrictModel):
    method: Literal["none", "api_key_header", "api_key_query", "bearer_token"] = "none"
    env_var: Optional[str] = Field(
        None, pattern=ENV_VAR, description="Name of the environment variable that holds the secret (never the secret)."
    )
    param_name: Optional[str] = Field(
        None, description="Header name (api_key_header) or query parameter name (api_key_query)."
    )

    @model_validator(mode="after")
    def _check(self) -> "Authentication":
        if self.method != "none" and not self.env_var:
            raise ValueError(f"authentication method '{self.method}' requires 'env_var'")
        if self.method in ("api_key_header", "api_key_query") and not self.param_name:
            raise ValueError(f"authentication method '{self.method}' requires 'param_name'")
        if self.method == "none" and (self.env_var or self.param_name):
            raise ValueError("authentication method 'none' must not define env_var or param_name")
        return self


class RequestSettings(StrictModel):
    timeout_seconds: float = Field(60, gt=0, le=600)
    max_retries: int = Field(3, ge=0, le=10)
    backoff_seconds: float = Field(2.0, ge=0, le=120, description="Base delay; doubles after each retry.")
    min_interval_seconds: float = Field(0.5, ge=0, le=60, description="Minimum delay between two requests (rate limit).")
    headers: Dict[str, str] = Field(default_factory=dict, description="Non-secret request headers.")

    @field_validator("headers")
    @classmethod
    def _no_secret_headers(cls, v: Dict[str, str]) -> Dict[str, str]:
        bad = [h for h in v if h.lower() in FORBIDDEN_HEADERS]
        if bad:
            raise ValueError(
                f"header(s) {bad} may carry credentials and cannot be set in a configuration; "
                "use the 'authentication' block with an environment variable instead"
            )
        return v


class PaginationConfig(StrictModel):
    """Pagination for the generic REST connector (dedicated connectors handle their own)."""

    type: Literal["none", "page", "offset", "next_link"] = "none"
    page_param: str = "page"
    first_page: int = Field(1, ge=0)
    page_size_param: Optional[str] = None
    page_size: Optional[int] = Field(None, gt=0, le=100000)
    offset_param: str = "offset"
    limit_param: str = "limit"
    total_pages_path: Optional[str] = Field(None, description="Dotted path to the total page count in the response.")
    next_link_path: Optional[str] = Field(None, description="Dotted path to the next-page URL in the response.")
    max_pages: int = Field(1000, ge=1, le=100000)

    @model_validator(mode="after")
    def _check(self) -> "PaginationConfig":
        if self.type == "next_link" and not self.next_link_path:
            raise ValueError("pagination type 'next_link' requires 'next_link_path'")
        if self.type == "offset" and not self.page_size:
            raise ValueError("pagination type 'offset' requires 'page_size'")
        return self


STANDARD_FIELDS = {
    "indicator_code",
    "indicator_name",
    "country_code",
    "country_name",
    "period",
    "value",
    "value_text",
    "unit",
    "low",
    "high",
    "comments",
    "source_record_id",
}


class ResponseMapping(StrictModel):
    """How the generic REST connector finds observations in a JSON response."""

    records_path: str = Field("", description="Dotted path to the list of records; empty string = response root.")
    fields: Dict[str, str] = Field(description="Standard field name -> dotted path inside each record.")
    country_separator: str = Field(";", description="Separator used when {countries} is filled with several codes.")

    @field_validator("fields")
    @classmethod
    def _check_fields(cls, v: Dict[str, str]) -> Dict[str, str]:
        unknown = set(v) - STANDARD_FIELDS
        if unknown:
            raise ValueError(f"unknown standard field(s) {sorted(unknown)}; allowed: {sorted(STANDARD_FIELDS)}")
        missing = {"country_code", "period"} - set(v)
        if missing:
            raise ValueError(f"response.fields must map {sorted(missing)}")
        if "value" not in v and "value_text" not in v:
            raise ValueError("response.fields must map 'value' or 'value_text'")
        return v


class Source(StrictModel):
    id: str = Field(pattern=IDENTIFIER)
    provider: str
    api_name: str
    api_type: Literal["rest", "odata", "json-stat", "sdmx"]
    connector: str = Field(description="Identifier of an approved connector (see `python -m src.main connectors`).")
    base_url: str
    endpoint: Optional[str] = Field(
        None, description="Endpoint template relative to base_url (generic_rest only), e.g. 'series/{indicator}'."
    )
    documentation_url: str
    api_version: Optional[str] = None
    authentication: Authentication = Field(default_factory=Authentication)
    request: RequestSettings = Field(default_factory=RequestSettings)
    pagination: PaginationConfig = Field(default_factory=PaginationConfig)
    response: Optional[ResponseMapping] = None
    allow_insecure_http: bool = Field(False, description="Permit plain http:// (discouraged).")

    @field_validator("connector")
    @classmethod
    def _known_connector(cls, v: str) -> str:
        from .connectors import available_connectors

        if v not in available_connectors():
            raise ValueError(f"unknown connector '{v}'; available connectors: {sorted(available_connectors())}")
        return v

    @field_validator("endpoint")
    @classmethod
    def _endpoint_template(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and ("://" in v or v.startswith("//")):
            raise ValueError("endpoint must be relative to base_url, not an absolute URL")
        return _check_template(v, "endpoint")

    @model_validator(mode="after")
    def _check(self) -> "Source":
        for name in ("base_url", "documentation_url"):
            parsed = urlparse(getattr(self, name))
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError(f"{name} must be an absolute http(s) URL")
        if urlparse(self.base_url).scheme == "http" and not self.allow_insecure_http:
            raise ValueError("base_url must use https (set allow_insecure_http: true only if the API offers no HTTPS)")
        if self.connector == "generic_rest":
            if self.response is None:
                raise ValueError("the generic_rest connector requires a 'response' mapping")
            if self.endpoint is None:
                raise ValueError("the generic_rest connector requires an 'endpoint'")
        else:
            if self.response is not None:
                raise ValueError(f"'response' mapping is only used by generic_rest, not '{self.connector}'")
            if self.pagination.type != "none":
                raise ValueError(f"connector '{self.connector}' handles pagination itself; remove 'pagination'")
        return self


# ---------------------------------------------------------------------------
# C. Extraction parameters
# ---------------------------------------------------------------------------


class IndicatorSpec(StrictModel):
    code: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.\-]+$")
    name: Optional[str] = Field(None, description="Official indicator name, checked against source metadata.")
    definition: Optional[str] = None
    unit: Optional[str] = None
    category: Optional[str] = None
    measurement_scale: Optional[Literal["ratio", "interval", "ordinal", "nominal", "count", "text"]] = None
    filters: Dict[str, FilterValue] = Field(default_factory=dict, description="Indicator-specific dimension filters.")


class ExtractionTask(StrictModel):
    id: str = Field(pattern=IDENTIFIER)
    source: str = Field(description="id of an entry in 'sources'.")
    indicators: List[IndicatorSpec] = Field(min_length=1)
    countries: Union[Literal["all"], List[str]] = Field(
        description="'all' or a list of country codes in the source's own coding (e.g. ISO3 for WHO, ISO2 for Eurostat)."
    )
    start_year: int = Field(ge=1800, le=2100)
    end_year: int = Field(ge=1800, le=2100)
    frequency: Literal["annual", "quarterly", "monthly", "any"] = "annual"
    filters: Dict[str, FilterValue] = Field(default_factory=dict, description="Dimension filters applied to all indicators.")
    query_params: Dict[str, Union[str, int, float, bool]] = Field(
        default_factory=dict, description="Extra query parameters (templates allowed)."
    )
    country_batch_size: int = Field(50, ge=1, le=500, description="Countries per request.")

    @field_validator("countries")
    @classmethod
    def _countries(cls, v):
        if v == "all":
            return v
        if not v:
            raise ValueError("countries must be 'all' or a non-empty list")
        bad = [c for c in v if not COUNTRY_CODE.match(c)]
        if bad:
            raise ValueError(f"invalid country code(s) {bad}: use upper-case codes such as 'FRA' or 'FR'")
        dupes = sorted({c for c in v if v.count(c) > 1})
        if dupes:
            raise ValueError(f"duplicate country code(s) {dupes}")
        return v

    @field_validator("query_params")
    @classmethod
    def _query_templates(cls, v):
        for key, value in v.items():
            if isinstance(value, str):
                _check_template(value, f"query_params.{key}")
        return v

    @model_validator(mode="after")
    def _check(self) -> "ExtractionTask":
        if self.end_year < self.start_year:
            raise ValueError(f"end_year ({self.end_year}) is before start_year ({self.start_year})")
        codes = [i.code for i in self.indicators]
        dupes = sorted({c for c in codes if codes.count(c) > 1})
        if dupes:
            raise ValueError(f"duplicate indicator code(s) {dupes} in task '{self.id}'")
        return self


# ---------------------------------------------------------------------------
# D. Processing
# ---------------------------------------------------------------------------


class MissingValues(StrictModel):
    treatment: Literal["keep", "drop"] = Field(
        "keep", description="keep: retain rows and flag them (missing=True); drop: remove them (counted in the manifest)."
    )
    markers: List[str] = Field(default_factory=list, description="Text values that mean 'no observation', e.g. '..'.")


class Processing(StrictModel):
    preserve_raw: Literal[True] = Field(True, description="Raw responses are always preserved.")
    country_codes: Literal["iso3", "source"] = Field("iso3", description="Harmonise country identifiers to ISO 3166 alpha-3.")
    numeric_values: Literal["source_numeric_only", "parse_plain_numbers"] = Field(
        "source_numeric_only",
        description="source_numeric_only: only values the API marks as numeric populate 'value'. "
        "parse_plain_numbers: also convert text values that are plain numbers (never categories).",
    )
    standardise_periods: bool = True
    missing_values: MissingValues = Field(default_factory=MissingValues)
    deduplicate: bool = Field(True, description="Remove exact duplicate observations; conflicting duplicates are only flagged.")
    wide_panel: bool = Field(False, description="Also produce a country-year wide panel.")


# ---------------------------------------------------------------------------
# E. Validation
# ---------------------------------------------------------------------------


class ValidationRules(StrictModel):
    expected_indicators: Optional[List[str]] = Field(None, description="Defaults to every requested indicator.")
    expected_countries: Optional[List[str]] = Field(
        None, description="Defaults to the requested countries (when not 'all'). Compared after harmonisation."
    )
    expected_start_year: Optional[int] = None
    expected_end_year: Optional[int] = None
    check_duplicates: bool = True
    check_missing: bool = True
    check_units: bool = True
    check_indicator_definitions: bool = True
    check_coverage: bool = True
    min_records: int = Field(1, ge=0)
    max_missing_share: float = Field(0.5, ge=0, le=1, description="Warn when the share of missing values exceeds this.")
    fail_on_request_error: bool = Field(False, description="Abort at the first failed request instead of continuing.")


# ---------------------------------------------------------------------------
# F. Output
# ---------------------------------------------------------------------------


class OutputSettings(StrictModel):
    directory: str = Field("output", description="Relative to the configuration file.")
    folder_name: str = Field(
        "{project}_{timestamp}", description="Placeholders: {project}, {date}, {year}, {timestamp}."
    )
    overwrite: bool = False
    formats: List[Literal["csv", "xlsx", "json"]] = Field(default_factory=lambda: ["csv", "xlsx"])
    data_dictionary: bool = True
    readme: bool = True

    @field_validator("folder_name")
    @classmethod
    def _folder(cls, v: str) -> str:
        unknown = template_placeholders(v) - {"project", "date", "year", "timestamp"}
        if unknown:
            raise ValueError(f"folder_name uses unsupported placeholder(s) {sorted(unknown)}")
        if any(ch in v for ch in '/\\:*?"<>|') or ".." in v:
            raise ValueError("folder_name must be a plain folder name (no path separators)")
        return v

    @field_validator("formats")
    @classmethod
    def _formats(cls, v):
        if not v:
            raise ValueError("at least one dataset format is required")
        return list(dict.fromkeys(v))


# ---------------------------------------------------------------------------
# Root configuration
# ---------------------------------------------------------------------------


class ExtractionConfig(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    project: ProjectInfo
    sources: List[Source] = Field(min_length=1)
    extractions: List[ExtractionTask] = Field(min_length=1)
    processing: Processing = Field(default_factory=Processing)
    validation: ValidationRules = Field(default_factory=ValidationRules)
    output: OutputSettings = Field(default_factory=OutputSettings)

    @model_validator(mode="after")
    def _cross_checks(self) -> "ExtractionConfig":
        source_ids = [s.id for s in self.sources]
        dupes = sorted({s for s in source_ids if source_ids.count(s) > 1})
        if dupes:
            raise ValueError(f"duplicate source id(s) {dupes}")
        task_ids = [t.id for t in self.extractions]
        dupes = sorted({t for t in task_ids if task_ids.count(t) > 1})
        if dupes:
            raise ValueError(f"duplicate extraction id(s) {dupes}")
        for task in self.extractions:
            if task.source not in source_ids:
                raise ValueError(f"extraction '{task.id}' refers to unknown source '{task.source}'")
        v = self.validation
        if v.expected_start_year and v.expected_end_year and v.expected_end_year < v.expected_start_year:
            raise ValueError("validation.expected_end_year is before expected_start_year")
        return self

    def source(self, source_id: str) -> Source:
        return next(s for s in self.sources if s.id == source_id)


# ---------------------------------------------------------------------------
# Extraction plan (produced by the LLM, approved by the researcher)
# ---------------------------------------------------------------------------


class ResearchRequirements(StrictModel):
    objective: str
    subject: str
    variables: List[str] = Field(min_length=1)
    geographic_coverage: str
    time_period: str
    frequency: str
    aggregation_level: str
    output_formats: List[str] = Field(default_factory=list)
    analytical_applications: List[str] = Field(default_factory=list)
    stated_by_user: List[str] = Field(default_factory=list, description="Requirements explicitly provided by the user.")
    assumptions: List[str] = Field(default_factory=list, description="Assumptions made by the LLM, to be confirmed.")


class PlanSource(StrictModel):
    id: str = Field(pattern=IDENTIFIER)
    provider: str
    api_name: str
    api_type: str
    documentation_url: str
    base_url: str
    authentication: str
    rate_limits: Optional[str] = None
    metadata_available: Optional[str] = None
    update_frequency: Optional[str] = None
    priority: Literal["official_api", "official_download", "official_database", "secondary"] = "official_api"
    proposed_connector: Optional[str] = None


VerificationStatus = Literal["confirmed", "partially_verified", "unverified"]


class VerificationEvidence(StrictModel):
    query: str = Field(description="Exact query or URL used.")
    performed_on: date
    result: str = Field(description="What the query returned.")


class IndicatorMapping(StrictModel):
    research_variable: str
    source: str
    indicator_code: str
    official_name: str
    definition: Optional[str] = None
    unit: Optional[str] = None
    measurement_scale: Optional[str] = None
    population_coverage: Optional[str] = None
    geographic_coverage: Optional[str] = None
    time_coverage: Optional[str] = None
    aggregation_methodology: Optional[str] = None
    category: Optional[str] = None
    documentation_url: Optional[str] = None
    alternatives_considered: List[str] = Field(default_factory=list)
    verification_status: VerificationStatus
    evidence: List[VerificationEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _evidence(self) -> "IndicatorMapping":
        if self.verification_status != "unverified" and not self.evidence:
            raise ValueError(
                f"indicator '{self.indicator_code}' is marked '{self.verification_status}' but has no evidence"
            )
        return self


class Approval(StrictModel):
    status: Literal["draft", "approved", "rejected"] = "draft"
    approved_by: Optional[str] = None
    approved_on: Optional[date] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _check(self) -> "Approval":
        if self.status == "approved" and (not self.approved_by or not self.approved_on):
            raise ValueError("an approved plan must record approved_by and approved_on")
        return self


class ExtractionPlan(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    plan_id: str = Field(pattern=PROJECT_NAME)
    created: date
    prepared_by: str = Field(description="Who prepared the plan, e.g. 'LLM (model name) with researcher'.")
    research: ResearchRequirements
    sources: List[PlanSource] = Field(min_length=1)
    indicator_mapping: List[IndicatorMapping] = Field(min_length=1)
    expected_data_structure: str
    known_limitations: List[str] = Field(default_factory=list)
    outstanding_decisions: List[str] = Field(default_factory=list)
    approval: Approval = Field(default_factory=Approval)

    @model_validator(mode="after")
    def _cross(self) -> "ExtractionPlan":
        ids = {s.id for s in self.sources}
        for m in self.indicator_mapping:
            if m.source not in ids:
                raise ValueError(f"indicator mapping '{m.indicator_code}' refers to unknown source '{m.source}'")
        if self.approval.status == "approved" and self.outstanding_decisions:
            raise ValueError("a plan cannot be approved while outstanding_decisions remain")
        return self
