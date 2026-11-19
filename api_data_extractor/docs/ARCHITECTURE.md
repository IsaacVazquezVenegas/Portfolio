# Architecture

## Division of responsibilities

| Actor | Responsibility | Artefact |
|---|---|---|
| Researcher | Research objectives, scientific meaning, approval | `approval` block of the plan |
| LLM | Discovery, indicator mapping, configuration drafting | `extraction_plan.json`, `extraction_config.yaml` |
| Python engine | Validation, API access, extraction, processing, documentation | output package |
| Data sources | Original observations and metadata | raw responses |

The LLM never executes anything; the engine never interprets the research question.

## Flow

```text
 researcher ──► LLM (LLM_DATA_EXTRACTION_PROTOCOL.md)
                  │ extraction_plan.json (draft) ──► researcher approves
                  ▼
           extraction_config.yaml
                  │
 python -m src.main run
   1-2  validation.py   YAML safe_load → Pydantic models (models.py) → connector checks
                        → env vars present → plan approved
   3-4  connectors/*    one connector + ApiClient per source
   5-6  api_client.py   retries, rate limit, auth, every response → RawStore
   7    connectors/*    responses → standard observations
   8    transformation  explicit steps only, each recorded
   9    quality.py      warnings/errors, nothing discarded
  10    export.py       CSV / Excel / JSON
  11    metadata.py     data dictionary, manifest, README
  12    extraction.py   status: success | completed_with_warnings | partial | failed
```

## Modules

| Module | Role |
|---|---|
| `src/models.py` | Pydantic models for configuration and plan (single source of truth for the JSON Schemas) |
| `src/validation.py` | Loading, structural + environmental validation, schema export |
| `src/api_client.py` | HTTP, retries/back-off, `Retry-After`, rate limiting, auth injection, redaction, raw store |
| `src/connectors/base.py` | Connector interface and helpers |
| `src/connectors/{who,world_bank,eurostat,generic_rest}.py` | Source-specific logic |
| `src/connectors/__init__.py` | Registry of approved connectors |
| `src/transformation.py` | Long-format dataset, harmonisation, missing values, dedup, wide panel |
| `src/quality.py` | Quality checks |
| `src/export.py` | File writers (formula-safe Excel, UTF-8-BOM CSV, checksums) |
| `src/metadata.py` | Data dictionary, manifest, README |
| `src/extraction.py` | Orchestration |
| `src/logging_config.py` | Console/file logging with secret redaction |
| `src/reference/countries.csv` | ISO3/ISO2 reference (World Bank + WHO country lists, 2026-11-19) |

## Connector contract

```python
class MyConnector(BaseConnector):
    connector_id = "my_api"
    description = "..."
    api_types = ("rest",)
    allowed_filter_keys = ("dimension_a",)       # None = any

    @classmethod
    def check_task(cls, source, task): ...        # optional extra static checks

    def fetch_observations(self, task, indicator) -> list[dict]:
        resp = self.client.get(url, params, purpose=f"data:{indicator.code}")  # always via ApiClient
        return [{"country_code": ..., "period": ..., "value": ..., "value_text": ...,
                 "request_id": resp.request_id, "source_url": resp.url, ...}]

    def fetch_indicator_metadata(self, indicator) -> dict | None: ...  # {"found", "name", "definition", "unit"}
```

Register it in `src/connectors/__init__.py`. Nothing else changes: validation, processing, quality checks and exports are shared. The World Bank and Eurostat connectors were added this way (specification §13, criterion 12).

Rules for connectors: issue requests only through `self.client`; return numbers in `value` only when the source types them as numbers; keep the displayed value in `value_text`; never write files; refuse to follow pagination links to another host.

## Traceability

Every dataset row carries `request_id` → the matching entry in `raw_data.json` (URL, parameters, status, timestamp, attempts, verbatim body) and `source_record_id` where the source provides one. `country_code_source` keeps the source's identifier next to the harmonised one. The manifest stores the SHA-256 of the configuration and every output file.

## Security

* `yaml.safe_load` only; configurations cannot construct objects or contain code.
* Strict schemas (`extra="forbid"`), whitelisted connectors, filter keys and template placeholders (text substitution, no `format()`/`eval`).
* HTTPS by default; non-HTTP URLs refused; pagination links restricted to the source host.
* Credentials only from environment variables / `.env` (git-ignored); injected at request time; redacted from raw records, logs and errors.
* Excel cells starting with `=` are written as text (no formula injection).

## Known limitations (v1.0)

* GET-based JSON APIs only (POST is available in `ApiClient` but no connector uses it); no SDMX-XML or CSV-download connector yet.
* Eurostat large requests may be answered asynchronously by the API; not handled.
* Time filtering is by calendar year.
* The country reference table is a snapshot; unknown codes are kept and reported, never dropped.
