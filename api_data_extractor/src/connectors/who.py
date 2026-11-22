"""WHO Global Health Observatory (GHO) OData API connector.

Documentation: https://www.who.int/data/gho/info/gho-odata-api
Base URL:      https://ghoapi.azureedge.net/api

* Data:        ``{base}/{IndicatorCode}?$filter=...``
* Metadata:    ``{base}/Indicator?$filter=IndicatorCode eq '...'``
* Dimensions:  ``{base}/DIMENSION/{TYPE}/DimensionValues``

Observations carry the textual ``Value`` (e.g. ``"30.9 [24.9-36.9]"`` or a
policy category such as ``"4"``/``"Not applicable"``) and, for genuinely
numeric indicators, ``NumericValue``. Only ``NumericValue`` populates the
numeric ``value`` column: categorical policy scores stay as text.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ..api_client import ApiRequestError
from ..models import ExtractionTask, IndicatorSpec
from .base import BaseConnector, ConnectorError, as_list, logger, text_or_none, to_float

_SAFE_DIMENSION = re.compile(r"^[A-Z0-9_]{1,64}$")


def odata_literal(value: Any) -> str:
    if isinstance(value, bool):
        raise ConnectorError("boolean filter values are not supported")
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def odata_condition(field: str, value: Any) -> str:
    values = as_list(value)
    if len(values) == 1:
        return f"{field} eq {odata_literal(values[0])}"
    return f"{field} in ({','.join(odata_literal(v) for v in values)})"


class WhoGhoConnector(BaseConnector):
    connector_id = "who_gho"
    description = "WHO Global Health Observatory OData API (ghoapi.azureedge.net)"
    api_types = ("odata",)
    allowed_filter_keys = (
        "SpatialDimType",
        "ParentLocationCode",
        "TimeDimType",
        "Dim1Type",
        "Dim1",
        "Dim2Type",
        "Dim2",
        "Dim3Type",
        "Dim3",
        "DataSourceDimType",
        "DataSourceDim",
    )
    max_pages = 1000

    def __init__(self, source, client):
        super().__init__(source, client)
        self._dimension_labels: Dict[str, Dict[str, str]] = {}

    # -- query building ---------------------------------------------------------

    def build_filter(self, task: ExtractionTask, indicator: IndicatorSpec, countries: Optional[List[str]]) -> str:
        filters = self.merged_filters(task, indicator)
        filters.setdefault("SpatialDimType", "COUNTRY")
        parts = []
        if countries:
            parts.append(odata_condition("SpatialDim", countries))
        parts.append(f"TimeDim ge {int(task.start_year)}")
        parts.append(f"TimeDim le {int(task.end_year)}")
        for key in sorted(filters):
            parts.append(odata_condition(key, filters[key]))
        return " and ".join(parts)

    # -- data -------------------------------------------------------------------

    def fetch_observations(self, task: ExtractionTask, indicator: IndicatorSpec) -> List[Dict[str, Any]]:
        observations: List[Dict[str, Any]] = []
        for batch in self.country_batches(task):
            url: Optional[str] = f"{self.base_url}/{indicator.code}"
            params: Optional[Dict[str, Any]] = {"$filter": self.build_filter(task, indicator, batch)}
            pages = 0
            while url:
                pages += 1
                if pages > self.max_pages:
                    raise ConnectorError(f"more than {self.max_pages} pages for {indicator.code}; aborting")
                try:
                    resp = self.client.get(url, params, purpose=f"data:{indicator.code}")
                except ApiRequestError as exc:
                    if exc.status_code == 404:
                        raise ConnectorError(
                            f"indicator '{indicator.code}' was not found in the GHO API (HTTP 404)"
                        ) from exc
                    raise
                data = resp.data
                if not isinstance(data, dict) or not isinstance(data.get("value"), list):
                    raise ConnectorError(f"{resp.request_id}: unexpected GHO response structure (no 'value' list)")
                for record in data["value"]:
                    observations.append(self._parse_record(record, resp.request_id, resp.url))
                next_link = data.get("@odata.nextLink")
                if next_link and urlparse(next_link).netloc != urlparse(self.base_url).netloc:
                    raise ConnectorError(f"refusing to follow nextLink to a different host: {next_link}")
                url, params = (next_link, None) if next_link else (None, None)
        self._attach_labels(observations)
        return observations

    def _parse_record(self, rec: Dict[str, Any], request_id: str, url: str) -> Dict[str, Any]:
        dimensions = {}
        for n in ("1", "2", "3"):
            dim_type, dim_value = rec.get(f"Dim{n}Type"), rec.get(f"Dim{n}")
            if dim_type and dim_value is not None:
                dimensions[dim_type] = dim_value
        if rec.get("DataSourceDimType") and rec.get("DataSourceDim") is not None:
            dimensions[rec["DataSourceDimType"]] = rec["DataSourceDim"]
        period = rec.get("TimeDimensionValue") or text_or_none(rec.get("TimeDim"))
        return {
            "indicator_code": rec.get("IndicatorCode"),
            "country_code": rec.get("SpatialDim"),
            "spatial_type": rec.get("SpatialDimType"),
            "period": text_or_none(period),
            "value": to_float(rec.get("NumericValue")),
            "value_text": text_or_none(rec.get("Value")),
            "low": to_float(rec.get("Low")),
            "high": to_float(rec.get("High")),
            "comments": rec.get("Comments"),
            "dimensions": dimensions,
            "source_record_id": text_or_none(rec.get("Id")),
            "source_updated": rec.get("Date"),
            "request_id": request_id,
            "source_url": url,
        }

    # -- labels & metadata ------------------------------------------------------

    def dimension_values(self, dimension: str) -> Dict[str, str]:
        if dimension in self._dimension_labels:
            return self._dimension_labels[dimension]
        labels: Dict[str, str] = {}
        if _SAFE_DIMENSION.match(dimension):
            try:
                resp = self.client.get(
                    f"{self.base_url}/DIMENSION/{dimension}/DimensionValues", purpose=f"metadata:dimension:{dimension}"
                )
                for item in (resp.data or {}).get("value", []):
                    if item.get("Code") is not None:
                        labels[str(item["Code"])] = item.get("Title")
            except ApiRequestError as exc:
                logger.warning("could not retrieve labels for WHO dimension %s: %s", dimension, exc)
        self._dimension_labels[dimension] = labels
        return labels

    def _attach_labels(self, observations: List[Dict[str, Any]]) -> None:
        spatial_types = {o["spatial_type"] for o in observations if o.get("spatial_type")}
        dim_types = {d for o in observations for d in o["dimensions"]}
        for spatial in spatial_types:
            labels = self.dimension_values(spatial)
            for o in observations:
                if o.get("spatial_type") == spatial:
                    o["country_name"] = labels.get(o["country_code"])
        for dim in dim_types:
            self.dimension_values(dim)
        for o in observations:
            o["dimension_labels"] = {
                d: self._dimension_labels.get(d, {}).get(str(v)) for d, v in o["dimensions"].items()
            }

    def fetch_indicator_metadata(self, indicator: IndicatorSpec) -> Optional[Dict[str, Any]]:
        try:
            resp = self.client.get(
                f"{self.base_url}/Indicator",
                {"$filter": f"IndicatorCode eq {odata_literal(indicator.code)}"},
                purpose=f"metadata:indicator:{indicator.code}",
            )
        except ApiRequestError as exc:
            logger.warning("could not retrieve WHO metadata for %s: %s", indicator.code, exc)
            return None
        values = (resp.data or {}).get("value") or []
        if not values:
            return {"found": False}
        item = values[0]
        return {
            "found": True,
            "name": item.get("IndicatorName"),
            "language": item.get("Language"),
            "request_id": resp.request_id,
        }
