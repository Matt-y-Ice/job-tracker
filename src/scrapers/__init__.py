"""Job sources. Each module in this package defines one Source subclass.

To add a source: create a new file here with a class that subclasses Source,
sets `key` and `label`, and yields make_job(...) dicts from fetch(). It is picked
up automatically; add an entry under "sources" in config/sources.json to turn
it off or give it settings.
"""
import importlib
import pkgutil

from .base import REGISTRY, Source

_SKIP = {"base", "company_boards", "dedupe", "location", "runner", "__main__"}


def discover() -> dict[str, type[Source]]:
    """Import every source module in this package and return the registry."""
    for module in pkgutil.iter_modules(__path__):
        if module.name not in _SKIP:
            importlib.import_module(f"{__name__}.{module.name}")
    return REGISTRY
