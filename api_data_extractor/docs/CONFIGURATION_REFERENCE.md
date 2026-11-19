# Configuration reference (schema 1.0)

The formal definition is `schemas/extraction_config.schema.json`, generated from `src/models.py`
(`python -m src.main schemas`). Unknown fields are rejected everywhere. Paths are relative to the configuration file.

Annotated examples: `config/extraction_config.yaml` (Eurostat + World Bank) and
`projects/WHO_MPOWER/extraction_config.yaml` (WHO GHO).

## Top level

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | `"1.0"` | no | |
| `project` | object | **yes** | §A |
| `sources` | list | **yes** | §B, at least one |
| `extractions` | list | **yes** | §C, at least one |
| `processing` | object | no | §D |
| `validation` | object | no | §E |
| `output` | object | no | §F |

## A. `project`

| Field | Default | Notes |
|---|---|---|
| `name` | – | Letters, digits, `_`, `-`; used in folder names |
| `description` | – | |
| `version` | – | `1.0` or `1.0.0`; bump it whenever the configuration changes |
| `created` | – | `YYYY-MM-DD` |
| `extraction_plan` | none | Path to the plan. If set, the plan must be valid and **approved** before `run` |
| `authors` | `[]` | |

## B. `sources[]`

| Field | Default | Notes |
|---|---|---|
| `id` | – | Referenced by `extractions[].source` |
| `provider`, `api_name` | – | Documentation |
| `api_type` | – | `rest`, `odata`, `json-stat`, `sdmx`; must be supported by the connector |
| `connector` | – | `who_gho`, `world_bank`, `eurostat`, `generic_rest` |
| `base_url` | – | Must be `https://` unless `allow_insecure_http: true` |
| `endpoint` | none | `generic_rest` only; template relative to `base_url` |
| `documentation_url` | – | |
| `api_version` | none | Recorded in the manifest |
| `authentication.method` | `none` | `none`, `api_key_header`, `api_key_query`, `bearer_token` |
| `authentication.env_var` | – | **Name** of the environment variable holding the secret (e.g. in `.env`) |
| `authentication.param_name` | – | Header or query parameter name for API keys |
| `request.timeout_seconds` | 60 | |
| `request.max_retries` | 3 | Retries on connection errors, timeouts, HTTP 429/5xx |
| `request.backoff_seconds` | 2 | Doubles after each retry; `Retry-After` is honoured (max 120 s) |
| `request.min_interval_seconds` | 0.5 | Minimum delay between requests (rate limit) |
| `request.headers` | `{}` | Non-secret headers only (`Authorization`, `Cookie`, `X-API-Key`… are refused) |
| `pagination` | `type: none` | `generic_rest` only, see below |
| `response` | none | `generic_rest` only, see below |

### `generic_rest` options

```yaml
endpoint: "series/{indicator}/countries/{countries}"   # placeholders: indicator, countries, country, start_year, end_year
pagination:
  type: page            # none | page | offset | next_link
  page_param: page
  first_page: 1
  page_size_param: per_page
  page_size: 500
  total_pages_path: meta.pages      # page: stop at total; else stop when a page is short/empty
  # offset: offset_param, limit_param, page_size
  # next_link: next_link_path (same host only)
  max_pages: 1000
response:
  records_path: data                # dotted path to the list of records ('' = root)
  country_separator: ";"
  fields:                           # standard field -> dotted path in each record
    country_code: country.iso3      # required
    period: date                    # required
    value: value                    # value or value_text required
    indicator_name: indicator.label
    unit: unit
```

`{country}` → one request per country; `{countries}` → batches joined with `country_separator`.
Placeholders are replaced textually; nothing is evaluated.

## C. `extractions[]`

| Field | Default | Notes |
|---|---|---|
| `id` | – | |
| `source` | – | A `sources[].id` |
| `indicators[]` | – | `code` (required), `name`, `definition`, `unit`, `category`, `measurement_scale`, `filters` |
| `countries` | – | `all` or list of codes **in the source's coding** (upper case) |
| `start_year`, `end_year` | – | Inclusive; `end_year ≥ start_year` |
| `frequency` | `annual` | `annual`, `quarterly`, `monthly`, `any` (`any` disables year-gap reporting) |
| `filters` | `{}` | Applied to all indicators; indicator `filters` override. Values: string, int or list |
| `query_params` | `{}` | Extra query parameters (`generic_rest`); templates allowed |
| `country_batch_size` | 50 | Countries per request |

Connector-specific filters:

| Connector | Indicator code | Allowed filter keys | Default behaviour |
|---|---|---|---|
| `who_gho` | GHO indicator code | `SpatialDimType`, `ParentLocationCode`, `TimeDimType`, `Dim1Type`, `Dim1`, `Dim2Type`, `Dim2`, `Dim3Type`, `Dim3`, `DataSourceDimType`, `DataSourceDim` | `SpatialDimType eq 'COUNTRY'` unless overridden; follows `@odata.nextLink`; fetches dimension labels and indicator names |
| `world_bank` | WDI code (e.g. `SH.PRV.SMOK`) | `source` | `per_page=1000`, pages followed; `all` includes regional aggregates (flagged) |
| `eurostat` | Dataset code (e.g. `hlth_ehis_sk1e`) | Any dataset dimension except `geo`, `time`, `sinceTimePeriod`, `untilTimePeriod`, `lang` | JSON-stat decoded; flags (e.g. `b`, `:`) go to `comments` |
| `generic_rest` | Anything | Any (sent as query parameters) | |

## D. `processing`

| Field | Default | Notes |
|---|---|---|
| `preserve_raw` | `true` | Cannot be disabled |
| `country_codes` | `iso3` | `iso3` harmonises (ISO2, `EL`, `UK`, `XK` → ISO3); `source` keeps codes. The original is always kept in `country_code_source` |
| `numeric_values` | `source_numeric_only` | `parse_plain_numbers` also converts text such as `"4"` to `4.0` — only use it if the researcher wants ordinal categories treated as numbers |
| `standardise_periods` | `true` | `YYYY`, `YYYY-Qn`, `YYYY-MM`, `YYYY-MM-DD` |
| `missing_values.treatment` | `keep` | `keep` flags rows (`missing = True`); `drop` removes them (counted) |
| `missing_values.markers` | `[]` | Text values meaning "no data" (e.g. `".."`) |
| `deduplicate` | `true` | Removes **exact** duplicates; same identity with different values is only flagged (`duplicate_conflict`) |
| `wide_panel` | `false` | Adds `dataset_wide.csv` / `wide_panel` sheet: one row per country-period, one column per `source:indicator [dimensions]` |

Never applied: interpolation, imputation, scoring of categories, merging different indicators.

## E. `validation`

| Field | Default | Notes |
|---|---|---|
| `expected_indicators` | all requested | |
| `expected_countries` | requested list | Compared after harmonisation |
| `expected_start_year`, `expected_end_year` | task range | |
| `check_duplicates`, `check_missing`, `check_units`, `check_indicator_definitions`, `check_coverage` | `true` | |
| `min_records` | 1 | Fewer rows → error |
| `max_missing_share` | 0.5 | Above → warning |
| `fail_on_request_error` | `false` | `true` stops at the first failed indicator |

## F. `output`

| Field | Default | Notes |
|---|---|---|
| `directory` | `output` | |
| `folder_name` | `{project}_{timestamp}` | Placeholders `{project}`, `{date}`, `{year}`, `{timestamp}` (UTC) |
| `overwrite` | `false` | An existing non-empty folder stops the run |
| `formats` | `[csv, xlsx]` | Any of `csv`, `xlsx`, `json` |
| `data_dictionary` | `true` | |
| `readme` | `true` | |

`raw_data.json`, `extraction_log.txt`, `extraction_manifest.json` and the configuration snapshot are always written.

## Exit codes

`0` success / warnings · `1` invalid configuration · `2` failed · `3` partial (some indicators failed) · `4` output folder exists.
