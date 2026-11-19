"""Common connector interface.

A connector translates one extraction task + indicator into API requests
(always issued through :class:`src.api_client.ApiClient`, which preserves the
raw responses) and parses the responses into *observations*: plain
dictionaries using the standard field names below. Connectors never write
files and never transform values beyond reading them from the response.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from .. import APP_NAME
from ..api_client import ApiClient
from ..models import ExtractionTask, IndicatorSpec, Source

logger = logging.getLogger(APP_NAME)

#: Fields a connector may populate for each observation.
OBSERVATION_FIELDS = (
    "indicator_code",
    "indicator_name",
    "country_code",
    "country_name",
    "spatial_type",
    "period",
    "value",
    "value_text",
    "low",
    "high",
    "unit",
    "comments",
    "dimensions",
    "dimension_labels",
    "source_record_id",
    "source_updated",
    "request_id",
    "source_url",
)


class ConnectorError(Exception):
    """Raised when a source returns something the connector cannot interpret."""


class BaseConnector(ABC):
    connector_id: ClassVar[str]
    description: ClassVar[str]
    api_types: ClassVar[Tuple[str, ...]]
    #: Filter keys accepted by this connector (None = any key).
    allowed_filter_keys: ClassVar[Optional[Iterable[str]]] = None

    def __init__(self, source: Source, client: ApiClient):
        self.source = source
        self.client = client
        self.base_url = source.base_url.rstrip("/")

    # -- static checks (run during configuration validation) ----------------

    @classmethod
    def check_task(cls, source: Source, task: ExtractionTask) -> List[str]:
        """Return connector-specific configuration errors for a task."""
        errors = []
        if source.api_type not in cls.api_types:
            errors.append(
                f"source '{source.id}': connector '{cls.connector_id}' supports api_type {list(cls.api_types)}, "
                f"not '{source.api_type}'"
            )
        if cls.allowed_filter_keys is not None:
            allowed = set(cls.allowed_filter_keys)
            keys = set(task.filters)
            for ind in task.indicators:
                keys |= set(ind.filters)
            unknown = sorted(keys - allowed)
            if unknown:
                errors.append(
                    f"extraction '{task.id}': filter(s) {unknown} are not supported by connector "
                    f"'{cls.connector_id}' (supported: {sorted(allowed)})"
                )
        return errors

    # -- runtime -------------------------------------------------------------

    @abstractmethod
    def fetch_observations(self, task: ExtractionTask, indicator: IndicatorSpec) -> List[Dict[str, Any]]:
        """Retrieve all observations for one indicator of a task."""

    def fetch_indicator_metadata(self, indicator: IndicatorSpec) -> Optional[Dict[str, Any]]:
        """Official metadata (at least ``name``; optionally ``definition``, ``unit``...). None if unavailable."""
        return None

    def source_metadata(self) -> Dict[str, Any]:
        """Extra facts about the source recorded in the manifest (API version, release date...)."""
        return {}

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def merged_filters(task: ExtractionTask, indicator: IndicatorSpec) -> Dict[str, Any]:
        filters = dict(task.filters)
        filters.update(indicator.filters)
        return filters

    @staticmethod
    def country_batches(task: ExtractionTask) -> List[Optional[List[str]]]:
        if task.countries == "all":
            return [None]
        size = task.country_batch_size
        return [task.countries[i : i + size] for i in range(0, len(task.countries), size)]


def get_path(data: Any, path: Optional[str]) -> Any:
    """Follow a dotted path ('a.b.0.c') through dicts and lists; None when absent."""
    if path is None or path == "":
        return data
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.lstrip("-").isdigit():
            idx = int(part)
            current = current[idx] if -len(current) <= idx < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def to_float(value: Any) -> Optional[float]:
    """Convert values the API already types as numbers. Strings are NOT parsed here."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def text_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def chunked(seq: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]
