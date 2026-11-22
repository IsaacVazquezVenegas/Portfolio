# Changelog

## Unreleased — Data views and missing-value codes

* Plans record how each source codes missing values (`missing_value_codes`: `no_data`, `not_applicable`, `category`, with evidence). Validation fails when a `no_data` code is not in `processing.missing_values.markers`, and warns about sources with no recorded codes. New `value-codes --run DIR` command lists a run's text codes and proposes plan entries.
* Indicators gain a short `label` (plan and configuration). The wide CSV and the `wide_panel` sheet use readable column names, with the codes in the first data row.
* `page-export` writes `views.json` (+ parts). The page's Data tab has four views (Series, By year, By indicator, By country) and charts chosen from the data's shape (rank, dumbbell, distribution, categories, changes, stacked). An optional `view_spec.json`, written by Claude on request, overrides the engine's choices.

## Unreleased — Large runs on the Data Fetcher page

* `page-export` publishes CSV files above 12 MB (`dataset.csv`, `dataset_wide.csv`, large workbook sheets) as parts with the header repeated; the page joins them for the row preview and on download (`build: join_csv`, sheet `csv_parts`).
* The Excel export drops control characters that worksheets cannot hold (found in a WHO survey title); CSV and JSON keep the text as published.

## Unreleased — Data Fetcher page

* New page `page/index.html`, published as a private artifact: projects, Stage 1 register, plan with evidence, approval by the researcher, runs, data (coverage by country and year, distribution by year, row preview linked to source requests), history of changes. Light and dark themes.
* New `page-export` command: writes the plan, its fingerprint and warnings, and a structural log of every change; summarises a run per series (coverage, categorical or numeric distribution) and publishes its files in forms the page can serve (Excel sheets and raw responses as parts the page reassembles on download).
* New `apply-approval` command: records an approval given on the page in the plan, only if it names the plan's current fingerprint and the approved plan validates.
* `docs/DATA_FETCHER_PAGE.md` documents the page, its files and its database.

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
