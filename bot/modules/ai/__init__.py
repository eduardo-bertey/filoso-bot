"""Capa de IA: catálogo de proveedores, disponibilidad, claves y servicio."""

from .providers import (
    PROVIDER_SPECS,
    ProviderClient,
    ProviderConfig,
    ProviderError,
    ProviderRegistry,
    ProviderSpec,
    endpoint_for,
    spec_for,
)
from .keys import KeyEntry, KeyManager, probe_key
from .scheduler import Scheduler
from .service import AIService

__all__ = [
    "PROVIDER_SPECS",
    "ProviderClient",
    "ProviderConfig",
    "ProviderError",
    "ProviderRegistry",
    "ProviderSpec",
    "AIService",
    "KeyEntry",
    "KeyManager",
    "Scheduler",
    "endpoint_for",
    "spec_for",
    "probe_key",
]
