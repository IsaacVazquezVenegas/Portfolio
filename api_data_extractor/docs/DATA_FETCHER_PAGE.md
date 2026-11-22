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
| Data | Four views of the run: Series (one row per series with coverage), By year (countries × series for one year), By indicator (countries × years for one series), By country (every series for one country) | A chart chosen from the data's shape, coverage grid (value / reported without value / not in the source) | Preview rows (each row links to its request), Redo the views with Claude |
| History | Plan changes (field by field), runs, approvals | The changes, before and after | — |

## Files published beside the page

Written by `python -m src.main page-export --site <folder>`; the folder mirrors the page's files.

```text
data/projects.json                          one entry per project, newest first
data/<plan_id>/plan.json                    the plan as written
data/<plan_id>/meta.json                    fingerprint (sha256 without the approval block), warnings
data/<plan_id>/history.json                 change log, newest first (actor: claude | engine)
data/<plan_id>/extraction_config.yaml.txt   the configuration found beside the plan, if any
data/<plan_id>/runs/<run_id>/summary.json   per-series coverage and distributions, files, warnings
data/<plan_id>/runs/<run_id>/views.json     years, countries, groups, series; cells in views.partN.json
data/<plan_id>/runs/<run_id>/view_spec.json optional: Claude's choices for the views (see below)
data/<plan_id>/runs/<run_id>/...            dataset.csv, dataset_wide.csv, manifest, log, README,
                                            extraction_config.yaml.txt, <workbook>.xlsx.json (+ sheet CSVs),
                                            raw_data.partN.json
```

The page can serve only `.csv`, `.json`, `.txt` and `.md` files of at most 16 MB. Workbooks are therefore exported as their sheets and rebuilt in the browser on download; raw responses are split into parts of at most 12 MB and joined on download. CSV files above 12 MB (`dataset.csv`, `dataset_wide.csv`, large workbook sheets) are published as `<name>.partN.csv`, each with the header row, and the page joins them for the row preview and on download. A single raw response or CSV row above 12 MB stops the export with an error.

## Data views

`page-export` analyses each run without calling Claude. Series take their readable name from the configuration's `label` (or `name`) and their group from `category`, in configuration order. Each cell in `views.partN.json` is a number, a text, `{"m": "<code>"}` for a value the source marks as missing, or `null` when the source has no row. The chart follows the shape:

| Series | 1 year | 2–3 years | 4 years or more |
|---|---|---|---|
| Numeric | Countries ranked | Dumbbell (first and last year per country) | Distribution by year |
| Categorical | Category counts | Category counts and the list of countries that changed | Stacked categories by year |

When the views are not helpful, **Redo the views with Claude** copies a chat message (`/datafetcher <plan_id>: redo the views of run <run_id>. What is not helpful: …`). Claude then writes `view_spec.json` in the run folder and republishes; the page applies it over the engine's choices:

```json
{
  "default_view": "series | year | indicator | country",
  "groups_order": ["OVERVIEW", "M", "P"],
  "series": {"<series key>": {"label": "Short name", "chart": "rank | dumbbell | distribution | categories | changes | stacked", "hidden": false}},
  "note": "Why these choices (shown on the page)",
  "created": "YYYY-MM-DD"
}
```

The file only changes presentation; the data files stay as the engine wrote them.

## Database

| Collection | Document | Written by | Rule |
|---|---|---|---|
| `approvals` | `<plan_id>`: `status` (approved, withdrawn), `plan_sha256`, `by_id`, `by_name`, `at`, `note` | The page, when the researcher presses Approve | read: viewers; write: owner |
| `events` | one per approval or withdrawal: `plan_id`, `kind`, `plan_sha256`, `by_name`, `at`, `note` | The page | read: viewers; write: owner |

Claude reads `approvals/<plan_id>`, saves it as JSON and runs `apply-approval`. The command refuses an approval given to an earlier version of the plan, a withdrawn approval, or a plan that does not validate as approved (for example with outstanding decisions).

## Updating the page

Publish `page/index.html` over the same artifact. Data files left out of a publish are kept, so a plan update sends only `data/projects.json` and `data/<plan_id>/*.json`, and a run adds its own folder.
