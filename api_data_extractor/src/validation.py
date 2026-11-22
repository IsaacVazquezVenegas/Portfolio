"""Loading and validation of extraction configurations and plans.

Validation happens in two layers:

1. **Structural** – the document is parsed with ``yaml.safe_load`` (no
   arbitrary object construction) and validated against the Pydantic models
   in :mod:`src.models` (types, mandatory fields, unknown fields, connector
   identifiers, date ranges, template placeholders...).
2. **Environmental** – checks that depend on the machine running the
   extraction: required authentication environment variables are set, the
   referenced extraction plan exists and has been approved by the researcher.

Any failure raises :class:`ConfigValidationError` with readable messages and
stops execution. Nothing the LLM writes can bypass this step.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Union

import yaml
from pydantic import ValidationError

from .models import ExtractionConfig, ExtractionPlan


class ConfigValidationError(Exception):
    """Raised when a configuration or plan is invalid. ``errors`` holds one message per problem."""

    def __init__(self, title: str, errors: List[str]):
        self.title = title
        self.errors = errors
        super().__init__(self.render())

    def render(self) -> str:
        lines = [self.title] + [f"  - {e}" for e in self.errors]
        return "\n".join(lines)


@dataclass
class ValidationReport:
    config: ExtractionConfig
    config_path: Path
    plan: Optional[ExtractionPlan] = None
    plan_path: Optional[Path] = None
    warnings: List[str] = field(default_factory=list)


def _format_pydantic_errors(exc: ValidationError) -> List[str]:
    messages = []
    for err in exc.errors():
        location = ".".join(str(p) for p in err["loc"]) or "<root>"
        msg = err["msg"]
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, ") :]
        if err["type"] == "extra_forbidden":
            msg = "unknown field (not part of the configuration schema)"
        messages.append(f"{location}: {msg}")
    return messages


def _load_document(path: Path, kind: str) -> dict:
    if not path.is_file():
        raise ConfigValidationError(f"{kind} not found", [str(path)])
    text = path.read_text(encoding="utf-8")
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise ConfigValidationError(f"{kind} is not syntactically valid: {path}", [str(exc)]) from None
    if not isinstance(data, dict):
        raise ConfigValidationError(f"{kind} must contain a mapping at the top level", [str(path)])
    return data


def check_connectors(config: ExtractionConfig) -> List[str]:
    """Connector-specific checks (supported api_type, filter names, placeholders...)."""
    from .connectors import get_connector_class

    errors: List[str] = []
    for task in config.extractions:
        source = config.source(task.source)
        errors.extend(get_connector_class(source.connector).check_task(source, task))
    return errors


def parse_config(data: dict, origin: str = "<memory>") -> ExtractionConfig:
    try:
        config = ExtractionConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigValidationError(f"Invalid extraction configuration ({origin})", _format_pydantic_errors(exc)) from None
    errors = check_connectors(config)
    if errors:
        raise ConfigValidationError(f"Invalid extraction configuration ({origin})", errors)
    return config


def parse_plan(data: dict, origin: str = "<memory>") -> ExtractionPlan:
    try:
        return ExtractionPlan.model_validate(data)
    except ValidationError as exc:
        raise ConfigValidationError(f"Invalid extraction plan ({origin})", _format_pydantic_errors(exc)) from None


def load_plan(path: Union[str, Path]) -> ExtractionPlan:
    path = Path(path)
    return parse_plan(_load_document(path, "Extraction plan"), str(path))


def plan_warnings(plan: ExtractionPlan) -> List[str]:
    """Traceability problems that do not make the plan invalid but must be shown to the researcher.

    The same conditions are hard errors once the plan is ``approved`` (see
    :class:`src.models.ExtractionPlan`).
    """
    warnings: List[str] = []
    status = plan.approval.status
    if plan.scoping is None:
        if status != "scoping":
            warnings.append("the plan has no 'scoping' section: Stage 1 (Requirements Register) is not recorded")
        return warnings
    scoping = plan.scoping
    if status == "scoping":
        for row in scoping.rows_with_status("ambiguous"):
            warnings.append(f"register row {row.id} ('{row.user_wording}') is still ambiguous")
        for q in scoping.open_questions():
            warnings.append(f"question {q.id} is open (default if unanswered: {q.default})")
        return warnings
    for row in plan.uncovered_rows():
        warnings.append(f"register row {row.id} ('{row.user_wording}') is 'clear' but no indicator mapping covers it")
    for m in plan.unlinked_indicators():
        warnings.append(f"indicator '{m.indicator_code}' names no register row (register_rows)")
    for row in scoping.rows_with_status("needs_another_source"):
        warnings.append(f"register row {row.id} ('{row.user_wording}') still needs another source: {row.reason}")
    for q in scoping.open_questions():
        warnings.append(f"question {q.id} is still open")
    return warnings


def check_authentication(config: ExtractionConfig, environ=None) -> List[str]:
    """Return an error for every source whose credential environment variable is not set."""
    environ = os.environ if environ is None else environ
    errors = []
    for source in config.sources:
        auth = source.authentication
        if auth.method != "none" and not environ.get(auth.env_var or ""):
            errors.append(
                f"sources.{source.id}.authentication: environment variable '{auth.env_var}' is not set "
                "(define it in your shell or in a local .env file)"
            )
    return errors


def check_plan(config: ExtractionConfig, config_path: Path, require_approval: bool) -> Tuple[
    Optional[ExtractionPlan], Optional[Path], List[str], List[str]
]:
    """Load the related extraction plan and check it is consistent and approved."""
    errors: List[str] = []
    warnings: List[str] = []
    if not config.project.extraction_plan:
        warnings.append("project.extraction_plan is not set: the configuration is not linked to an approved plan")
        return None, None, errors, warnings
    plan_path = (config_path.parent / config.project.extraction_plan).resolve()
    try:
        plan = load_plan(plan_path)
    except ConfigValidationError as exc:
        errors.extend([exc.title] + exc.errors)
        return None, plan_path, errors, warnings

    if plan.approval.status != "approved":
        msg = (
            f"extraction plan '{plan.plan_id}' has approval status '{plan.approval.status}'; "
            "the researcher must approve it before execution"
        )
        (errors if require_approval else warnings).append(msg)
    warnings.extend(f"plan: {w}" for w in plan_warnings(plan))

    planned = {(m.source, m.indicator_code) for m in plan.indicator_mapping}
    for task in config.extractions:
        for ind in task.indicators:
            if (task.source, ind.code) not in planned:
                warnings.append(
                    f"indicator '{ind.code}' (source '{task.source}') is not part of the extraction plan's indicator mapping"
                )
    for m in plan.indicator_mapping:
        if m.verification_status != "confirmed":
            warnings.append(f"indicator '{m.indicator_code}' availability is '{m.verification_status}' in the plan")
    return plan, plan_path, errors, warnings


def validate_config_file(
    path: Union[str, Path],
    *,
    check_env: bool = True,
    require_plan_approval: bool = True,
    environ=None,
) -> ValidationReport:
    """Fully validate a configuration file. Raises :class:`ConfigValidationError` on failure."""
    path = Path(path).resolve()
    config = parse_config(_load_document(path, "Configuration file"), str(path))
    errors: List[str] = []
    if check_env:
        errors.extend(check_authentication(config, environ))
    plan, plan_path, plan_errors, warnings = check_plan(config, path, require_plan_approval)
    errors.extend(plan_errors)
    if errors:
        raise ConfigValidationError(f"Configuration cannot be executed ({path})", errors)
    return ValidationReport(config=config, config_path=path, plan=plan, plan_path=plan_path, warnings=warnings)


def export_json_schemas(directory: Union[str, Path]) -> List[Path]:
    """Write the JSON Schemas generated from the Pydantic models."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, model, title in (
        ("extraction_config.schema.json", ExtractionConfig, "Extraction configuration"),
        ("extraction_plan.schema.json", ExtractionPlan, "Extraction plan"),
    ):
        schema = generate_schema(model, title)
        target = directory / filename
        target.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(target)
    return written


def generate_schema(model, title: str) -> dict:
    schema = model.model_json_schema()
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"api_data_extractor/{model.__name__}/1.0",
        **schema,
        "title": title,
    }
    return schema
