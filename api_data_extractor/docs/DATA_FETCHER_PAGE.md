# Data Fetcher page

The Data Fetcher page is a private artifact that shows extraction projects run with this framework inside a Claude environment. The division of work in `ARCHITECTURE.md` is unchanged:

| Actor | Does | Never does |
|---|---|---|
| Researcher | Answers the Stage 1 questions in the chat, approves the plan on the page | — |
| Claude | Runs the protocol in the chat, writes the plan, runs the engine, exports and publishes | Approves a plan, calls an API outside the engine for data |
| Engine | Validates, extracts, summarises (`page-export`), records approvals (`apply-approval`) | Interprets the research question |
| Page | Shows the state, records the researcher's approval, rebuilds downloads | Calls an API, calls Claude |

## Views

| View | Shows | Detail on click | Actions |
|---|---|---|---|
| Projects | One row per project: plan status, register counts, last run | — | Open |
| Register | Scope, open questions, one row per register item with status filters and search | Concept, candidates, questions, catalogue look-ups, linked indicators | Discuss this row (copies a chat message) |
| Plan | Warnings, research scope, sources, indicators with verification status | Source details; indicator definition, evidence queries, alternatives | Approve plan, Copy run request |
| Runs | One row per run, test runs flagged | Errors, grouped warnings, files | Download each file, Discuss these warnings |
| Data | One row per series: kind, countries, years, coverage | Distribution by year, coverage grid (value / reported without value / not in the source) | Preview rows (each row links to its request) |
| History | Plan changes (field by field), runs, approvals | The changes, before and after | — |

## Files published beside the page

Written by `python -m src.main page-export --site <folder>`; the folder mirrors the page's files.

```text
data/projects.json                          one entry per project, newest first
data/<plan_id>/plan.json                    the plan as written
data/<plan_id>/meta.json                    fingerprint (sha256 without the approval block), warnings
data/<plan_id>/history.json                 change log, newest first (actor: claude | engine)
data/<plan_id>/runs/<run_id>/summary.json   per-series coverage and distributions, files, warnings
data/<plan_id>/runs/<run_id>/...            dataset.csv, dataset_wide.csv, manifest, log, README,
                                            extraction_config.yaml.txt, <workbook>.xlsx.json (+ sheet CSVs),
                                            raw_data.partN.json
```

The page can serve only `.csv`, `.json`, `.txt` and `.md` files of at most 16 MB. Workbooks are therefore exported as their sheets and rebuilt in the browser on download; raw responses are split into parts of at most 12 MB and joined on download. A single raw response above 12 MB stops the export with an error.

## Database

| Collection | Document | Written by | Rule |
|---|---|---|---|
| `approvals` | `<plan_id>`: `status` (approved, withdrawn), `plan_sha256`, `by_id`, `by_name`, `at`, `note` | The page, when the researcher presses Approve | read: viewers; write: owner |
| `events` | one per approval or withdrawal: `plan_id`, `kind`, `plan_sha256`, `by_name`, `at`, `note` | The page | read: viewers; write: owner |

Claude reads `approvals/<plan_id>`, saves it as JSON and runs `apply-approval`. The command refuses an approval given to an earlier version of the plan, a withdrawn approval, or a plan that does not validate as approved (for example with outstanding decisions).

## Updating the page

Publish `page/index.html` over the same artifact. Data files left out of a publish are kept, so a plan update sends only `data/projects.json` and `data/<plan_id>/*.json`, and a run adds its own folder.
