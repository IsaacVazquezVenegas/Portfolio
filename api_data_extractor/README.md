# API Data Extraction Framework

A configuration-driven Python engine that retrieves observations from official statistical APIs, preserves the raw responses, and produces analysis-ready, fully documented datasets.

An LLM (ChatGPT, Claude, …) following [`docs/LLM_DATA_EXTRACTION_PROTOCOL.md`](docs/LLM_DATA_EXTRACTION_PROTOCOL.md) helps the researcher find sources and indicators and drafts two files: an **extraction plan** (approved by the researcher) and an **extraction configuration**. Python validates and executes the configuration locally, without any LLM.

```text
research question ─► LLM ─► extraction_plan.json (scoping → draft) ─► researcher approval ─► extraction_config.yaml
                                                                                   │
                                     python -m src.main run ◄──────────────────────┘
                                                │
                     dataset.csv / .xlsx · raw_data.json · data_dictionary.xlsx · manifest · log · README
```

**Connectors:** WHO GHO (OData) · World Bank Indicators v2 · Eurostat (JSON-stat) · generic REST/JSON.

## Installation (Windows + VS Code)

1. Install Python 3.10 or later from python.org (tick *Add python.exe to PATH*).
2. Open the `api_data_extractor` folder in VS Code, then open a terminal (*Terminal → New Terminal*, PowerShell).
3. Create and activate a virtual environment and install the dependencies:

```powershell
python -m venv .venv
```

```powershell
.\.venv\Scripts\Activate.ps1
```

```powershell
pip install -r requirements.txt
```

If PowerShell refuses to run the activation script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use `cmd` and `.venv\Scripts\activate.bat`. In VS Code, select the interpreter `.venv` (*Ctrl+Shift+P → Python: Select Interpreter*).

On macOS/Linux use `source .venv/bin/activate`.

4. Check the installation:

```powershell
python -m pytest
```

## Usage

All commands are run from the `api_data_extractor` folder.

```powershell
python -m src.main connectors
```

```powershell
python -m src.main validate-plan --plan projects/WHO_MPOWER/extraction_plan.json
python -m src.main render-plan --plan projects/WHO_MPOWER/extraction_plan.json --output plan.md
```

`render-plan` turns the plan into a readable Markdown document (Stage 1 register, questions, indicators, items left out) for review or for colleagues.

```powershell
python -m src.main validate --config projects/WHO_MPOWER/extraction_config.yaml
```

```powershell
python -m src.main run --config projects/WHO_MPOWER/extraction_config.yaml
```

The WHO MPOWER plan is deliberately left in **draft**: the researcher must review it, clear `outstanding_decisions` and set

```json
"approval": {"status": "approved", "approved_by": "Your Name", "approved_on": "2026-11-19"}
```

Until then `run` refuses to execute. For exploratory test runs only:

```powershell
python -m src.main run --config projects/WHO_MPOWER/extraction_config.yaml --allow-unapproved-plan
```

Each run writes a new folder `projects/WHO_MPOWER/output/WHO_MPOWER_<timestamp>/`:

| File | Content |
|---|---|
| `dataset.csv` | Long format: one row per indicator × country × period × dimension |
| `dataset_wide.csv` | Country-year panel (when `processing.wide_panel: true`) |
| `dataset.xlsx` | Data, wide panel and quality-check sheets |
| `dataset.json` | Processed records (when `json` is in `output.formats`) |
| `raw_data.json` | Every HTTP response, verbatim (secrets redacted) |
| `data_dictionary.xlsx` | Definition, source, code, unit, scale, coverage and processing of each variable; description of each column |
| `extraction_log.txt` | Execution log |
| `extraction_manifest.json` | Timestamps, versions, endpoints, parameters, request list, counts, errors, warnings, processing steps, file checksums |
| `extraction_config.yaml`, `extraction_plan.json` | Snapshots of what was executed |
| `README.md` | Human-readable summary, including the requested items the extraction leaves out (parked or dropped in the plan) |

## Starting a new project

1. Give the LLM the protocol document and describe your research need.
2. At the end of scoping, save the Stage 1 checkpoint as `projects/<NAME>/extraction_plan.json` (status `scoping`) and check it with `validate-plan`.
3. Continue with the LLM until the plan reaches `draft`, and save `projects/<NAME>/extraction_config.yaml`.
4. `validate-plan` and `render-plan`, review, approve; `validate`; `run`.

The annotated template [`config/extraction_config.yaml`](config/extraction_config.yaml) shows every section (Eurostat + World Bank). The full option list is in [`docs/CONFIGURATION_REFERENCE.md`](docs/CONFIGURATION_REFERENCE.md); the formal schemas are in `schemas/`.

## Data Fetcher page (running the protocol inside Claude)

When the protocol runs in a Claude environment that can reach the APIs, Claude also runs the engine there and publishes the results to a private **Data Fetcher** page ([`page/index.html`](page/index.html)): register, plan, approval, runs, coverage and distributions per series, a row preview with the source request of every row, and downloads. See [`docs/DATA_FETCHER_PAGE.md`](docs/DATA_FETCHER_PAGE.md).

```powershell
python -m src.main page-export --plan projects/WHO_MPOWER/extraction_plan.json --run projects/WHO_MPOWER/output/<run> --site <page folder> --note "why the plan changed"
python -m src.main apply-approval --plan projects/WHO_MPOWER/extraction_plan.json --approval approval.json
```

`page-export` writes the page's data files and logs every change to the plan; `apply-approval` copies an approval given on the page into the plan, after checking it approves this exact version.

## Credentials

Never put keys in configurations or LLM chats. Name the variable in the configuration (`authentication.env_var: MY_API_KEY`) and define it in a local `.env` file (copy `.env.example`; `.env` is git-ignored) or in your shell. Secrets are redacted from logs and raw files.

## Tests

```powershell
python -m pytest
```

Offline tests (mocked responses in `tests/mock_responses/`) cover validation, the HTTP client (retries, rate limits, authentication, invalid responses), each connector, processing and the full output package. Live tests against WHO, Eurostat and World Bank:

```powershell
python -m pytest --live tests/test_live.py
```

## Principles

* Raw responses are always preserved; every dataset row has a `request_id` pointing to its response.
* Only explicitly configured transformations are applied, and each one is recorded in the manifest.
* No interpolation; categorical measures (e.g. MPOWER levels) stay text in `value_text` — `value` holds numbers only when the source provides numbers.
* Quality issues produce warnings; data are never silently discarded.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the design and how to add a connector.
