"""Export plans and run outputs as data files for the Data Fetcher page.

The Data Fetcher page (``page/index.html``) is a published artifact that shows
extraction projects. It reads plain files published beside it::

    data/projects.json                       one entry per project
    data/<plan_id>/plan.json                 the extraction plan, as written
    data/<plan_id>/meta.json                 plan fingerprint, warnings, export time
    data/<plan_id>/history.json              every change to the plan, newest first
    data/<plan_id>/runs/<run_id>/summary.json  coverage and distributions per series
    data/<plan_id>/runs/<run_id>/<outputs>   the run's files (Excel and raw data as parts the page reassembles)

``export_plan`` and ``export_run`` write into a local folder that mirrors the
page's files (``--site``). Everything here is plain structuring: no LLM call,
no network access. ``apply_approval`` copies an approval recorded on the page
into the plan file after checking that it approves this exact plan.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .validation import ConfigValidationError, load_plan, parse_plan, plan_warnings

PAGE_SCHEMA_VERSION = "1.0"

#: Run outputs published as they are (the page serves .csv, .json, .txt and .md only;
#: the configuration goes as .txt). dataset.json duplicates dataset.csv and is left out.
RUN_FILES = {
    "dataset.csv": "dataset.csv",
    "dataset_wide.csv": "dataset_wide.csv",
    "extraction_manifest.json": "extraction_manifest.json",
    "extraction_log.txt": "extraction_log.txt",
    "extraction_config.yaml": "extraction_config.yaml.txt",
    "README.md": "README.md",
}
#: Excel files cannot be served by the page: their sheets are exported as data and
#: the page rebuilds the workbook on download.
XLSX_FILES = ("dataset.xlsx", "data_dictionary.xlsx")
RAW_FILE = "raw_data.json"
#: Published files must stay under 16 MB; raw responses are split into parts below this.
MAX_PART_BYTES = 12_000_000
#: Sheets with more cells than this are written as CSV beside the workbook description.
INLINE_SHEET_CELLS = 20_000

#: Keys that identify list items, in order of preference, so that a change is
#: reported against "requirements_register[R5]" rather than a list position.
ITEM_KEYS = ("id", "indicator_code", "query", "code")
MAX_VALUE_CHARS = 300


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Plan fingerprint and approval
# ---------------------------------------------------------------------------

def plan_fingerprint(plan_data: dict) -> str:
    """SHA-256 of the plan without its approval block.

    An approval names this fingerprint, so any later change to the plan's
    content (but not the act of recording the approval) invalidates it.
    """
    content = {k: v for k, v in plan_data.items() if k != "approval"}
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ApprovalError(ValueError):
    pass


def apply_approval(plan_path: Union[str, Path], approval: dict) -> dict:
    """Record a page approval in the plan file.

    ``approval`` is the page's record: ``{"status": "approved", "plan_sha256": ...,
    "by_name": ..., "at": ISO time, "note": optional}``. The plan is rewritten
    only if the fingerprint matches and the approved plan still validates.
    Returns the new approval block.
    """
    plan_path = Path(plan_path)
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    if approval.get("status") != "approved":
        raise ApprovalError(f"the page record has status '{approval.get('status')}', not 'approved'")
    current = plan_fingerprint(data)
    if approval.get("plan_sha256") != current:
        raise ApprovalError(
            "the approval was given to another version of the plan "
            f"(approved {str(approval.get('plan_sha256'))[:12]}, current {current[:12]}); "
            "the researcher must approve the current version on the page"
        )
    name = (approval.get("by_name") or "").strip()
    if not name:
        raise ApprovalError("the approval record names no researcher")
    at = str(approval.get("at") or "")[:10] or date.today().isoformat()
    block = {"status": "approved", "approved_by": name, "approved_on": at}
    note = (approval.get("note") or "").strip()
    block["notes"] = (f"{note} " if note else "") + f"Approved on the Data Fetcher page (plan sha256 {current[:12]})."
    new = copy.deepcopy(data)
    new["approval"] = block
    try:
        parse_plan(new, origin=str(plan_path))
    except ConfigValidationError as exc:
        raise ApprovalError("the plan cannot be approved as it stands:\n  - " + "\n  - ".join(exc.errors)) from exc
    plan_path.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return block


# ---------------------------------------------------------------------------
# Plan history (structural diff)
# ---------------------------------------------------------------------------

def _item_key(item: Any) -> Optional[str]:
    if isinstance(item, dict):
        for k in ITEM_KEYS:
            if isinstance(item.get(k), str):
                return item[k]
    return None


def _short(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_VALUE_CHARS else text[: MAX_VALUE_CHARS - 1] + "…"


def diff_plans(old: Any, new: Any, path: str = "") -> List[dict]:
    """List of {path, change, before, after}: 'added', 'removed' or 'changed'."""
    out: List[dict] = []
    if isinstance(old, dict) and isinstance(new, dict):
        for k in list(old.keys()) + [k for k in new.keys() if k not in old]:
            sub = f"{path}.{k}" if path else k
            if k not in new:
                out.append({"path": sub, "change": "removed", "before": _short(old[k]), "after": None})
            elif k not in old:
                out.append({"path": sub, "change": "added", "before": None, "after": _short(new[k])})
            else:
                out.extend(diff_plans(old[k], new[k], sub))
        return out
    if isinstance(old, list) and isinstance(new, list):
        okeys = [_item_key(i) for i in old]
        nkeys = [_item_key(i) for i in new]
        keyed = all(okeys) and all(nkeys) and len(set(okeys)) == len(okeys) and len(set(nkeys)) == len(nkeys)
        if keyed:
            omap, nmap = dict(zip(okeys, old)), dict(zip(nkeys, new))
            for k in okeys + [k for k in nkeys if k not in omap]:
                sub = f"{path}[{k}]"
                if k not in nmap:
                    out.append({"path": sub, "change": "removed", "before": _short(omap[k]), "after": None})
                elif k not in omap:
                    out.append({"path": sub, "change": "added", "before": None, "after": _short(nmap[k])})
                else:
                    out.extend(diff_plans(omap[k], nmap[k], sub))
            return out
        if old != new:
            out.append({"path": path, "change": "changed", "before": _short(old), "after": _short(new)})
        return out
    if old != new:
        out.append({"path": path, "change": "changed", "before": _short(old), "after": _short(new)})
    return out


# ---------------------------------------------------------------------------
# Plan export
# ---------------------------------------------------------------------------

def _project_entry(projects: List[dict], plan_id: str) -> dict:
    for p in projects:
        if p.get("id") == plan_id:
            return p
    entry = {"id": plan_id, "runs": []}
    projects.append(entry)
    return entry


def export_plan(plan_path: Union[str, Path], site: Union[str, Path], note: Optional[str] = None,
                actor: str = "claude") -> dict:
    """Validate the plan and write it, its meta and its history into the site folder."""
    plan_path, site = Path(plan_path), Path(site)
    plan = load_plan(plan_path)  # raises ConfigValidationError when invalid
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    folder = site / "data" / plan.plan_id
    previous = _read_json(folder / "plan.json", None)
    history = _read_json(folder / "history.json", [])
    now = _now()
    fingerprint = plan_fingerprint(data)

    if previous is None:
        changes = [{"path": "", "change": "created", "before": None, "after": None}]
    else:
        changes = diff_plans(previous, data)
    if changes:
        history.insert(0, {"at": now, "actor": actor, "note": note, "plan_sha256": fingerprint,
                           "status": plan.approval.status, "changes": changes})

    warnings = plan_warnings(plan)
    meta = {
        "page_schema_version": PAGE_SCHEMA_VERSION,
        "plan_id": plan.plan_id,
        "plan_sha256": fingerprint,
        "exported_at": now if changes or previous is None else _read_json(folder / "meta.json", {}).get("exported_at", now),
        "source_file": plan_path.name,
        "warnings": warnings,
    }
    _write_json(folder / "plan.json", data)
    _write_json(folder / "meta.json", meta)
    _write_json(folder / "history.json", history)

    projects = _read_json(site / "data" / "projects.json", [])
    entry = _project_entry(projects, plan.plan_id)
    register = plan.scoping.requirements_register if plan.scoping else []
    counts: Dict[str, int] = {}
    for row in register:
        counts[row.status] = counts.get(row.status, 0) + 1
    entry.update({
        "objective": (plan.research.objective if plan.research else None) or (plan.scoping.purpose if plan.scoping else None),
        "status": plan.approval.status,
        "plan_sha256": fingerprint,
        "updated": meta["exported_at"],
        "register_counts": counts,
        "open_questions": len(plan.scoping.open_questions()) if plan.scoping else 0,
        "indicators": len(plan.indicator_mapping),
    })
    projects.sort(key=lambda p: p.get("updated") or "", reverse=True)
    _write_json(site / "data" / "projects.json", projects)
    return {"plan_id": plan.plan_id, "changes": len(changes), "plan_sha256": fingerprint, "warnings": warnings}


# ---------------------------------------------------------------------------
# Run export
# ---------------------------------------------------------------------------

def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except (TypeError, ValueError):
        return False


def _quantiles(values: List[float]) -> List[float]:
    import statistics

    v = sorted(values)
    if len(v) == 1:
        return [v[0]] * 5
    q = statistics.quantiles(v, n=4, method="inclusive")
    return [round(x, 4) for x in (v[0], q[0], q[1], q[2], v[-1])]


def summarise_dataset(csv_path: Union[str, Path]) -> Tuple[List[dict], Dict[str, str]]:
    """One summary per series (indicator code + dimensions) of a long dataset."""
    import pandas as pd

    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    countries = {}
    for code, name in zip(df["country_code"], df["country_name"]):
        if code and code not in countries:
            countries[code] = name or code
    series: List[dict] = []
    for (source, code, dims), g in df.groupby(["source_id", "indicator_code", "dimension_labels"], sort=True):
        first = g.iloc[0]
        years = sorted({int(y) for y in g["year"] if y.isdigit()})
        numeric = g["value"].map(lambda v: v != "" and _is_number(v))
        missing = g["missing"].str.lower() == "true"
        kind = "numeric" if numeric.mean() >= 0.5 else "categorical"
        coverage: Dict[str, str] = {}
        by_country = {c: grp for c, grp in g.groupby("country_code")}
        for c, grp in by_country.items():
            states = {}
            for y, miss in zip(grp["year"], grp["missing"].str.lower() == "true"):
                if y.isdigit():
                    states[int(y)] = "m" if miss and states.get(int(y)) != "v" else "v"
            coverage[c] = "".join(states.get(y, ".") for y in years)
        dist: Dict[str, Any] = {}
        if kind == "numeric":
            for y, grp in g[numeric].groupby("year"):
                dist[y] = _quantiles([float(v) for v in grp["value"]]) + [len(grp)]
            categories: List[str] = []
        else:
            cats = g.loc[~missing, "value_text"].replace("", "(blank)")
            order = cats.value_counts()
            categories = sorted(order.index, key=lambda c: (not _is_number(c), float(c) if _is_number(c) else 0, -order[c], c))
            for y, grp in g[~missing].groupby("year"):
                vc = grp["value_text"].replace("", "(blank)").value_counts()
                dist[y] = {k: int(n) for k, n in vc.items()}
        series.append({
            "key": f"{source}:{code}" + (f" [{dims}]" if dims else ""),
            "source_id": source,
            "indicator_code": code,
            "indicator_name": first["indicator_name"],
            "category": first["category"],
            "dimensions": dims,
            "unit": first["unit"],
            "kind": kind,
            "ordinal": kind == "categorical" and all(_is_number(c) for c in categories if c != "Not applicable"),
            "observations": int(len(g)),
            "missing": int(missing.sum()),
            "countries": len(by_country),
            "years": years,
            "coverage": coverage,
            "categories": categories,
            "distribution": dist,
        })
    return series, countries


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def _export_workbook(path: Path, target: Path) -> str:
    """Describe a workbook as JSON (small sheets inline, large ones as CSV) for the page to rebuild."""
    import csv

    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    sheets = []
    for ws in wb.worksheets:
        rows = [[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)]
        width = max((len(r) for r in rows), default=0)
        if len(rows) * width <= INLINE_SHEET_CELLS:
            sheets.append({"name": ws.title, "rows": rows})
            continue
        types = []
        for c in range(width):
            body = [r[c] for r in rows[1:] if c < len(r) and r[c] != ""]
            types.append("n" if body and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in body) else "s")
        csv_name = f"{path.stem}__{ws.title}.csv"
        with open(target / csv_name, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(rows)
        sheets.append({"name": ws.title, "csv": csv_name, "types": types})
    wb.close()
    spec = f"{path.name}.json"
    _write_json(target / spec, {"workbook": path.name, "sheets": sheets})
    return spec


def _split_raw(path: Path, target: Path) -> List[str]:
    """Split the raw response log (a JSON list) into parts the page can serve."""
    records = json.loads(path.read_text(encoding="utf-8"))
    parts, chunk, size = [], [], 2
    def flush():
        name = f"raw_data.part{len(parts) + 1}.json"
        (target / name).write_text(json.dumps(chunk, ensure_ascii=False), encoding="utf-8")
        parts.append(name)
    for rec in records:
        n = len(json.dumps(rec, ensure_ascii=False).encode("utf-8")) + 1
        if n > MAX_PART_BYTES:
            raise ValueError(f"one raw response ({rec.get('request_id', '?')}) is {n / 1e6:.1f} MB: too large for the page")
        if chunk and size + n > MAX_PART_BYTES:
            flush(); chunk, size = [], 2
        chunk.append(rec); size += n
    if chunk or not parts:
        flush()
    return parts


def export_run(run_dir: Union[str, Path], site: Union[str, Path], plan_id: Optional[str] = None) -> dict:
    """Copy a run's outputs into the site folder and write its summary."""
    run_dir, site = Path(run_dir), Path(site)
    manifest = _read_json(run_dir / "extraction_manifest.json", None)
    if manifest is None:
        raise FileNotFoundError(f"{run_dir} holds no extraction_manifest.json: is it an output folder?")
    run_id = run_dir.name
    plan_id = plan_id or _read_json(run_dir / "extraction_plan.json", {}).get("plan_id") or manifest.get("project")
    target = site / "data" / plan_id / "runs" / run_id
    target.mkdir(parents=True, exist_ok=True)

    files = []
    for name, published in RUN_FILES.items():
        if (run_dir / name).exists():
            shutil.copy2(run_dir / name, target / published)
            files.append({"name": name, "bytes": (run_dir / name).stat().st_size, "path": published})
    for name in XLSX_FILES:
        if (run_dir / name).exists():
            files.append({"name": name, "bytes": (run_dir / name).stat().st_size, "build": "xlsx",
                          "path": _export_workbook(run_dir / name, target)})
    if (run_dir / RAW_FILE).exists():
        files.append({"name": RAW_FILE, "bytes": (run_dir / RAW_FILE).stat().st_size, "build": "join",
                      "parts": _split_raw(run_dir / RAW_FILE, target)})

    series, countries = summarise_dataset(run_dir / "dataset.csv") if (run_dir / "dataset.csv").exists() else ([], {})
    approval = (manifest.get("configuration") or {}).get("plan_approval") or {}
    summary = {
        "page_schema_version": PAGE_SCHEMA_VERSION,
        "run_id": run_id,
        "plan_id": plan_id,
        "status": manifest.get("status"),
        "started_at": (manifest.get("execution") or {}).get("started_at"),
        "finished_at": (manifest.get("execution") or {}).get("finished_at"),
        "rows": sum(s["observations"] for s in series),
        "plan_approval": approval,
        "test_run": approval.get("status") != "approved",
        "config_sha256": (manifest.get("configuration") or {}).get("sha256"),
        "requests": len(manifest.get("requests") or []),
        "errors": manifest.get("errors") or [],
        "warnings": manifest.get("warnings") or [],
        "files": files,
        "countries": countries,
        "series": series,
    }
    _write_json(target / "summary.json", summary)

    projects = _read_json(site / "data" / "projects.json", [])
    entry = _project_entry(projects, plan_id)
    runs = [r for r in entry.get("runs", []) if r.get("run_id") != run_id]
    runs.append({"run_id": run_id, "status": summary["status"], "rows": summary["rows"],
                 "finished_at": summary["finished_at"], "test_run": summary["test_run"]})
    entry["runs"] = sorted(runs, key=lambda r: r.get("finished_at") or "", reverse=True)
    entry["updated"] = _now()
    projects.sort(key=lambda p: p.get("updated") or "", reverse=True)
    _write_json(site / "data" / "projects.json", projects)

    folder = site / "data" / plan_id
    history = _read_json(folder / "history.json", [])
    history.insert(0, {"at": _now(), "actor": "engine", "note": f"Run {run_id}: {summary['status']}, "
                       f"{summary['rows']} observation(s)" + (" (test run, plan not approved)" if summary["test_run"] else ""),
                       "plan_sha256": None, "status": None, "changes": []})
    _write_json(folder / "history.json", history)
    return {"plan_id": plan_id, "run_id": run_id, "rows": summary["rows"], "series": len(series),
            "files": files, "folder": str(target)}
