"""Writing the output package (CSV, Excel, JSON)."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

EXCEL_MAX_CELL = 32767


def _cell(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and math.isinf(value):
        return str(value)
    if hasattr(value, "item"):  # numpy scalar
        value = value.item()
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        # Control characters (e.g. in WHO survey titles) are not allowed in worksheets;
        # the CSV and JSON outputs keep the text exactly as published.
        value = ILLEGAL_CHARACTERS_RE.sub("", value)
    if isinstance(value, str) and len(value) > EXCEL_MAX_CELL:
        value = value[: EXCEL_MAX_CELL - 20] + " ...[truncated]"
    return value


def add_sheet(wb: Workbook, title: str, df: pd.DataFrame) -> None:
    ws = wb.create_sheet(title=title[:31])
    ws.append([str(c) for c in df.columns])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in df.itertuples(index=False, name=None):
        ws.append([_cell(v) for v in row])
    # Text that looks like a formula (e.g. "=1+1") must stay text: no formula injection.
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.data_type = "s"
    ws.freeze_panes = "A2"
    for idx, col in enumerate(df.columns, start=1):
        sample = [len(str(col))] + [len(str(v)) for v in df[col].head(200) if v is not None]
        ws.column_dimensions[get_column_letter(idx)].width = min(max(sample) + 2, 60)


def write_workbook(path: Path, sheets: Dict[str, pd.DataFrame]) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    for title, df in sheets.items():
        add_sheet(wb, title, df)
    wb.save(path)
    return path


def write_csv(path: Path, df: pd.DataFrame) -> Path:
    # utf-8-sig so that Excel on Windows opens accented country names correctly.
    df.to_csv(path, index=False, encoding="utf-8-sig", lineterminator="\n")
    return path


def write_json(path: Path, data: Any) -> Path:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def describe_files(folder: Path, names: Sequence[str]) -> List[Dict[str, Any]]:
    out = []
    for name in names:
        p = folder / name
        if p.is_file():
            out.append({"file": name, "bytes": p.stat().st_size, "sha256": sha256_file(p)})
    return out


def records_frame(records: List[Dict[str, Any]], columns: Optional[List[str]] = None) -> pd.DataFrame:
    return pd.DataFrame(records, columns=columns)
