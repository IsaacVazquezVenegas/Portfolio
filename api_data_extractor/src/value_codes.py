"""Stage 4 check: how a source writes 'no observation'.

Lists every text a run's dataset holds where no numeric value exists, per source, and
proposes a meaning for the codes that look like missing-value markers. The researcher
confirms the classification, which is recorded in the plan (``missing_value_codes``);
the configuration then lists the ``no_data`` codes in ``processing.missing_values.markers``.

No LLM is needed: punctuation-only codes and common abbreviations are recognised here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Union

#: Codes made only of punctuation, dashes or spaces ('. . .', '..', '—', '-', ':').
_PUNCT_ONLY = re.compile(r"^[\s.\-–—:_/*x?]*$", re.IGNORECASE)
#: Abbreviations that mean 'no data' in official statistics.
_NO_DATA_WORDS = {"na", "n/a", "n.a.", "nd", "n.d.", "no data", "not available", "data not available",
                  "missing", "unknown", "not reported", "null", "none", "nan"}
_NOT_APPLICABLE = {"not applicable", "n.app.", "n/app", "not relevant"}


def suggest_meaning(code: str) -> str:
    text = code.strip().lower()
    if text in _NOT_APPLICABLE:
        return "not_applicable"
    if text in _NO_DATA_WORDS or _PUNCT_ONLY.match(text):
        return "no_data"
    return "category"


def inspect_value_codes(dataset_csv: Union[str, Path], max_examples: int = 3) -> List[Dict]:
    """Codes found where 'value' is empty, with counts, examples and a suggested meaning.

    Only codes suggested as ``no_data`` or ``not_applicable`` are listed one by one; ordinary
    categories (Yes/No, currencies, survey names...) are summarised per source.
    """
    import pandas as pd

    df = pd.read_csv(dataset_csv, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    text_only = df[df["value"] == ""]
    out: List[Dict] = []
    for source, g in text_only.groupby("source_id", sort=True):
        categories = 0
        for code, rows in g.groupby("value_text", sort=True):
            meaning = suggest_meaning(code) if code != "" else "no_data"
            if meaning == "category":
                categories += 1
                continue
            note = ("a dash alone often marks a follow-up question skipped after a 'No' (not applicable): "
                    "check it against the parent question") if re.fullmatch(r"\s*[-–—]\s*", code) else None
            out.append({
                "note": note,
                "source": source,
                "code": code,
                "suggested_meaning": meaning,
                "rows": int(len(rows)),
                "indicators": int(rows["indicator_code"].nunique()),
                "examples": sorted(rows["indicator_code"].unique())[:max_examples],
            })
        out.append({"source": source, "code": None, "suggested_meaning": "category", "distinct_codes": categories,
                    "rows": int((g["value_text"].map(suggest_meaning) == "category").sum())})
    return out


def plan_entries(found: List[Dict], run_id: str) -> List[Dict]:
    """The proposal as plan ``missing_value_codes`` entries (empty codes are left out: always missing)."""
    return [{"source": f["source"], "code": f["code"], "meaning": f["suggested_meaning"],
             "evidence": f"value-codes on run {run_id}: {f['rows']} row(s) in {f['indicators']} indicator(s)",
             **({"note": f["note"]} if f.get("note") else {})}
            for f in found if f.get("code")]
