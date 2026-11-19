"""World Bank Indicators API (v2) connector.

Documentation: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392
Base URL:      https://api.worldbank.org/v2

* Data:     ``{base}/country/{FRA;DEU|all}/indicator/{code}?format=json&date=2000:2020&per_page=1000&page=N``
* Metadata: ``{base}/indicator/{code}?format=json``

Responses are a two-element list ``[paging_metadata, records]``; errors are
returned as ``[{"message": [...]}]`` (sometimes with HTTP 200).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..api_client import ApiRequestError
from ..models import ExtractionTask, IndicatorSpec
from .base import BaseConnector, ConnectorError, logger, text_or_none, to_float

PER_PAGE = 1000


def _error_message(data: Any) -> Optional[str]:
    if isinstance(data, list) and data and isinstance(data[0], dict) and "message" in data[0]:
        msgs = data[0]["message"]
        return "; ".join(f"{m.get('key')}: {m.get('value')}" for m in msgs if isinstance(m, dict)) or "unknown error"
    return None


class WorldBankConnector(BaseConnector):
    connector_id = "world_bank"
    description = "World Bank Indicators API v2 (api.worldbank.org)"
    api_types = ("rest",)
    allowed_filter_keys = ("source",)
    max_pages = 1000

    def __init__(self, source, client):
        super().__init__(source, client)
        self._last_updated: Dict[str, str] = {}

    def fetch_observations(self, task: ExtractionTask, indicator: IndicatorSpec) -> List[Dict[str, Any]]:
        filters = self.merged_filters(task, indicator)
        observations: List[Dict[str, Any]] = []
        for batch in self.country_batches(task):
            countries = "all" if batch is None else ";".join(batch)
            url = f"{self.base_url}/country/{countries}/indicator/{indicator.code}"
            page, pages = 1, 1
            while page <= pages:
                if page > self.max_pages:
                    raise ConnectorError(f"more than {self.max_pages} pages for {indicator.code}; aborting")
                params: Dict[str, Any] = {
                    "format": "json",
                    "date": f"{task.start_year}:{task.end_year}",
                    "per_page": PER_PAGE,
                    "page": page,
                }
                if "source" in filters:
                    params["source"] = filters["source"]
                resp = self.client.get(url, params, purpose=f"data:{indicator.code}")
                error = _error_message(resp.data)
                if error:
                    raise ConnectorError(f"{resp.request_id}: World Bank API error for '{indicator.code}': {error}")
                if not isinstance(resp.data, list) or len(resp.data) < 1 or not isinstance(resp.data[0], dict):
                    raise ConnectorError(f"{resp.request_id}: unexpected World Bank response structure")
                meta = resp.data[0]
                records = resp.data[1] if len(resp.data) > 1 and resp.data[1] is not None else []
                pages = int(meta.get("pages") or 1)
                if meta.get("lastupdated"):
                    self._last_updated[indicator.code] = meta["lastupdated"]
                for rec in records:
                    observations.append(self._parse(rec, resp.request_id, resp.url))
                page += 1
        return observations

    @staticmethod
    def _parse(rec: Dict[str, Any], request_id: str, url: str) -> Dict[str, Any]:
        country = rec.get("country") or {}
        ind = rec.get("indicator") or {}
        value = rec.get("value")
        return {
            "indicator_code": ind.get("id"),
            "indicator_name": ind.get("value"),
            "country_code": rec.get("countryiso3code") or country.get("id"),
            "country_name": country.get("value"),
            "spatial_type": None,
            "period": text_or_none(rec.get("date")),
            "value": to_float(value),
            "value_text": text_or_none(value),
            "unit": rec.get("unit") or None,
            "comments": rec.get("obs_status") or None,
            "dimensions": {},
            "dimension_labels": {},
            "source_record_id": None,
            "source_updated": None,
            "request_id": request_id,
            "source_url": url,
        }

    def fetch_indicator_metadata(self, indicator: IndicatorSpec) -> Optional[Dict[str, Any]]:
        try:
            resp = self.client.get(
                f"{self.base_url}/indicator/{indicator.code}", {"format": "json"}, purpose=f"metadata:indicator:{indicator.code}"
            )
        except ApiRequestError as exc:
            logger.warning("could not retrieve World Bank metadata for %s: %s", indicator.code, exc)
            return None
        if _error_message(resp.data) or not isinstance(resp.data, list) or len(resp.data) < 2 or not resp.data[1]:
            return {"found": False}
        item = resp.data[1][0]
        return {
            "found": True,
            "name": item.get("name"),
            "definition": item.get("sourceNote"),
            "unit": item.get("unit") or None,
            "source_organization": item.get("sourceOrganization"),
            "database": (item.get("source") or {}).get("value"),
            "request_id": resp.request_id,
        }

    def source_metadata(self) -> Dict[str, Any]:
        return {"last_updated_by_indicator": dict(self._last_updated)} if self._last_updated else {}
