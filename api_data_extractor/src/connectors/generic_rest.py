"""Generic REST/JSON connector.

Covers simple documented APIs whose responses contain a list of records.
Everything is declared in the configuration:

* ``endpoint``: template relative to ``base_url``; placeholders
  ``{indicator}``, ``{countries}``, ``{country}``, ``{start_year}``, ``{end_year}``.
* ``extraction.query_params``: query parameters (same placeholders).
* ``extraction.filters``: extra query parameters passed verbatim.
* ``response.records_path`` / ``response.fields``: where records and
  standard fields are found (dotted paths).
* ``pagination``: none, page, offset or next_link.

If ``{country}`` is used, one request is issued per country; if
``{countries}`` is used, countries are joined with
``response.country_separator`` in batches of ``country_batch_size``.
Placeholders are filled by plain string substitution — nothing is evaluated.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin, urlparse

from ..models import ExtractionTask, IndicatorSpec, Source, template_placeholders
from .base import BaseConnector, ConnectorError, as_list, get_path, text_or_none, to_float

_PLACEHOLDER = re.compile(r"\{(indicator|countries|country|start_year|end_year)\}")


def fill_template(template: str, values: Dict[str, str], *, url_path: bool = False) -> str:
    def repl(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in values or values[key] is None:
            raise ConnectorError(f"placeholder '{{{key}}}' has no value for this request")
        value = str(values[key])
        return quote(value, safe=";,:") if url_path else value

    return _PLACEHOLDER.sub(repl, template)


class GenericRestConnector(BaseConnector):
    connector_id = "generic_rest"
    description = "Configurable REST/JSON connector (records list + field mapping)"
    api_types = ("rest", "odata")

    @classmethod
    def check_task(cls, source: Source, task: ExtractionTask) -> List[str]:
        errors = super().check_task(source, task)
        used = template_placeholders(source.endpoint or "")
        for value in task.query_params.values():
            if isinstance(value, str):
                used |= template_placeholders(value)
        if task.countries == "all" and used & {"country", "countries"}:
            errors.append(f"extraction '{task.id}': countries 'all' cannot fill a {{country}}/{{countries}} placeholder")
        if task.countries != "all" and not used & {"country", "countries"}:
            errors.append(
                f"extraction '{task.id}': a country list is given but neither the endpoint nor query_params "
                "use {country} or {countries}, so it would not be sent to the API"
            )
        if "country" in used and "countries" in used:
            errors.append(f"extraction '{task.id}': use either {{country}} or {{countries}}, not both")
        return errors

    def _country_groups(self, task: ExtractionTask) -> List[Dict[str, Optional[str]]]:
        used = template_placeholders(self.source.endpoint or "")
        for v in task.query_params.values():
            if isinstance(v, str):
                used |= template_placeholders(v)
        if task.countries == "all":
            return [{"country": None, "countries": None}]
        if "country" in used:
            return [{"country": c, "countries": None} for c in task.countries]
        sep = self.source.response.country_separator
        return [{"country": None, "countries": sep.join(b)} for b in self.country_batches(task)]

    def fetch_observations(self, task: ExtractionTask, indicator: IndicatorSpec) -> List[Dict[str, Any]]:
        pagination = self.source.pagination
        mapping = self.source.response
        observations: List[Dict[str, Any]] = []
        for group in self._country_groups(task):
            values = {
                "indicator": indicator.code,
                "start_year": str(task.start_year),
                "end_year": str(task.end_year),
                **group,
            }
            path = fill_template(self.source.endpoint or "", values, url_path=True)
            base_params: Dict[str, Any] = {}
            for key, val in task.query_params.items():
                base_params[key] = fill_template(val, values) if isinstance(val, str) else val
            for key, val in self.merged_filters(task, indicator).items():
                base_params[key] = [str(v) for v in as_list(val)] if isinstance(val, list) else val

            url: Optional[str] = urljoin(self.base_url + "/", path.lstrip("/"))
            page_number = pagination.first_page
            offset = 0
            pages = 0
            while url:
                pages += 1
                if pages > pagination.max_pages:
                    raise ConnectorError(f"pagination exceeded max_pages ({pagination.max_pages}) for {indicator.code}")
                params = dict(base_params)
                if pagination.type == "page":
                    params[pagination.page_param] = page_number
                    if pagination.page_size_param and pagination.page_size:
                        params[pagination.page_size_param] = pagination.page_size
                elif pagination.type == "offset":
                    params[pagination.offset_param] = offset
                    params[pagination.limit_param] = pagination.page_size
                resp = self.client.get(url, params if pages == 1 or pagination.type != "next_link" else None,
                                       purpose=f"data:{indicator.code}")
                records = get_path(resp.data, mapping.records_path)
                if records is None:
                    records = []
                if not isinstance(records, list):
                    raise ConnectorError(f"{resp.request_id}: records_path '{mapping.records_path}' is not a list")
                for rec in records:
                    observations.append(self._parse(rec, indicator, resp.request_id, resp.url))

                # next page
                url = self._next(resp, records, page_number, url)
                page_number += 1
                offset += pagination.page_size or 0
        return observations

    def _next(self, resp, records, page_number, current_url) -> Optional[str]:
        p = self.source.pagination
        if p.type == "none" or not records:
            return None
        if p.type == "page":
            if p.total_pages_path:
                total = get_path(resp.data, p.total_pages_path)
                try:
                    return current_url if page_number < int(total) else None
                except (TypeError, ValueError):
                    raise ConnectorError(f"{resp.request_id}: total_pages_path did not return a number") from None
            return current_url if p.page_size and len(records) >= p.page_size else None
        if p.type == "offset":
            return current_url if len(records) >= (p.page_size or 0) else None
        nxt = get_path(resp.data, p.next_link_path)
        if not nxt:
            return None
        nxt = urljoin(current_url, str(nxt))
        if urlparse(nxt).netloc != urlparse(self.base_url).netloc:
            raise ConnectorError(f"refusing to follow next link to a different host: {nxt}")
        return nxt

    def _parse(self, rec: Any, indicator: IndicatorSpec, request_id: str, url: str) -> Dict[str, Any]:
        fields = self.source.response.fields

        def field(name):
            return get_path(rec, fields[name]) if name in fields else None

        raw_value = field("value")
        value_text = field("value_text")
        return {
            "indicator_code": text_or_none(field("indicator_code")) or indicator.code,
            "indicator_name": text_or_none(field("indicator_name")),
            "country_code": text_or_none(field("country_code")),
            "country_name": text_or_none(field("country_name")),
            "spatial_type": None,
            "period": text_or_none(field("period")),
            "value": to_float(raw_value),
            "value_text": text_or_none(value_text if value_text is not None else raw_value),
            "low": to_float(field("low")),
            "high": to_float(field("high")),
            "unit": text_or_none(field("unit")),
            "comments": text_or_none(field("comments")),
            "dimensions": {},
            "dimension_labels": {},
            "source_record_id": text_or_none(field("source_record_id")),
            "source_updated": None,
            "request_id": request_id,
            "source_url": url,
        }
