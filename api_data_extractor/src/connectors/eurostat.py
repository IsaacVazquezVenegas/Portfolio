"""Eurostat dissemination API (Statistics API 1.0, JSON-stat 2.0) connector.

Documentation: https://wikis.ec.europa.eu/display/EUROSTATHELP/API+Statistics+-+data+query
Base URL:      https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0

* Data: ``{base}/data/{dataset}?geo=FR&geo=DE&sinceTimePeriod=2014&untilTimePeriod=2019&sex=T&lang=EN``

In this connector the *indicator code is the Eurostat dataset code* and the
filters select one slice of the dataset (e.g. ``unit: PC``, ``sex: T``).
Country codes are Eurostat geo codes (ISO 3166 alpha-2 except ``EL`` for
Greece and ``UK``); they are harmonised to ISO3 during processing.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..api_client import ApiRequestError
from ..models import ExtractionTask, IndicatorSpec, Source
from .base import BaseConnector, ConnectorError, as_list, logger

_SAFE_KEY = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_RESERVED = {"geo", "time", "sinceTimePeriod", "untilTimePeriod", "lastTimePeriod", "lang", "format"}


def _category_codes(dimension: Dict[str, Any]) -> List[str]:
    index = dimension.get("category", {}).get("index", {})
    if isinstance(index, list):
        return list(index)
    return [code for code, _ in sorted(index.items(), key=lambda kv: kv[1])]


def decode_jsonstat(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Expand a JSON-stat 2.0 dataset into one dict per populated cell.

    Each dict has ``codes`` and ``labels`` (dimension id -> category), ``value``
    and ``status``. Cells with neither value nor status are not returned.
    """
    ids: List[str] = data.get("id") or []
    sizes: List[int] = data.get("size") or []
    if len(ids) != len(sizes):
        raise ConnectorError("JSON-stat response has inconsistent 'id' and 'size'")
    if any(s == 0 for s in sizes):
        return []
    dims = data.get("dimension") or {}
    codes = {d: _category_codes(dims.get(d, {})) for d in ids}
    labels = {d: dims.get(d, {}).get("category", {}).get("label", {}) for d in ids}
    for d, size in zip(ids, sizes):
        if len(codes[d]) != size:
            raise ConnectorError(f"JSON-stat dimension '{d}' has {len(codes[d])} categories but size {size}")

    values = data.get("value")
    status = data.get("status") or {}
    if isinstance(values, list):
        value_map = {i: v for i, v in enumerate(values) if v is not None}
    else:
        value_map = {int(k): v for k, v in (values or {}).items()}
    if isinstance(status, list):
        status_map = {i: s for i, s in enumerate(status) if s}
    else:
        status_map = {int(k): s for k, s in status.items()}

    strides = []
    acc = 1
    for size in reversed(sizes):
        strides.insert(0, acc)
        acc *= size

    cells = []
    for flat in sorted(set(value_map) | set(status_map)):
        cell_codes, cell_labels = {}, {}
        for d, size, stride in zip(ids, sizes, strides):
            pos = (flat // stride) % size
            code = codes[d][pos]
            cell_codes[d] = code
            cell_labels[d] = labels[d].get(code)
        cells.append({"index": flat, "codes": cell_codes, "labels": cell_labels,
                      "value": value_map.get(flat), "status": status_map.get(flat)})
    return cells


class EurostatConnector(BaseConnector):
    connector_id = "eurostat"
    description = "Eurostat Statistics API 1.0 (JSON-stat 2.0)"
    api_types = ("json-stat",)

    @classmethod
    def check_task(cls, source: Source, task: ExtractionTask) -> List[str]:
        errors = super().check_task(source, task)
        keys = set(task.filters) | {k for ind in task.indicators for k in ind.filters}
        for key in sorted(keys):
            if key in _RESERVED:
                errors.append(f"extraction '{task.id}': filter '{key}' is set by the engine (use countries/start_year/end_year)")
            elif not _SAFE_KEY.match(key):
                errors.append(f"extraction '{task.id}': invalid Eurostat dimension name '{key}'")
        return errors

    def fetch_observations(self, task: ExtractionTask, indicator: IndicatorSpec) -> List[Dict[str, Any]]:
        observations: List[Dict[str, Any]] = []
        filters = self.merged_filters(task, indicator)
        for batch in self.country_batches(task):
            params: Dict[str, Any] = {
                "lang": "EN",
                "sinceTimePeriod": str(task.start_year),
                "untilTimePeriod": str(task.end_year),
            }
            if batch:
                params["geo"] = list(batch)
            for key in sorted(filters):
                params[key] = [str(v) for v in as_list(filters[key])]
            try:
                resp = self.client.get(f"{self.base_url}/data/{indicator.code}", params, purpose=f"data:{indicator.code}")
            except ApiRequestError as exc:
                if exc.status_code == 404:
                    raise ConnectorError(f"Eurostat dataset '{indicator.code}' is not available (HTTP 404)") from exc
                raise
            data = resp.data
            if not isinstance(data, dict) or data.get("class") != "dataset":
                raise ConnectorError(f"{resp.request_id}: unexpected Eurostat response (not a JSON-stat dataset)")
            empty_dims = [d for d, s in zip(data.get("id", []), data.get("size", [])) if s == 0]
            if empty_dims:
                logger.warning(
                    "%s: Eurostat returned no categories for dimension(s) %s - check the filter codes",
                    resp.request_id, empty_dims,
                )
            for cell in decode_jsonstat(data):
                observations.append(self._to_observation(cell, indicator, data, resp.request_id, resp.url))
        return observations

    @staticmethod
    def _to_observation(cell, indicator, data, request_id, url) -> Dict[str, Any]:
        codes, labels = cell["codes"], cell["labels"]
        dims = {k: v for k, v in codes.items() if k not in ("geo", "time")}
        value = cell["value"]
        return {
            "indicator_code": indicator.code,
            "indicator_name": data.get("label"),
            "country_code": codes.get("geo"),
            "country_name": labels.get("geo"),
            "spatial_type": None,
            "period": codes.get("time"),
            "value": float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None,
            "value_text": None if value is None else str(value),
            "unit": labels.get("unit") or codes.get("unit"),
            "comments": f"Eurostat flag '{cell['status']}'" if cell["status"] else None,
            "dimensions": dims,
            "dimension_labels": {k: labels.get(k) for k in dims},
            "source_record_id": str(cell["index"]),
            "source_updated": data.get("updated"),
            "request_id": request_id,
            "source_url": url,
        }

    def fetch_indicator_metadata(self, indicator: IndicatorSpec) -> Optional[Dict[str, Any]]:
        # The dataset label is part of every data response; no separate call needed.
        return None
