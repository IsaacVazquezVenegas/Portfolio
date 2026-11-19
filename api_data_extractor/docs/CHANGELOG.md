# Changelog

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
