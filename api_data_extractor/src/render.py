"""Readable Markdown version of an extraction plan (``render-plan`` command).

The output follows the layout of ``docs/examples/WHO_MPOWER_stage1.md``:
the Stage 1 record first (inputs, scope, Requirements Register, questions,
notes), then, from status ``draft`` on, the sources, the indicator mapping
and the approval. It is generated from the plan only; nothing is added.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from .models import ExtractionPlan, RegisterRow, Scoping

STARTING_POINTS = {
    "A": "the researcher knows the source and the indicator codes or dataset names",
    "B": "the researcher knows the source or dataset only by name or description",
    "C": "the researcher has a research question only",
}

STATUS_LABELS = {
    "clear": "clear",
    "ambiguous": "ambiguous",
    "needs_another_source": "needs another source",
    "parked": "parked",
    "dropped": "dropped",
}

KIND_LABELS = {
    "policy": "policy",
    "programme_service": "programme or service",
    "outcome": "outcome",
    "composite_index": "composite index",
    "price_tax": "price / tax",
    "intervention_effect": "**intervention effect**",
    "other": "other",
    "unknown": "?",
}


def _cell(text: Optional[str]) -> str:
    """Make a value safe for a Markdown table cell."""
    if text is None or text == "":
        return "—"
    return str(text).replace("|", "/").replace("\n", " ")


def _table(header: List[str], rows: Iterable[List[str]]) -> List[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return lines


def _common_source(scoping: Scoping) -> Optional[str]:
    """The source shared by every candidate, if there is exactly one."""
    sources = {c.source for r in scoping.requirements_register for c in r.candidates}
    return sources.pop() if len(sources) == 1 else None


def _candidates(row: RegisterRow, common_source: Optional[str]) -> str:
    if not row.candidates:
        return "—"
    parts = []
    for c in row.candidates:
        text = f"`{c.code}` ({c.level}"
        text += f", {c.source})" if c.source and c.source != common_source else ")"
        if c.note:
            text += f": {c.note}"
        parts.append(text)
    return "; ".join(parts)


def _status(row: RegisterRow) -> str:
    label = STATUS_LABELS[row.status]
    return f"{label}: {row.reason}" if row.reason else label


def render_scoping(scoping: Scoping) -> List[str]:
    lines = ["## Stage 1 — Scoping", ""]
    lines.append(f"**Starting point:** {scoping.starting_point} ({STARTING_POINTS[scoping.starting_point]}).")
    lines.append("")
    lines.append("**Inputs supplied:**")
    lines.append("")
    lines += [f"* {i.description} ({i.form.replace('_', ' ')})" for i in scoping.inputs]
    lines.append("")
    if scoping.purpose:
        lines += [f"**Purpose:** {scoping.purpose}", ""]
    sc = scoping.scope
    lines += ["**Scope:**", ""]
    lines += _table(
        ["Item", "Value", "Origin"],
        [[name.capitalize(), item.value, item.origin] for name, item in
         (("countries", sc.countries), ("period", sc.period), ("breakdown", sc.breakdown), ("outputs", sc.outputs))],
    )
    lines.append("")
    if scoping.retrieval_strategy:
        lines += [f"**Retrieval strategy:** {scoping.retrieval_strategy}", ""]

    if scoping.catalogue_lookups:
        lines += ["### Catalogue look-ups (unverified until Stage 4)", ""]
        for c in scoping.catalogue_lookups:
            rows = f" (rows {', '.join(c.rows)})" if c.rows else ""
            lines.append(f"* `{c.query}` ({c.performed_on.isoformat()}){rows}: {c.result}")
        lines.append("")

    lines += ["### Requirements Register", ""]
    common = _common_source(scoping)
    if common:
        lines += [f"All candidate retrieval units come from {common}.", ""]
    lines += _table(
        ["ID", "Researcher's wording", "Group", "Interpreted concept", "Kind of data", "Origin",
         "Candidate retrieval unit", "Status", "Question"],
        [[r.id, r.user_wording, r.group, r.interpreted_concept,
          " / ".join(KIND_LABELS[k] for k in r.kind_of_data), r.origin, _candidates(r, common), _status(r),
          ", ".join(r.questions) or None]
         for r in scoping.requirements_register],
    )
    lines.append("")

    if scoping.questions:
        lines += ["### Questions", ""]
        for q in scoping.questions:
            rows = f", rows {', '.join(q.rows)}" if q.rows else ""
            lines.append(f"* **{q.id}** (round {q.round}{rows}): {q.text} *[Default: {q.default}]*")
            if q.status == "answered":
                lines.append(f"  * Answer: {q.answer}")
            elif q.status == "default_applied":
                lines.append("  * No answer: the default was applied.")
            else:
                lines.append("  * Open.")
        lines.append("")

    if scoping.analytical_notes:
        lines += ["### Analytical notes (not part of the extraction)", ""]
        for n in scoping.analytical_notes:
            rows = f" (rows {', '.join(n.rows)})" if n.rows else ""
            lines.append(f"* {n.id}{rows}: {n.note}")
        lines.append("")

    settled = [r.id for r in scoping.requirements_register if r.status != "ambiguous"]
    open_items = [r.id for r in scoping.rows_with_status("ambiguous")] + [q.id for q in scoping.open_questions()]
    lines.append(f"**Settled:** {', '.join(settled) or 'nothing yet'}.")
    lines.append(f"**Open:** {', '.join(open_items) or 'nothing'}.")
    lines.append("")
    return lines


def render_plan(plan: ExtractionPlan, warnings: Optional[List[str]] = None) -> str:
    lines = [
        f"# Extraction plan — {plan.plan_id}",
        "",
        f"* **Status:** {plan.approval.status}",
        f"* **Created:** {plan.created.isoformat()}",
        f"* **Prepared by:** {plan.prepared_by}",
    ]
    if plan.approval.approved_by:
        lines.append(f"* **Approved by:** {plan.approval.approved_by} on {plan.approval.approved_on}")
    if plan.approval.notes:
        lines.append(f"* **Approval notes:** {plan.approval.notes}")
    lines.append("")

    if plan.scoping is not None:
        lines += render_scoping(plan.scoping)

    if plan.research is not None:
        r = plan.research
        lines += ["## Research requirements", "", f"**Objective:** {r.objective}", "",
                  f"**Subject:** {r.subject}", ""]
        lines += [f"* **Countries:** {r.geographic_coverage}", f"* **Period:** {r.time_period}",
                  f"* **Frequency:** {r.frequency}", f"* **Aggregation:** {r.aggregation_level}"]
        if r.output_formats:
            lines.append(f"* **Outputs:** {', '.join(r.output_formats)}")
        lines.append("")
        for title, items in (("Stated by the researcher", r.stated_by_user), ("Assumptions to confirm", r.assumptions),
                             ("Analytical applications", r.analytical_applications)):
            if items:
                lines += [f"**{title}:**", ""] + [f"* {i}" for i in items] + [""]

    if plan.sources:
        lines += ["## Sources", ""]
        lines += _table(
            ["ID", "Provider", "API", "Connector", "Authentication", "Documentation"],
            [[s.id, s.provider, s.api_name, s.proposed_connector, s.authentication, s.documentation_url]
             for s in plan.sources],
        )
        lines.append("")

    if plan.indicator_mapping:
        lines += ["## Indicator mapping", ""]
        lines += _table(
            ["Register rows", "Variable", "Source", "Indicator code", "Official name", "Unit / scale",
             "Coverage", "Status"],
            [[", ".join(m.register_rows) or None, m.research_variable, m.source, f"`{m.indicator_code}`",
              m.official_name, " / ".join(x for x in (m.unit, m.measurement_scale) if x) or None,
              "; ".join(x for x in (m.geographic_coverage, m.time_coverage) if x) or None,
              m.verification_status]
             for m in plan.indicator_mapping],
        )
        lines.append("")

    excluded = plan.excluded_rows()
    if excluded:
        lines += ["## Not retrieved by this plan", ""]
        lines += [f"* **{r.id}** {r.user_wording}: {STATUS_LABELS[r.status]}. {r.reason}" for r in excluded]
        lines.append("")

    if plan.expected_data_structure:
        lines += ["## Expected data structure", "", plan.expected_data_structure, ""]
    for title, items in (("Known limitations", plan.known_limitations),
                         ("Outstanding decisions", plan.outstanding_decisions)):
        if items:
            lines += [f"## {title}", ""] + [f"* {i}" for i in items] + [""]
    if warnings:
        lines += ["## Validation warnings", ""] + [f"* {w}" for w in warnings] + [""]
    return "\n".join(lines).rstrip("\n") + "\n"
