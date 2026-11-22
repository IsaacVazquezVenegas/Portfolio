"""Export plans and run outputs as data files for the Data Fetcher page.

The Data Fetcher page (``page/index.html``) is a published artifact that shows
extraction projects. It reads plain files published beside it::

    data/projects.json                       one entry per project
    data/<plan_id>/plan.json                 the extraction plan, as written
    data/<plan_id>/meta.json                 plan fingerprint, warnings, export time
    data/<plan_id>/history.json              every change to the plan, newest first
    data/<plan_id>/extraction_config.yaml.txt  the configuration beside the plan, if any
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
#: CSV files above MAX_CSV_PART_BYTES are published as parts the page joins back together.
CSV_FILES = ("dataset.csv", "dataset_wide.csv")
RUN_FILES = {
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
#: Same limit for CSV files (dataset.csv, dataset_wide.csv, large workbook sheets).
MAX_CSV_PART_BYTES = MAX_PART_BYTES
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
    config = plan_path.parent / "extraction_config.yaml"
    if config.exists():  # kept beside the plan so a later session can restore the project
        shutil.copy2(config, folder / "extraction_config.yaml.txt")
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


def run_labels(run_dir: Union[str, Path]) -> Dict[Tuple[str, str], str]:
    """Short indicator labels from a run's configuration ((source, code) -> label or name)."""
    import yaml

    path = Path(run_dir) / "extraction_config.yaml"
    if not path.exists():
        return {}
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    out: Dict[Tuple[str, str], str] = {}
    for task in cfg.get("extractions") or []:
        for ind in task.get("indicators") or []:
            label = ind.get("label") or ind.get("name")
            if label and ind.get("code"):
                out[(task.get("source"), ind["code"])] = label
    return out


def summarise_dataset(csv_path: Union[str, Path], labels: Optional[Dict[Tuple[str, str], str]] = None
                      ) -> Tuple[List[dict], Dict[str, str]]:
    """One summary per series (indicator code + dimensions) of a long dataset."""
    import pandas as pd

    from .transformation import series_label

    labels = labels or {}

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
            "label": series_label(labels.get((source, code)) or first["indicator_name"] or code, dims),
            "group": first["category"] or "Other",
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


def build_views(csv_path: Union[str, Path], series: List[dict], target: Path,
                order: Optional[List[Tuple[str, str]]] = None) -> str:
    """Write the values the page's views need (by year, by indicator, by country) and return the index file.

    ``views.json`` lists years, countries, groups and series; ``views.partN.json`` hold the cells,
    split below MAX_PART_BYTES. A cell is a number, a text category, ``{"m": text}`` when the source
    reported no value, or null when the source has no row.
    """
    import pandas as pd

    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df = df[df["year"].str.isdigit()]
    years = sorted({int(y) for y in df["year"]})
    yi = {y: i for i, y in enumerate(years)}
    countries = {}
    for c, n in zip(df["country_code"], df["country_name"]):
        countries.setdefault(c, n or c)
    keys = {(s["source_id"], s["indicator_code"], s["dimensions"]): s for s in series}
    cells: Dict[str, Dict[str, list]] = {}
    for (source, code, dims), g in df.groupby(["source_id", "indicator_code", "dimension_labels"], sort=True):
        s = keys.get((source, code, dims))
        if s is None:
            continue
        numeric = s["kind"] == "numeric"
        out: Dict[str, list] = {}
        for c, y, v, t, miss in zip(g["country_code"], g["year"], g["value"], g["value_text"], g["missing"]):
            row = out.setdefault(c, [None] * len(years))
            if miss.lower() == "true":
                cell: Any = {"m": t}
            elif numeric and v != "" and _is_number(v):
                cell = float(v)
            else:
                cell = t
            i = yi[int(y)]
            if row[i] is None or isinstance(row[i], dict):
                row[i] = cell
        cells[s["key"]] = out
    parts, chunk, size = [], {}, 2
    part_of: Dict[str, int] = {}

    def flush():
        name = f"views.part{len(parts) + 1}.json"
        (target / name).write_text(json.dumps(chunk, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        parts.append(name)
    for key, data in cells.items():
        n = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + len(key) + 8
        if chunk and size + n > MAX_PART_BYTES:
            flush(); chunk, size = {}, 2
        chunk[key] = data; size += n
        part_of[key] = len(parts)
    if chunk or not parts:
        flush()
    # Groups follow the order of the configuration's indicators (the plan's order), else alphabetical.
    rank = {k: i for i, k in enumerate(order or [])}
    first: Dict[str, int] = {}
    for s in series:
        r = rank.get((s["source_id"], s["indicator_code"]), len(rank))
        first[s["group"]] = min(first.get(s["group"], r), r)
    groups = sorted(first, key=lambda g: (first[g], g))
    series = sorted(series, key=lambda s: (groups.index(s["group"]), rank.get((s["source_id"], s["indicator_code"]), len(rank)), s["key"]))
    index = {"years": years, "countries": countries, "groups": groups, "parts": parts,
             "series": [{"key": s["key"], "label": s["label"], "group": s["group"], "code": s["indicator_code"],
                         "kind": s["kind"], "ordinal": s["ordinal"], "unit": s["unit"], "categories": s["categories"],
                         "part": part_of.get(s["key"], 0)} for s in series]}
    _write_json(target / "views.json", index)
    return "views.json"


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
        parts = _split_csv(target / csv_name, target)
        if parts == [csv_name]:
            sheets.append({"name": ws.title, "csv": csv_name, "types": types})
        else:
            (target / csv_name).unlink()
            sheets.append({"name": ws.title, "csv_parts": parts, "types": types})
    wb.close()
    spec = f"{path.name}.json"
    _write_json(target / spec, {"workbook": path.name, "sheets": sheets})
    return spec


def _split_csv(path: Path, target: Path) -> List[str]:
    """Publish a CSV as it is, or as parts below MAX_CSV_PART_BYTES when it is larger.

    Each part repeats the header row; rows are never cut. Only the first part keeps
    a byte-order mark, so that joining the parts (dropping the headers of parts 2+)
    gives back the original file.
    """
    import csv
    import io

    if path.stat().st_size <= MAX_CSV_PART_BYTES:
        if path.parent != target:
            shutil.copy2(path, target / path.name)
        return [path.name]
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8") if bom else raw.decode("utf-8")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader)

    def line(row: List[str]) -> bytes:
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\r\n" if "\r\n" in text[:4096] else "\n").writerow(row)
        return buf.getvalue().encode("utf-8")

    head = line(header)
    parts: List[str] = []
    chunk: List[bytes] = []
    size = len(head)

    def flush() -> None:
        name = f"{path.stem}.part{len(parts) + 1}.csv"
        prefix = b"\xef\xbb\xbf" if bom and not parts else b""
        (target / name).write_bytes(prefix + head + b"".join(chunk))
        parts.append(name)

    for row in reader:
        b = line(row)
        if len(head) + len(b) > MAX_CSV_PART_BYTES:
            raise ValueError(f"one row of {path.name} is {len(b) / 1e6:.1f} MB: too large for the page")
        if chunk and size + len(b) > MAX_CSV_PART_BYTES:
            flush(); chunk, size = [], len(head)
        chunk.append(b); size += len(b)
    if chunk or not parts:
        flush()
    return parts


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
    for name in CSV_FILES:
        if (run_dir / name).exists():
            parts = _split_csv(run_dir / name, target)
            entry = {"name": name, "bytes": (run_dir / name).stat().st_size}
            entry.update({"path": parts[0]} if parts == [name] else {"build": "join_csv", "parts": parts})
            files.append(entry)
    for name in XLSX_FILES:
        if (run_dir / name).exists():
            files.append({"name": name, "bytes": (run_dir / name).stat().st_size, "build": "xlsx",
                          "path": _export_workbook(run_dir / name, target)})
    if (run_dir / RAW_FILE).exists():
        files.append({"name": RAW_FILE, "bytes": (run_dir / RAW_FILE).stat().st_size, "build": "join",
                      "parts": _split_raw(run_dir / RAW_FILE, target)})

    labels = run_labels(run_dir)
    series, countries = (summarise_dataset(run_dir / "dataset.csv", labels)
                         if (run_dir / "dataset.csv").exists() else ([], {}))
    views = build_views(run_dir / "dataset.csv", series, target, order=list(labels)) if series else None
    if (run_dir / "view_spec.json").exists():  # views adjusted by Claude on request
        shutil.copy2(run_dir / "view_spec.json", target / "view_spec.json")
    approval = (manifest.get("configuration") or {}).get("plan_approval") or {}
    summary = {
        "page_schema_version": PAGE_SCHEMA_VERSION,
        "run_id": run_id,
        "plan_id": plan_id,
        "views": views,
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
            "files": files, "views": summary.get("views"), "folder": str(target)}
