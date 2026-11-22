"""Registry of approved connectors.

Only connectors listed here can be referenced by a configuration. Adding a
new API means writing a connector module that subclasses
:class:`src.connectors.base.BaseConnector` and registering it below — the
core engine does not change.
"""

from __future__ import annotations

from importlib import import_module
from typing import Dict, Set, Type

_REGISTRY: Dict[str, str] = {
    "generic_rest": "generic_rest:GenericRestConnector",
    "who_gho": "who:WhoGhoConnector",
    "world_bank": "world_bank:WorldBankConnector",
    "eurostat": "eurostat:EurostatConnector",
}


def available_connectors() -> Set[str]:
    return set(_REGISTRY)


def get_connector_class(connector_id: str) -> Type["BaseConnector"]:  # noqa: F821
    try:
        module_name, class_name = _REGISTRY[connector_id].split(":")
    except KeyError:
        raise KeyError(f"unknown connector '{connector_id}'") from None
    module = import_module(f"{__name__}.{module_name}")
    return getattr(module, class_name)
