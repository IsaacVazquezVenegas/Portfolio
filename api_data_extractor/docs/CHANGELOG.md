# Changelog

## Unreleased — Stage 1 in the extraction plan

* The plan gains a `scoping` section: starting point, inputs supplied, scope with its origin, retrieval strategy, Requirements Register, questions with defaults and answers, catalogue look-ups and analytical notes.
* New approval status `scoping` (before `draft`): a Stage 1 checkpoint is valid with only the `scoping` section. From `draft` on, `research`, `sources`, `indicator_mapping` and `expected_data_structure` are required and no register row may be `ambiguous`.
* `indicator_mapping[].register_rows` links each indicator to its register rows. `validate-plan` warns about clear rows with no indicator, indicators with no row, rows that still need another source and open questions; these are errors at approval.
* New `render-plan` command (Markdown version of the plan).
* The output README lists the parked, dropped and unresolved register rows.
* The WHO MPOWER plan carries a reconstructed `scoping` section; the Stage 1 worked example has its checkpoint (`docs/examples/WHO_MPOWER_stage1_plan.json`) and rendered version.
* Plans without a `scoping` section remain valid (with a warning).

## 1.0.0 — 2026-11-19

Initial implementation of Technical Specification v1.0.

* Configuration and extraction-plan models (Pydantic) with generated JSON Schemas.
* Two-layer validation: structure/types/connectors/filters, then credentials and plan approval.
* `ApiClient`: retries with exponential back-off and `Retry-After`, rate limiting, timeouts, header/query/bearer authentication from environment variables, secret redaction, preservation of every response.
* Connectors: `who_gho` (OData, nextLink paging, indicator and dimension metadata), `world_bank` (v2, paging, metadata), `eurostat` (JSON-stat 2.0 decoding, flags), `generic_rest` (templated endpoints, page/offset/next-link paging, field mapping).
* Processing: period standardisation, ISO3 harmonisation, missing-value flagging/dropping, exact-duplicate removal with conflict flagging, optional wide panel. No interpolation or categorical scoring.
* Quality checks: records, failed requests, coverage, unexpected countries/years/aggregates, missing values, units, indicator-name consistency, duplicates.
* Output package: CSV, Excel, JSON, raw JSON, data dictionary, log, manifest (with checksums), config/plan snapshots, README.
* CLI: `validate`, `validate-plan`, `run`, `connectors`, `schemas`.
* WHO MPOWER project (plan in draft, awaiting researcher approval) and a Eurostat + World Bank cross-source example.
* Tests: 77 offline (mocked) tests, 3 live API tests (`--live`).
