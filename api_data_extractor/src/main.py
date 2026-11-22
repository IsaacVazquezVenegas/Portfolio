"""Command-line interface.

Run from the ``api_data_extractor`` folder::

    python -m src.main validate --config projects/WHO_MPOWER/extraction_config.yaml
    python -m src.main run      --config projects/WHO_MPOWER/extraction_config.yaml
    python -m src.main validate-plan --plan projects/WHO_MPOWER/extraction_plan.json
    python -m src.main render-plan --plan projects/WHO_MPOWER/extraction_plan.json
    python -m src.main connectors
    python -m src.main schemas

Exit codes: 0 success (possibly with warnings), 1 invalid configuration,
2 extraction failed, 3 extraction partially failed, 4 output folder exists.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

from . import APP_NAME, __version__
from .logging_config import setup_console
from .validation import ConfigValidationError, export_json_schemas, load_plan, plan_warnings, validate_config_file

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # optional dependency
        return
    load_dotenv(Path.cwd() / ".env")


def cmd_validate(args) -> int:
    report = validate_config_file(args.config, check_env=not args.skip_env,
                                  require_plan_approval=not args.allow_unapproved_plan)
    cfg = report.config
    print(f"OK: configuration '{cfg.project.name}' v{cfg.project.version} is valid")
    for task in cfg.extractions:
        src = cfg.source(task.source)
        countries = "all" if task.countries == "all" else f"{len(task.countries)} countries"
        print(f"  - {task.id}: {len(task.indicators)} indicator(s) from {src.provider} [{src.connector}], "
              f"{countries}, {task.start_year}-{task.end_year}")
    if report.plan:
        print(f"  plan: {report.plan.plan_id} (approval: {report.plan.approval.status})")
    for w in report.warnings:
        print(f"  WARNING: {w}")
    return 0


def cmd_validate_plan(args) -> int:
    plan = load_plan(args.plan)
    print(f"OK: extraction plan '{plan.plan_id}' is valid (approval: {plan.approval.status})")
    if plan.scoping is not None:
        counts = {}
        for row in plan.scoping.requirements_register:
            counts[row.status] = counts.get(row.status, 0) + 1
        summary = ", ".join(f"{n} {status.replace('_', ' ')}" for status, n in sorted(counts.items()))
        print(f"  register: {len(plan.scoping.requirements_register)} row(s) ({summary}); "
              f"{len(plan.scoping.open_questions())} open question(s)")
    by_status = {}
    for m in plan.indicator_mapping:
        by_status.setdefault(m.verification_status, []).append(m.indicator_code)
    for status, codes in sorted(by_status.items()):
        print(f"  {status}: {', '.join(codes)}")
    for d in plan.outstanding_decisions:
        print(f"  OUTSTANDING: {d}")
    for w in plan_warnings(plan):
        print(f"  WARNING: {w}")
    return 0


def cmd_render_plan(args) -> int:
    from .render import render_plan

    plan = load_plan(args.plan)
    text = render_plan(plan, plan_warnings(plan))
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"written {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_run(args) -> int:
    from .extraction import STATUS_FAILED, STATUS_PARTIAL, OutputExistsError, run_extraction

    try:
        result = run_extraction(args.config, overwrite=True if args.overwrite else None,
                                require_plan_approval=not args.allow_unapproved_plan)
    except OutputExistsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4
    counts = {lvl: sum(1 for i in result.issues if i.level == lvl) for lvl in ("error", "warning")}
    print(f"\nStatus: {result.status} - {result.rows} observation(s), "
          f"{counts['error']} error(s), {counts['warning']} warning(s)")
    print(f"Output: {result.output_dir}")
    if result.status == STATUS_FAILED:
        return 2
    if result.status == STATUS_PARTIAL:
        return 3
    return 0


def cmd_connectors(args) -> int:
    from .connectors import available_connectors, get_connector_class

    for cid in sorted(available_connectors()):
        cls = get_connector_class(cid)
        print(f"{cid:14s} {cls.description}  (api_type: {', '.join(cls.api_types)})")
    return 0


def cmd_schemas(args) -> int:
    for path in export_json_schemas(args.output):
        print(f"written {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.main", description="Configuration-driven API data extraction")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug messages")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="validate a configuration without executing it")
    p.add_argument("--config", required=True)
    p.add_argument("--skip-env", action="store_true", help="do not check authentication environment variables")
    p.add_argument("--allow-unapproved-plan", action="store_true", help="accept a plan that is not yet approved")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("validate-plan", help="validate an extraction plan")
    p.add_argument("--plan", required=True)
    p.set_defaults(func=cmd_validate_plan)

    p = sub.add_parser("render-plan", help="render an extraction plan as readable Markdown")
    p.add_argument("--plan", required=True)
    p.add_argument("--output", help="write to this file instead of the console")
    p.set_defaults(func=cmd_render_plan)

    p = sub.add_parser("run", help="validate and execute a configuration")
    p.add_argument("--config", required=True)
    p.add_argument("--overwrite", action="store_true", help="replace an existing output folder")
    p.add_argument("--allow-unapproved-plan", action="store_true",
                   help="execute although the linked plan is not approved (test runs only)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("connectors", help="list approved connectors")
    p.set_defaults(func=cmd_connectors)

    p = sub.add_parser("schemas", help="regenerate the JSON Schemas from the models")
    p.add_argument("--output", default=str(ROOT / "schemas"))
    p.set_defaults(func=cmd_schemas)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    setup_console(logging.DEBUG if args.verbose else logging.INFO)
    _load_dotenv()
    try:
        return args.func(args)
    except ConfigValidationError as exc:
        print(f"ERROR: {exc.render()}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
